"""Discover audited Unity ARM64 behavior by code shape, constants and imports.

Whole-player hashes change with unrelated engine changes. These contracts match
only the code the renderer reconstructs, preserving branch structure and data
constants while allowing relocated code, read-only data and PLT addresses.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import struct

from .config import CODE_ROOT


def _signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class _Player:
    def __init__(self, data):
        self.data = data
        if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
            raise ValueError('Unity contracts require a little-endian ARM64 ELF player')
        offset = struct.unpack_from('<Q', data, 40)[0]
        size, count, names = struct.unpack_from('<HHH', data, 58)
        self.sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size) for i in range(count)]
        strings = self.sections[names]
        table = data[strings[4]:strings[4] + strings[5]]
        self.named = {table[s[0]:table.find(b'\0', s[0])].decode(): s for s in self.sections}
        self.executable = [s for s in self.sections if s[1] != 8 and s[2] & 4]
        self.relocations = {}
        symbols = self.named.get('.dynsym')
        rela = self.named.get('.rela.plt')
        if symbols and rela:
            strings = self.sections[symbols[6]]
            table = data[strings[4]:strings[4] + strings[5]]
            for pos in range(rela[4], rela[4] + rela[5], rela[9]):
                address, info, _ = struct.unpack_from('<QQq', data, pos)
                name = struct.unpack_from('<I', data, symbols[4] + (info >> 32) * symbols[9])[0]
                self.relocations[address] = table[name:table.find(b'\0', name)].decode()

    def offset(self, address, length=1):
        for section in self.sections:
            if section[1] != 8 and section[3] <= address and address + length <= section[3] + section[5]:
                return section[4] + address - section[3]
        raise ValueError('Unity native contract references bytes outside a file-backed section')

    def read(self, address, length):
        offset = self.offset(address, length)
        return self.data[offset:offset + length]

    def address(self, offset):
        for section in self.executable:
            if section[4] <= offset < section[4] + section[5]:
                return section[3] + offset - section[4]
        return None

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def adr(self, address, word):
        immediate = _signed(((word >> 5) & 0x7ffff) * 4 + ((word >> 29) & 3), 21)
        return ((address & ~4095) + (immediate << 12)) if word & 0x80000000 else address + immediate

    def imported_call(self, address):
        try:
            first, second = self.word(address), self.word(address + 4)
        except ValueError:
            return None
        if first & 0x9f000000 != 0x90000000 or second & 0xffc00000 != 0xf9400000:
            return None
        got = self.adr(address, first) + ((second >> 10) & 4095) * 8
        return self.relocations.get(got)


def _shape(player, address, length, references=()):
    """Fingerprint instructions, referenced SIMD constants and imported calls."""
    raw = player.read(address, length)
    result, registers, constants, imports = bytearray(), {}, [], []
    for offset in range(0, length, 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        normalized, pc = word, address + offset
        if word & 0x9f000000 in (0x90000000, 0x10000000):
            registers[word & 31] = player.adr(pc, word)
            normalized &= ~0x60ffffe0
        elif word & 0xff000000 == 0x91000000 and (word >> 5) & 31 in registers:
            register = (word >> 5) & 31
            registers[word & 31] = registers[register] + ((word >> 10) & 4095) * (4096 if word & (1 << 22) else 1)
            normalized &= ~0x003ffc00
        elif word & 0xffc00000 in (0x3dc00000, 0xfd400000) and (word >> 5) & 31 in registers:
            size = 16 if word & 0xffc00000 == 0x3dc00000 else 8
            normalized &= ~0x003ffc00
        elif word & 0x7c000000 == 0x14000000:
            target = pc + _signed(word & 0x3ffffff, 26) * 4
            if not address <= target < address + length:
                normalized &= ~0x03ffffff
                imported = player.imported_call(target)
                if imported:
                    imports.append({'offset': offset, 'name': imported})
        result.extend(struct.pack('<I', normalized))
    # Explicit reaching definitions come from the audited control-flow paths.
    # Linear instruction order is insufficient when two branches load different
    # pages into the same register before their separate vector loads.
    for reference in references:
        load_offset, page_offset = reference['load'], reference['page']
        page, load = player.word(address + page_offset), player.word(address + load_offset)
        if page & 0x9f000000 != 0x90000000 or (page & 31) != ((load >> 5) & 31):
            raise ValueError('Unity constant reference no longer uses its audited page register')
        size = {0x3dc00000: 16, 0xfd400000: 8}.get(load & 0xffc00000)
        if size is None:
            raise ValueError('Unity constant load instruction changed')
        target = player.adr(address + page_offset, page) + ((load >> 10) & 4095) * size
        constants.append({'offset': load_offset, 'bytes': player.read(target, size).hex()})
    return {'code_sha256': hashlib.sha256(result).hexdigest(), 'constants': constants, 'imports': imports}


def _match(player, spec):
    matches = []
    anchor = bytes.fromhex(spec['anchor'])
    for section in player.executable:
        cursor, stop = section[4], section[4] + section[5]
        while True:
            cursor = player.data.find(anchor, cursor, stop)
            if cursor < 0:
                break
            candidate = cursor - spec.get('anchor_offset', 0)
            address = player.address(candidate)
            cursor += 4
            if address is None or address % 4:
                continue
            try:
                shape = _shape(player, address, spec['length'], spec.get('references', ()))
            except (ValueError, struct.error):
                continue
            if shape == spec['shape']:
                if spec['name'] == 'black_registration':
                    page, add = player.word(address + 8), player.word(address + 12)
                    target = player.adr(address + 8, page) + ((add >> 10) & 4095)
                    if player.read(target, 11) != b'UnityBlack\0':
                        continue
                matches.append(address)
    if len(matches) != 1:
        raise ValueError('Original Unity native contract changed or is ambiguous: ' + spec['name'])
    return matches[0]


@lru_cache(maxsize=8)
def _validate_player(path, size, modified_ns):
    data = Path(path).read_bytes()
    player = _Player(data)
    contract = json.loads((CODE_ROOT/'profiles/unity-native-contract-v1.json').read_text(encoding='utf-8'))
    addresses = {}
    for spec in contract['functions']:
        if spec['name'] in ('create_builtin_texture', 'pixel_fill_loop'):
            caller, offset = (('black_registration', 32) if spec['name'] == 'create_builtin_texture'
                              else ('create_builtin_texture', 156))
            pc = addresses[caller] + offset
            call = player.word(pc)
            address = pc + _signed(call & 0x3ffffff, 26) * 4
            if (call & 0xfc000000 != 0x94000000
                    or _shape(player, address, spec['length'], spec.get('references', ())) != spec['shape']):
                raise ValueError('Original Unity native helper contract changed: ' + spec['name'])
            addresses[spec['name']] = address
        else:
            addresses[spec['name']] = _match(player, spec)
    # The initializer must pair UnityBlack itself with the zero-fill callback.
    registration = addresses['black_registration']
    adr = player.word(registration + 4)
    page, add = player.word(registration + 8), player.word(registration + 12)
    if (adr & 0x9f00001f != 0x10000008 or page & 0x9f00001f != 0x90000000
            or add & 0xffc003ff != 0x91000000):
        raise ValueError('Original Unity black texture registration shape changed')
    name = player.adr(registration + 8, page) + ((add >> 10) & 4095)
    callback = player.adr(registration + 4, adr)
    if player.read(name, 11) != b'UnityBlack\0' or player.read(callback, 8) != bytes.fromhex('3f0000b9c0035fd6'):
        raise ValueError('Original Unity black texture no longer uses zero RGBA pixels')
    create_call = player.word(registration + 32)
    create = registration + 32 + _signed(create_call & 0x3ffffff, 26) * 4
    if create_call & 0xfc000000 != 0x94000000 or create != addresses['create_builtin_texture']:
        raise ValueError('Original Unity black texture initializer targets a changed helper')
    create = addresses['create_builtin_texture']
    fill_call = player.word(create + 156)
    fill = create + 156 + _signed(fill_call & 0x3ffffff, 26) * 4
    if fill_call & 0xfc000000 != 0x94000000 or fill != addresses['pixel_fill_loop']:
        raise ValueError('Original Unity builtin texture helper targets a changed fill loop')
    return {'authority': 'selected-player-instruction-contracts', 'player_sha256': hashlib.sha256(data).hexdigest(),
            'contract': contract['id'], 'functions': {key: hex(value) for key, value in addresses.items()},
            'black_pixel_rgba': [0, 0, 0, 0]}


def validate_engine_contract(root):
    path = Path(root)/'input/native-code/libunity.so'
    info = path.stat()
    return _validate_player(str(path.resolve()), info.st_size, info.st_mtime_ns)
