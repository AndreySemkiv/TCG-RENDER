"""Decode the copied original KVRF files without executing the game.

Format reference: https://github.com/SombrAbsol/kvrom2json/blob/main/docs/kvrom.md
Keeps every entry and category; does not expand dynamic game text tags.
"""
from pathlib import Path
import hashlib
import json
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2] / 'pocket-extraction'
SOURCE = ROOT / 'reports/file-only-analysis/locale-original'

def parse(path):
    data = path.read_bytes()
    if data[:4] != b'KVRF' or len(data) < 48:
        raise ValueError('Not an admitted KVRF file')
    size, version = struct.unpack_from('<II', data, 4)
    count, buckets = struct.unpack_from('<ii', data, 16)
    table = struct.unpack_from('<I', data, 32)[0]
    if size != len(data) or not (0 <= table <= len(data)-4*buckets):
        raise ValueError('Invalid KVRF bounds')
    visited = set()
    entries = []
    for bucket in range(buckets):
        address = struct.unpack_from('<I', data, table + bucket*4)[0]
        chain = set()
        while address:
            if address in chain or address in visited:
                raise ValueError('Repeated/cyclic entry address')
            chain.add(address)
            visited.add(address)
            if address > len(data)-16:
                raise ValueError('Entry outside file')
            next_address, key_hash, packed, compression, reserved, payload_size = struct.unpack_from('<IiHBBi', data, address)
            if payload_size < 0 or address+16+payload_size > len(data):
                raise ValueError('Payload outside file')
            payload = data[address+16:address+16+payload_size]
            if compression == 1:
                expected = struct.unpack_from('<I', payload)[0]
                payload = zlib.decompress(payload[4:], -15)
                if len(payload) != expected:
                    raise ValueError('Inflated size mismatch')
            elif compression != 0:
                raise ValueError('Unsupported compression')
            key_size = struct.unpack_from('<H', payload)[0]
            if key_size > len(payload)-2:
                raise ValueError('Invalid key length')
            raw_key = payload[2:2+key_size]
            kind = packed & 7
            if kind in (0, 1):
                key = raw_key.decode('utf-16-le')
            elif kind == 2:
                if len(raw_key) != 8:
                    raise ValueError('Invalid integer key width')
                key = f'0x{int.from_bytes(raw_key,"little"):X}'
            else:
                key = 'unknown' + raw_key.hex().upper()
            value = payload[2+key_size:].decode('utf-16-le')
            entries.append({'key': key, 'category': packed >> 3, 'key_type': kind, 'value': value, 'entry_offset': address})
            address = next_address
    if len(entries) != count:
        raise ValueError(f'Entry count mismatch: {len(entries)} vs {count}')
    return {'source': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'version': version, 'entry_count': count, 'entries': entries}

def main():
    summary = []
    matches = []
    for path in sorted(SOURCE.glob('*/*.kvrom')):
        result = parse(path)
        target = path.with_suffix('.json')
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        summary.append({k: v for k, v in result.items() if k != 'entries'})
        if path.stem == 'Master':
            for entry in result['entries']:
                if any(needle in entry['value'].casefold() for needle in ['bulbasaur', 'vine whip', 'chicote de vinha', 'there is a plant seed', 'este pokémon já nasce']):
                    matches.append({'language': path.parent.name, 'source': result['source'], **entry})
    (SOURCE / 'decode-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    (SOURCE / 'bulbasaur-text-matches.json').write_text(json.dumps(matches, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'files': len(summary), 'entries': sum(r['entry_count'] for r in summary), 'per_file': [{'source':r['source'],'entries':r['entry_count']} for r in summary], 'bulbasaur_text_matches': matches[:12]}, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
