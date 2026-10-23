"""Original damage type symbols and RectTransform scaling, native 0x47F8108.

Jump table at 0x1E4A2B0 and constants at 0x1CE2F00/0x1CE30A8 were read
from the supplied libil2cpp.so; see reports/runtime-inputs.json and native ASM.
"""
from .localization import UnsupportedLayout

VALUES={1:('+',4.0,1.0,1.0),2:('×',4.0,0.8999999761581421,0.8999999761581421),
    3:('-',0.0,1.7999999523162842,1.0)}


def bind_symbol(field,symbol,rect):
    if symbol not in VALUES:raise UnsupportedLayout('Original damage symbol enum is unknown',symbol=symbol)
    char,offset,sx,sy=VALUES[symbol]
    source_size=field.setdefault('original_damage_symbol_font_size',field['font_size'])
    field.update(text=char,font_size=source_size*sy,character_width_scale=sx/sy,glyph_vertical_offset=offset*sy)
    x,y,w,h=rect
    field['render_rect_top_left']=[x+w*(1-sx)/2,y+h*(1-sy)/2,w*sx,h*sy]
    field['damage_symbol_source']={'native_method':'PokemonCardUIBehaviour.UpdateTextView','virtual_address':'0x47F8108',
        'master_enum':symbol,'asset_enum':symbol+1,'source_markup':'<voffset=%g>%s</voffset>'%(offset,char),
        'local_scale':[sx,sy,1],'original_unscaled_rect':rect,'source_font_size':source_size}
    return field
