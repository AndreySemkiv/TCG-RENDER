"""Texture defaults recovered from the selected Unity player's native code.

The shipped Unity black texture has four zero bytes. The ShaderLab manual's
alpha-one value differs from this player; retaining alpha one covers optional
empty art layers with opaque black. Native evidence is retained in the B4b
review report. This is a built-in texture, not a replacement for game artwork.
"""
from functools import lru_cache
from pathlib import Path
import hashlib


UNITY_SHA256 = 'e6f33ccdad2fe323785c673ef8a5b6850f4a12100fa04c284c3f0264980b5bd9'


@lru_cache(maxsize=8)
def _validate_player(path, size, modified_ns):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != UNITY_SHA256:
        raise ValueError('Original engine texture defaults require the inspected Unity player')


def default_texture_color(root, name):
    colors = {'white': (255,255,255,255), 'gray': (128,128,128,255),
              'bump': (128,128,255,255), 'red': (255,0,0,255)}
    if name == 'black':
        path = Path(root)/'input/native-code/libunity.so'
        info = path.stat()
        _validate_player(str(path.resolve()), info.st_size, info.st_mtime_ns)
        return (0,0,0,0)
    return colors[name]
