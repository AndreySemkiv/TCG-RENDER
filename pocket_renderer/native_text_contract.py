"""Resolve inspected ARM64 contracts by current IL2CPP names and relocations.

Only copied files are read. No target code is executed, and no offsets from an
unrelated binary are used to resolve a method or a string literal.
"""
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
import hashlib
import struct


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class NativeSource:
    def __init__(self, root):
        root = Path(root)
        self.binary = (root/'input/native-code/libil2cpp.so').read_bytes()
        self.metadata = (root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat').read_bytes()
        b, md = self.binary, self.metadata
        if b[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', b, 18)[0] != 183:
            raise ValueError('Native contracts require a little-endian ARM64 ELF.')
        if struct.unpack_from('<II', md) != (0xfab11baf, 31):
            raise ValueError('Native contracts require valid IL2CPP metadata version 31.')
        values = struct.unpack_from('<64I', md)
        self.sections = list(zip(values[2::2], values[3::2]))
        if not all(offset + size <= len(md) for offset, size in self.sections):
            raise ValueError('Invalid IL2CPP metadata table bounds.')
        ph = struct.unpack_from('<Q', b, 32)[0]
        size, count = struct.unpack_from('<HH', b, 54)
        self.segments = [struct.unpack_from('<IIQQQQQQ', b, ph+i*size) for i in range(count)]
        sh = struct.unpack_from('<Q', b, 40)[0]
        size, count = struct.unpack_from('<HH', b, 58)
        self.relocations = {}
        for i in range(count):
            section = struct.unpack_from('<IIQQQQIIQQ', b, sh+i*size)
            if section[1] != 4:
                continue
            if section[9] != 24 or section[4] + section[5] > len(b):
                raise ValueError('Invalid native relocation table.')
            for p in range(section[4], section[4]+section[5], 24):
                address, kind, value = struct.unpack_from('<QQq', b, p)
                if kind & 0xffffffff == 1027:
                    self.relocations[address] = value
        self.types = {}
        start, length = self.sections[19]
        if length % 88:
            raise ValueError('Unsupported IL2CPP type definition layout.')
        for index, p in enumerate(range(start, start+length, 88)):
            row = struct.unpack_from('<16i8H2I', md, p)
            key = self.text(row[1]), self.text(row[0])
            self.types.setdefault(key, []).append((index, row))
        start, length = self.sections[20]
        self.images = [struct.unpack_from('<10i', md, p) for p in range(start, start+length, 40)]
        self.modules = {}
        self.provenance = {'native_sha256': hashlib.sha256(b).hexdigest(),
                           'metadata_sha256': hashlib.sha256(md).hexdigest(),
                           'resolution': 'current-source IL2CPP31 names, tokens and ELF relocations'}

    def offset(self, address, length=1):
        for segment in self.segments:
            if segment[0] == 1 and segment[3] <= address and address+length <= segment[3]+segment[5]:
                return segment[2]+address-segment[3]
        raise ValueError('Native contract address is outside file-backed ELF segments.')

    def qword(self, address):
        if address in self.relocations:
            return self.relocations[address]
        return struct.unpack_from('<Q', self.binary, self.offset(address, 8))[0]

    def text(self, index):
        start, size = self.sections[2]
        if index < 0 or index >= size:
            raise ValueError('Invalid IL2CPP name index.')
        end = self.metadata.find(b'\0', start+index, start+size)
        if end < 0:
            raise ValueError('Unterminated IL2CPP name.')
        return self.metadata[start+index:end].decode('utf-8')

    def type_row(self, namespace, name):
        rows = self.types.get((namespace, name), [])
        if len(rows) != 1:
            raise ValueError('Native contract type is missing or ambiguous: '+namespace+'.'+name)
        return rows[0]

    def module(self, type_index):
        image = next((row for row in self.images if row[2] <= type_index < row[2]+row[3]), None)
        if image is None:
            raise ValueError('Native contract type has no assembly image.')
        name = self.text(image[0])
        if name not in self.modules:
            string = name.encode()+b'\0'
            offsets, p = [], 0
            while (p := self.binary.find(string, p)) >= 0:
                offsets.append(p)
                p += len(string)
            addresses = set()
            for offset in offsets:
                for segment in self.segments:
                    if segment[0] == 1 and segment[2] <= offset < segment[2]+segment[5]:
                        addresses.add(segment[3]+offset-segment[2])
            candidates = []
            for address, target in self.relocations.items():
                if target not in addresses:
                    continue
                try:
                    count = struct.unpack_from('<I', self.binary, self.offset(address+8, 4))[0]
                    array = self.qword(address+16)
                    if 0 < count < 1000000:
                        self.offset(array, count*8)
                        pointers = [self.qword(array+i*8) for i in range(count)]
                        candidates.append(pointers)
                except (ValueError, struct.error):
                    continue
            if len(candidates) != 1:
                raise ValueError('Native assembly module is missing or ambiguous: '+name)
            self.modules[name] = {'pointers': candidates[0], 'bounds': sorted(set(candidates[0])-{0})}
        return self.modules[name]

    def methods(self, namespace, name):
        index, row = self.type_row(namespace, name)
        pointers = self.module(index)['pointers']
        result = []
        start = self.sections[5][0]
        for i in range(row[9], row[9]+row[16]):
            method = struct.unpack_from('<7i4H', self.metadata, start+i*36)
            rid = method[6] & 0xffffff
            if rid <= 0 or rid > len(pointers):
                raise ValueError('Native method token is outside its module.')
            result.append({'name': self.text(method[0]), 'address': pointers[rid-1],
                           'parameter_count': method[10], 'index': i})
        return result

    def method(self, namespace, name, method_name):
        methods = [m for m in self.methods(namespace, name) if m['name'] == method_name]
        if len(methods) != 1 or not methods[0]['address']:
            raise ValueError('Native method is missing or ambiguous: '+name+'.'+method_name)
        return methods[0]['address']

    def method_bytes(self, namespace, name, method_name):
        index, _ = self.type_row(namespace, name)
        module = self.module(index)
        address = self.method(namespace, name, method_name)
        n = bisect_right(module['bounds'], address)
        if n == len(module['bounds']):
            raise ValueError('Native method end is unavailable.')
        length = module['bounds'][n]-address
        if not 0 < length < 1024*1024 or length % 4:
            raise ValueError('Native method extent is unsupported.')
        start = self.offset(address, length)
        return self.binary[start:start+length]

    def literal(self, address):
        value = self.qword(address)
        encoded = value if value >> 29 == 5 else self.qword(value)
        if encoded >> 29 != 5:
            raise ValueError('Native string slot is not a metadata string literal.')
        index = (encoded & 0x1fffffff) >> 1
        start, size = self.sections[0]
        if index >= size//8:
            raise ValueError('Native string literal index is invalid.')
        length, offset = struct.unpack_from('<II', self.metadata, start+index*8)
        text_start, text_size = self.sections[1]
        if offset+length > text_size:
            raise ValueError('Native string literal bounds are invalid.')
        return self.metadata[text_start+offset:text_start+offset+length].decode('utf-8')

    def references(self, address, length):
        """Bounded straight-line ADRP+ADD/LDR references; no target execution."""
        start = self.offset(address, length)
        registers, result = {}, []
        for i, (word,) in enumerate(struct.iter_unpack('<I', self.binary[start:start+length])):
            pc = address+i*4
            if word & 0x9f000000 == 0x90000000:
                immediate = signed(((word >> 5) & 0x7ffff)*4+((word >> 29) & 3), 21) << 12
                registers[word & 31] = ((pc & ~4095)+immediate, i)
            elif word & 0xffc00000 in (0xf9400000, 0xb9400000, 0x39400000,
                                       0xf9000000, 0xb9000000, 0x39000000,
                                       0xfd400000, 0xbd400000, 0x3dc00000):
                source, dest = (word >> 5) & 31, word & 31
                if source in registers and (source >= 19 or i-registers[source][1] <= 12):
                    scale = {0xf9: 8, 0xb9: 4, 0x39: 1, 0xfd: 8, 0xbd: 4, 0x3d: 16}[word >> 24]
                    result.append({'pc': pc, 'target': registers[source][0]+((word >> 10) & 4095)*scale,
                                   'register': dest, 'kind': 'load' if word & (1 << 22) else 'store',
                                   'width': scale, 'word': word})
                if word >> 24 not in (0xfd, 0xbd, 0x3d) and word & (1 << 22):
                    registers.pop(dest, None)
            elif word & 0xff000000 == 0x91000000:
                source, dest = (word >> 5) & 31, word & 31
                if source in registers and (source >= 19 or i-registers[source][1] <= 12):
                    target = registers[source][0]+((word >> 10) & 4095)*(4096 if word & (1 << 22) else 1)
                    result.append({'pc': pc, 'target': target, 'register': dest, 'kind': 'address', 'word': word})
                    registers[dest] = target, i
                else:
                    registers.pop(dest, None)
            elif word & 0xffffffe0 == 0xd65f03c0:
                registers.clear()
            elif word & 0xfc000000 == 0x94000000:
                # AAPCS64 preserves x19..x29; call results/clobbers are unknown.
                for register in range(19):
                    registers.pop(register,None)
            elif word & 0xffe0ffe0 == 0xaa0003e0:
                dest, origin = word & 31, (word >> 16) & 31
                if origin in registers:
                    registers[dest] = registers[origin]
                else:
                    registers.pop(dest,None)
            elif word & 0x7f800000 in (0x52800000,0x72800000,0x12800000):
                registers.pop(word & 31,None)
        return result

    def method_references(self, namespace, name, method_name):
        data = self.method_bytes(namespace, name, method_name)
        return self.references(self.method(namespace, name, method_name), len(data))

    def normalized_method(self, namespace, name, method_name):
        """Fingerprint code layout/constants while ignoring relocated addresses.

        This is a structural comparison, not a proof of indirect-call behavior.
        Callers must separately validate resource/literal inputs they consume.
        """
        data = self.method_bytes(namespace, name, method_name)
        address = self.method(namespace, name, method_name)
        references = {ref['pc']: ref for ref in self.references(address, len(data))}
        words = []
        for i, (word,) in enumerate(struct.iter_unpack('<I', data)):
            pc = address+i*4
            if word & 0x9f000000 in (0x90000000, 0x10000000):
                word &= ~((0x7ffff << 5) | (3 << 29))
            elif word & 0xfc000000 in (0x14000000, 0x94000000):
                word &= ~0x3ffffff
            elif pc in references:
                word &= ~(0xfff << 10)
            elif word & 0x3b000000 == 0x18000000:
                word &= ~(0x7ffff << 5)
            words.append(word)
        return hashlib.sha256(struct.pack('<%dI' % len(words), *words)).hexdigest()


@lru_cache(maxsize=2)
def source(root):
    return NativeSource(Path(root).resolve())
