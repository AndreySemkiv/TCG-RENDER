"""Discover the supported ARM64 metadata loader from local bytes, not versions.

Recognition is structural and bounded. A changed/unknown loader is rejected;
matching parameters still have to pass metadata and master-data integrity checks.
"""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import struct

from .bluestacks import SourceError


MAGIC = 0xfab11baf


def metadata_sections(data, total_size=None):
    """Validate the IL2CPP31 tables consumed by this application's readers."""
    if len(data) < 256 or struct.unpack_from('<II', data) != (MAGIC, 31):
        raise SourceError('The decoded metadata is not a supported IL2CPP 31 file.')
    values = struct.unpack_from('<64I', data)
    pairs = list(zip(values[2::2], values[3::2]))
    total = len(data) if total_size is None else total_size
    if any(offset + size > total or (size and offset < 256) for offset, size in pairs):
        raise SourceError('The decoded metadata has invalid table bounds.')
    if (not pairs[2][1] or not pairs[5][1] or not pairs[19][1]
            or pairs[5][1] % 36 or pairs[11][1] % 12 or pairs[19][1] % 88):
        raise SourceError('The decoded metadata table layout is not supported.')
    return pairs


def _refs(elf, start, count=192):
    """Local ADRP/ADD and ADR data references in a bounded loader body."""
    out, regs = [], {}
    off = elf.off(start)
    if off is None:
        return out
    def signed(value, bits):
        return value - (1 << bits) if value & (1 << (bits-1)) else value
    for index in range(count):
        word = struct.unpack_from('<I', elf.data, off+index*4)[0]
        pc = start + index*4
        if word == 0xd65f03c0:
            break
        if word & 0x9f000000 == 0x90000000:
            immediate = signed(((word >> 5) & 0x7ffff)*4 + ((word >> 29) & 3), 21) << 12
            regs[word & 31] = ((pc & ~4095) + immediate, index)
        elif word & 0xff000000 == 0x91000000:
            source, target = (word >> 5) & 31, word & 31
            if source in regs and index-regs[source][1] <= 8:
                value = regs[source][0] + ((word >> 10) & 4095)*(4096 if word & (1 << 22) else 1)
                out.append(value)
            regs.pop(target, None)
        elif word & 0x9f000000 == 0x10000000:
            out.append(pc + signed(((word >> 5) & 0x7ffff)*4 + ((word >> 29) & 3), 21))
        elif word & 0xfc000000 == 0x94000000:
            for register in range(19):
                regs.pop(register, None)
    return out


def _function_start(elf, pc):
    # This loader is a normal ARM64 function immediately following a RET. Its
    # body is confirmed by data references, a caller constant, and decryption.
    for address in range(pc-4, max(0, pc-512), -4):
        offset = elf.off(address)
        if offset is None:
            break
        if struct.unpack_from('<I', elf.data, offset)[0] == 0xd65f03c0:
            return address+4
    return None


def _call_seeds(elf, targets):
    """Recover literal x1 arguments at direct calls to candidate loaders."""
    result = {target: set() for target in targets}
    for section in elf.sections:
        if not section[2] & 4 or section[1] == 8:
            continue
        words = memoryview(elf.data)[section[4]:section[4]+section[5]].cast('I')
        for index, word in enumerate(words):
            if word & 0xfc000000 != 0x94000000:
                continue
            displacement = word & 0x3ffffff
            if displacement & (1 << 25):
                displacement -= 1 << 26
            target = section[3]+index*4+displacement*4
            if target not in targets:
                continue
            value, mask = None, 0
            for instruction in words[max(0, index-10):index]:
                kind = instruction & 0xff80001f
                shift = ((instruction >> 21) & 3)*16
                immediate = (instruction >> 5) & 65535
                if kind == 0xd2800001:  # MOVZ x1
                    value, mask = immediate << shift, 15
                elif kind == 0xf2800001 and value is not None:  # MOVK x1
                    value = (value & ~(65535 << shift)) | (immediate << shift)
                elif instruction & 0xfc000000 == 0x94000000:
                    value, mask = None, 0
                elif instruction & 31 == 1 and kind not in (0xd2800001, 0xf2800001):
                    value, mask = None, 0
            if value is not None and mask:
                result[target].add(value)
    return result


def discover_loader(elf, protected, aes):
    """Recognize the embedded-prefix AES-CTR loader and its caller's XOR mask."""
    if (len(elf.data) < 64 or elf.data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', elf.data, 18)[0] != 183):
        raise SourceError('Automatic native metadata detection requires a little-endian ARM64 ELF file.')
    if protected[:8] == struct.pack('<II', MAGIC, 31):
        metadata_sections(protected)
        return {'id': 'automatic-plaintext-metadata31', 'metadata_plaintext': True}
    candidates = []
    for section in elf.sections:
        if not section[2] & 2 or section[2] & 4 or section[1] == 8:
            continue
        begin, end = section[4], section[4]+section[5]
        # Supported loader embeds a power-of-two header. Sizes are recognized
        # from the record, never from a game-version label or absolute address.
        for size in (4096, 8192, 16384, 32768, 65536):
            position = begin
            needle = struct.pack('<I', size)
            while True:
                position = elf.data.find(needle, position, end)
                if position < 0:
                    break
                prefix = position+4
                if position % 4 == 0 and prefix+16+size <= end:
                    sample = elf.data[prefix+16:prefix+16+256]
                    entropy = -sum(n/256*math.log2(n/256) for n in Counter(sample).values())
                    if entropy > 7:
                        candidates.append((elf.va(position), elf.va(prefix), size))
                position += 4
    if len(candidates) > 128:
        raise SourceError('Ambiguous native metadata prefix layout.')
    references = elf.xrefs([address for size_address, prefix, size in candidates for address in (size_address, prefix)])
    functions = {}
    for ref in references:
        start = _function_start(elf, ref['pc'])
        if start is not None:
            functions.setdefault(start, set()).add(ref['target'])
    seeds = _call_seeds(elf, functions)
    matches = {}
    for function, addresses in functions.items():
        for size_address, prefix, size in candidates:
            if size_address not in addresses or prefix not in addresses:
                continue
            off = elf.off(prefix)
            for key_address in _refs(elf, function):
                key_offset = elf.off(key_address)
                if key_offset is None or abs(key_address-prefix) > size+4096:
                    continue
                raw = elf.data[key_offset:key_offset+16]
                if len(raw) != 16:
                    continue
                for seed in seeds[function]:
                    mask = struct.pack('<Q', seed)
                    key = bytes(value ^ mask[i % 8] for i, value in enumerate(raw))
                    stream = aes(key, b''.join(b'\0'*8+i.to_bytes(8, 'big') for i in range(1, 17)))
                    header = bytes(a ^ b for a, b in zip(elf.data[off+16:off+16+256], stream))
                    try:
                        metadata_sections(header, size+len(protected)-4)
                    except SourceError:
                        continue
                    identity = (size_address, prefix, key_address, seed)
                    matches[identity] = {'id': 'automatic-arm64-aes-metadata31',
                        'prefix_size_va': size_address, 'prefix_va': prefix,
                        'key_va': key_address, 'key_seed': seed,
                        'recognition': 'ARM64 data references + direct caller constant + valid decrypted metadata tables'}
    if len(matches) != 1:
        raise SourceError('Could not uniquely recognize this native metadata loader. No version-specific offsets were applied.')
    return next(iter(matches.values()))


def unity_version(root):
    """Read the source Unity version from the selected Player's serialized data."""
    root = Path(root)/'input/unity-data'
    for name in ('globalgamemanagers', 'data.unity3d'):
        path = root/name
        if path.is_file():
            with path.open('rb') as source:
                match = re.search(rb'\b(?:20[0-9]{2}|[5-9][0-9]{3})\.[0-9]+\.[0-9]+[abfp][0-9]+\b', source.read(65536))
            if match:
                return match.group().decode('ascii')
    raise SourceError('The selected APK does not declare a recognizable Unity version.')


def discover_master_key(metadata, entry, encrypted, decoder):
    """Find hash-named 32-byte static fields and verify the source master hash."""
    pairs = metadata_sections(metadata)
    defaults, size = pairs[7]
    fields, field_size = pairs[11]
    strings, string_size = pairs[2]
    payload, payload_size = pairs[8]
    candidates, found = set(), []
    for position in range(defaults, defaults+size, 12):
        field, _, index = struct.unpack_from('<3i', metadata, position)
        if field < 0 or field*12+12 > field_size or index < 0 or index+32 > payload_size:
            continue
        name_index = struct.unpack_from('<i', metadata, fields+field*12)[0]
        if not 0 <= name_index < string_size:
            continue
        name = metadata[strings+name_index:strings+name_index+64]
        if not re.fullmatch(b'[A-Fa-f0-9]{64}', name):
            continue
        key = metadata[payload+index:payload+index+32]
        if hashlib.sha256(key).hexdigest().encode() == name.lower():
            candidates.add(payload+index)
    iv = int(entry['content_hash'], 16)
    for offset in sorted(candidates):
        key = metadata[offset:offset+32]
        probe, _ = decoder.chacha_original(encrypted[:128], key, iv)
        # MasterMemory begins with a MessagePack map of table names and ranges.
        if not probe or probe[0] not in (0xde, 0xdf) and probe[0] & 0xf0 != 0x80:
            continue
        skip = 3 if probe[0] == 0xde else 5 if probe[0] == 0xdf else 1
        try:
            parser = decoder.MessagePack(probe, skip)
            first_key = parser.val()
            if not isinstance(first_key, str) or not first_key.isidentifier():
                continue
        except (ValueError, UnicodeError, struct.error, IndexError):
            continue
        decoded, _ = decoder.chacha_original(encrypted, key, iv)
        if len(decoded) == entry['content_size'] and decoder.xxh64(decoded) == iv:
            found.append(offset)
    if len(found) != 1:
        raise SourceError('Could not uniquely recover a source master-data key with a verified content hash.')
    return found[0]
