"""Texture defaults recovered from the selected Unity player's native code.

The shipped Unity black texture has four zero bytes. The ShaderLab manual's
alpha-one value differs from this player; retaining alpha one covers optional
empty art layers with opaque black. Native evidence is retained in the B4b
review report. This is a built-in texture, not a replacement for game artwork.
"""
from .unity_contracts import validate_engine_contract


def default_texture_color(root, name):
    colors = {'white': (255,255,255,255), 'gray': (128,128,128,255),
              'bump': (128,128,255,255), 'red': (255,0,0,255)}
    if name == 'black':
        contract = validate_engine_contract(root)
        return tuple(contract['black_pixel_rgba'])
    return colors[name]
