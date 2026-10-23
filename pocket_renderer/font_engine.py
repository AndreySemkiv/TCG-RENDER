"""Populate missing SDF glyphs from the original extracted font outlines.

Original populated pages are never edited. FreeType is used only for missing
glyphs. Its output is explicitly marked as generated, not as a stored Unity
atlas or a verified implementation of Unity's dynamic FontEngine.
"""
from __future__ import annotations
import copy
import ctypes
import hashlib
import json
from pathlib import Path
import sys
from PIL import Image
from .text_tokens import characters as visible_characters
from .text_tokens import font_characters
from .config import PROJECT_ROOT

class GlyphError(ValueError):
    pass

SOURCE_FONTS = {'bold':'TPC_GillSansNovaBold.otf', 'medium':'TPC_GillSansNovaMedium.otf',
                'number':'TPC_FuturaLTProHeavy.otf'}

def _role(text):
    return text.get('font_id') or ('bold' if 'Bold' in text.get('font_file','') else
                                  'medium' if 'Medium' in text.get('font_file','') else 'number')

def missing_glyphs(fonts: dict, ui: dict) -> dict:
    missing = {}
    for text in ui['texts']:
        for role,c in font_characters(dict(text,font_id=_role(text))):
            if c not in '\r\n' and str(ord(c)) not in fonts[role]['unicode_lookup']:
                missing.setdefault(role,set()).add(c)
    return {role:sorted(values,key=ord) for role,values in missing.items()}

def ensure_glyphs(root: Path, fonts: dict, ui_spec: dict, outdir: Path,
                  strict: bool = False) -> dict:
    result = copy.deepcopy(fonts)
    missing = missing_glyphs(result, ui_spec)
    if not missing: return result
    if strict:
        raise GlyphError('Caracteres ausentes dos atlas armazenados: ' + json.dumps(missing,ensure_ascii=False))
    runtime = Path(root)/'tools/font-runtime'
    if not runtime.is_dir():
        runtime = PROJECT_ROOT/'tools/font-runtime'
    if str(runtime) not in sys.path: sys.path.insert(0,str(runtime))
    try:
        import freetype
    except ImportError as error:
        raise GlyphError('Instale freetype-py conforme BATCH-RENDER.md para os caracteres dinâmicos.') from error
    for role, characters in missing.items():
        font = result[role]
        source = next((Path(t['original_font_file']) for t in ui_spec['texts']
                       if _role(t)==role and t.get('original_font_file')), None)
        if source is None:
            if font.get('original_font_file'):source=Path(font['original_font_file'])
            elif role in SOURCE_FONTS:source=Path(root)/'offline-render/bulbasaur-original/fonts'/SOURCE_FONTS[role]
            else:raise GlyphError('Fonte estática original sem contornos; caracteres ausentes em '+role)
        if not source.is_absolute(): source = Path(root)/source
        if not source.is_file(): raise GlyphError('Fonte original ausente: ' + str(source))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        face = freetype.Face(str(source), index=int(font['face_info'].get('m_FaceIndex',0)))
        ppem = int(round(font['face_info']['m_PointSize']))
        face.set_pixel_sizes(0, ppem)
        flags = freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_NO_BITMAP
        existing = {g['m_Index']:g for g in font['glyphs']}
        # Validate the font face and scale against independent original metrics.
        errors = []
        metric_samples=[]
        for glyph in font['glyphs']:
            advance = glyph['m_Metrics']['m_HorizontalAdvance']
            if advance <= 0: continue
            face.load_glyph(glyph['m_Index'], flags)
            errors.append(abs(face.glyph.metrics.horiAdvance/64-advance))
            m=face.glyph.metrics
            metric_samples.append((glyph,{'m_Width':m.width/64,'m_Height':m.height/64,
                'm_HorizontalBearingX':m.horiBearingX/64,'m_HorizontalBearingY':m.horiBearingY/64,
                'm_HorizontalAdvance':m.horiAdvance/64}))
            if len(errors) >= 16: break
        source_metric_overrides=[]
        if errors and max(errors)>1/32:
            # Stored TMP metrics are authoritative, including authored advances.
            # A matching outline can have a different serialized advance (the
            # original number face's digit 1 is one concrete example). Require
            # independent shape metrics and a majority of exact advances before
            # accepting this case; never edit/rebuild the original glyph.
            shape_errors=[abs(g['m_Metrics'][key]-metrics[key])
                          for g,metrics in metric_samples for key in metrics if key!='m_HorizontalAdvance']
            exact_advances=sum(error<=1/32 for error in errors)
            if len(errors)>=16 and exact_advances>=len(errors)-1 and max(shape_errors)<=1/32:
                for (glyph,metrics),error in zip(metric_samples,errors):
                    if error>1/32:
                        source_metric_overrides.append({'glyph_index':glyph['m_Index'],
                            'stored_metrics':copy.deepcopy(glyph['m_Metrics']),
                            'outline_metrics':metrics,'policy':'Preserve original stored glyph and metrics unchanged'})
                errors=[error for error in errors if error<=1/32]+shape_errors
        if not errors or max(errors) > 1/32:
            raise GlyphError('As métricas FreeType não conferem com a fonte original: ' + role)
        padding = int(font['atlas_padding'])
        spread = padding+1
        value = ctypes.c_uint(spread)
        error = freetype.FT_Property_Set(freetype.get_handle(),b'sdf',b'spread',ctypes.byref(value))
        if error: raise GlyphError('FreeType não disponibiliza geração SDF: ' + str(error))
        target = Path(outdir)/'dynamic-fonts'/role
        target.mkdir(parents=True,exist_ok=True)
        width,height = int(font['atlas_width']),int(font['atlas_height'])
        next_index = max((a['index'] for a in font['atlases']), default=-1)+1
        page_index = next_index
        alpha = bytearray(width*height)
        cursor_x=cursor_y=row_height=0
        generated=[]
        native_aliases=[]
        pages=[]
        def finish_page():
            nonlocal alpha,page_index,cursor_x,cursor_y,row_height
            if not any(alpha): return
            image = Image.new('RGBA',(width,height),(255,255,255,0))
            image.putalpha(Image.frombytes('L',(width,height),bytes(alpha)))
            path = target/('atlas%d.png'%page_index)
            image.save(path)
            pages.append({'index':page_index,'width':width,'height':height,'format':'generated-Alpha8-SDF',
                          'png':str(path.resolve()),'png_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                          'generated_from_original_font':True,'source_font_sha256':source_hash,
                          'settings':font['atlases'][0].get('settings',{}),
                          'generator':{'library':'FreeType','version':list(freetype.version()),'ppem':ppem,
                                       'spread':spread,'atlas_padding':padding,'metrics_max_error':max(errors)}})
            if source_metric_overrides:
                pages[-1]['generator']['source_metric_overrides']=source_metric_overrides
            page_index+=1;alpha=bytearray(width*height);cursor_x=cursor_y=row_height=0
        for char in characters:
            code=ord(char);gid=face.get_char_index(code)
            if not gid and code == 0x2011:
                # Shipped TMP_FontAsset.TryAddCharacterInternal, 0x6969384:
                # if U+2011 is absent, use this SAME face's U+002D glyph.
                # Keep the original Unicode and non-breaking text semantics.
                gid=face.get_char_index(0x2D)
                if gid:
                    native_aliases.append({'unicode':code,'glyph_unicode':0x2D,'glyph_index':gid,
                        'method':'TMP_FontAsset.TryAddCharacterInternal','virtual_address':'0x6969384',
                        'condition':'U+2011 absent from the original font face'})
            if not gid: raise GlyphError('A fonte original não contém U+%04X (%s).'%(code,char))
            if gid not in existing:
                face.load_glyph(gid,flags)
                m=face.glyph.metrics
                metrics={'m_Width':m.width/64,'m_Height':m.height/64,
                         'm_HorizontalBearingX':m.horiBearingX/64,'m_HorizontalBearingY':m.horiBearingY/64,
                         'm_HorizontalAdvance':m.horiAdvance/64}
                face.glyph.render(freetype.FT_RENDER_MODE_SDF)
                bitmap=face.glyph.bitmap
                bw,bh,pitch=bitmap.width,bitmap.rows,bitmap.pitch
                # The renderer supplies spread padding; keep the original atlas
                # padding convention by removing only surplus generated margin.
                margin=spread-padding
                tw,th=bw-margin*2,bh-margin*2
                core_w,core_h=bw-spread*2,bh-spread*2
                if min(core_w,core_h,tw,th)<=0 or tw>width or th>height:
                    raise GlyphError('Dimensões SDF incompatíveis para '+char)
                if cursor_x+tw>width: cursor_x=0;cursor_y+=row_height+1;row_height=0
                if cursor_y+th>height: finish_page()
                buffer=bytes(bitmap.buffer)
                for row in range(th):
                    src=(row+margin)*abs(pitch)+margin
                    dest=(cursor_y+row)*width+cursor_x
                    alpha[dest:dest+tw]=buffer[src:src+tw]
                glyph={'m_Index':gid,'m_Metrics':metrics,
                       'm_GlyphRect':{'m_X':cursor_x+padding,'m_Y':height-(cursor_y+padding+core_h),
                                      'm_Width':core_w,'m_Height':core_h},
                       'm_Scale':1.0,'m_AtlasIndex':page_index,'m_ClassDefinitionType':0,
                       'generated_from_original_font':True}
                font['glyphs'].append(glyph);existing[gid]=glyph
                generated.append({'character':char,'unicode':code,'glyph_index':gid,'atlas_index':page_index})
                cursor_x+=tw+1;row_height=max(row_height,th)
            font['unicode_lookup'][str(code)]={'glyph_index':gid,'scale':1.0}
            font['characters'].append({'m_ElementType':1,'m_Unicode':code,'m_GlyphIndex':gid,'m_Scale':1.0})
        finish_page()
        font['atlases'].extend(pages)
        provenance={'source_font':str(source),'source_font_sha256':source_hash,
            'generator':'FreeType native SDF from original outlines; original stored atlas pages unchanged',
            'glyphs':generated,'limit':'The Unity FontEngine dynamic-SDF algorithm is not executed or verified byte-for-byte. Generated glyphs may differ at their edges.'}
        if source_metric_overrides:
            provenance['source_metric_overrides']=source_metric_overrides
        if native_aliases:
            font['native_character_aliases']=native_aliases
            provenance['native_character_aliases']=native_aliases
        if generated:
            font['dynamic_generation']=provenance
            font.setdefault('limitations',[]).append(provenance['limit'])
        elif native_aliases:
            provenance={'source_font':str(source),'source_font_sha256':source_hash,
                        'native_character_aliases':native_aliases,'new_glyph_pixels_generated':False}
        (target/'provenance.json').write_text(json.dumps(provenance,indent=2,ensure_ascii=False),encoding='utf8')
    assert not missing_glyphs(result,ui_spec)
    return result
