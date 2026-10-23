"""Locate exact external CAB carriers from original UnityFS directories.

Only archive directory metadata is decompressed. Texture blocks are untouched.
The consumer must still load and verify the requested CAB and object PPtr.
Layout follows UnityPy BundleFile.read_fs in the bundled runtime.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import io
import os
import struct


def _cstring(stream):
    value = bytearray()
    while len(value) < 4096:
        char = stream.read(1)
        if char == b'\0':
            return value.decode('utf8')
        if not char:
            break
        value.extend(char)
    raise ValueError('Truncated or overlong UnityFS directory string')


def directory_cabs(path):
    path = Path(path)
    with path.open('rb') as stream:
        if _cstring(stream) != 'UnityFS':
            raise ValueError('Unsupported bundle directory signature')
        version = struct.unpack('>I', stream.read(4))[0]
        _cstring(stream)
        _cstring(stream)
        size, compressed, unpacked, flags = struct.unpack('>QIII', stream.read(20))
        if size != path.stat().st_size or version < 7 or flags & ~0x2FF:
            raise ValueError('Unsupported original UnityFS directory header')
        # Directory blocks are small. Reject implausible headers before reading.
        if compressed > size or unpacked > 64 * 1024 * 1024:
            raise ValueError('Unbounded original UnityFS directory size')
        stream.seek((stream.tell() + 15) // 16 * 16)
        if flags & 128:
            stream.seek(size - compressed)
        raw = stream.read(compressed)
    compression = flags & 63
    if compression == 0:
        info = raw
    elif compression in (2, 3):
        import lz4.block
        info = lz4.block.decompress(raw, uncompressed_size=unpacked)
    elif compression == 1:
        from UnityPy.helpers.CompressionHelper import decompress_lzma
        info = decompress_lzma(raw)
    else:
        raise ValueError('Unsupported UnityFS directory compression')
    if len(info) != unpacked:
        raise ValueError('Original UnityFS directory length mismatch')
    reader = io.BytesIO(info)
    reader.seek(16)
    blocks = struct.unpack('>I', reader.read(4))[0]
    if blocks * 10 > len(info):
        raise ValueError('Invalid original UnityFS block count')
    reader.seek(blocks * 10, 1)
    count = struct.unpack('>I', reader.read(4))[0]
    if count * 21 > len(info):
        raise ValueError('Invalid original UnityFS node count')
    names = []
    for _ in range(count):
        reader.read(20)
        name = _cstring(reader).lower()
        if name.startswith('cab-') and not name.endswith(('.ress', '.resource')):
            names.append(name)
    if reader.tell() != len(info):
        raise ValueError('Unparsed original UnityFS directory bytes')
    return names


@lru_cache(maxsize=4)
def _index(source_string):
    source = Path(source_string)
    index, errors = {}, []
    paths = []
    # External CABs can live outside Common (e.g. CardNew/Logo_bundles).
    # Future imported namespaces must not require another hardcoded root list.
    # Read directory metadata only, including medium/thumbnail carriers: a
    # large prefab can refer to any exact original serialized-file carrier.
    for parent, dirs, files in os.walk(source):
        paths.extend(Path(parent) / f for f in files if f.endswith('_bundles') or f == 'UnityMonoScripts')
    for path in sorted(paths):
        try:
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Original CAB carrier leaves the imported source')
            for cab in directory_cabs(path):
                index.setdefault(cab, []).append(path.relative_to(source).as_posix())
        except (OSError, ValueError, EOFError, struct.error) as error:
            errors.append((str(path.relative_to(source)), str(error)))
    return index, errors


def find_cab(source, cab):
    """Return an exact CAB's carrier; conflicting duplicate bytes are rejected."""
    source = Path(source).resolve()
    index, errors = _index(str(source))
    candidates = index.get(cab.lower(), [])
    if not candidates:
        raise ValueError('Original CAB carrier not found: %s; %d unindexed bundle headers' % (cab, len(errors)))
    if len(candidates) > 1:
        hashes = {hashlib.sha256((source / p).read_bytes()).hexdigest() for p in candidates}
        if len(hashes) != 1:
            raise ValueError('Conflicting original CAB carriers: ' + cab)
    return candidates[0]
