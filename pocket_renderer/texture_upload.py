"""Exact upload bytes for original PNG assets and runtime R16 textures.

R16 inputs are already quantized by the recovered original engine conversion.
This module never converts their component values, resizes them, or routes them
through an eight-bit image decoder. Row reversal, if explicitly requested, is
only a byte-preserving upload-memory transformation.
"""
from pathlib import Path
import sys
from PIL import Image


def _dimensions(value):
    if (not isinstance(value,(list,tuple)) or len(value)!=2
            or any(isinstance(v,bool) or not isinstance(v,int) or v<=0 for v in value)):
        raise ValueError('Texture image_size must contain two positive integers')
    return tuple(value)


def texture_upload_data(path,image_format=None,image_size=None,row_origin=None):
    """Return exact GPU bytes; unspecified format preserves the PNG RGBA8 path."""
    path=Path(path)
    image_format=image_format or 'rgba8-unorm'
    if image_format=='r16-unorm':
        width,height=_dimensions(image_size)
        if row_origin not in ('bottom-left','top-left'):
            raise ValueError('Raw R16 texture row_origin must be explicit')
        if sys.byteorder!='little':
            raise ValueError('Raw R16 upload currently requires a little-endian host')
        if path.stat().st_size!=width*height*2:
            raise ValueError('Raw R16 byte count does not match its declared image_size')
        pixels=path.read_bytes()
        if row_origin=='top-left':
            stride=width*2
            pixels=b''.join(pixels[row*stride:(row+1)*stride] for row in range(height-1,-1,-1))
        return {'width':width,'height':height,'format':image_format,'pixels':pixels,
                'source_row_origin':row_origin,'gpu_row_origin':'bottom-left','component_bits':16,
                'conversion':'none; exact little-endian R16 UNORM components',
                'rows_flipped_in_upload_memory':row_origin=='top-left'}
    if image_format!='rgba8-unorm':
        raise ValueError('Unsupported explicit texture image_format: '+str(image_format))
    if row_origin not in (None,'top-left','bottom-left'):
        raise ValueError('Unknown PNG texture row_origin')
    with Image.open(path) as original:
        image=original.convert('RGBA')
        if image_size is not None and image.size!=_dimensions(image_size):
            raise ValueError('PNG dimensions differ from the declared image_size')
        source_origin=row_origin or 'top-left'
        if source_origin=='top-left':image=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return {'width':image.width,'height':image.height,'format':image_format,'pixels':image.tobytes(),
                'source_row_origin':source_origin,'gpu_row_origin':'bottom-left','component_bits':8,
                'conversion':'original PNG decode to RGBA8; no resize',
                'rows_flipped_in_upload_memory':source_origin=='top-left'}
