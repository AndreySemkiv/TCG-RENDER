"""Original TMP ex sprite asset, material and native font-type index selection."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,re

ASSET_ID=840073264968542736
INDEX_BY_FONT_TYPE={1:0,2:2,3:1,4:3} # native int table at 0x1CE0480, read by 0x4A21000.
TAG=re.compile(r'\[Img:ex\s*\]')
TMP_SOURCE='https://raw.githubusercontent.com/Unity-Technologies/uGUI/main/com.unity.ugui/Runtime/TMP/TextMeshProUGUI.cs'


def bind_inline_ex(root,resources,text,group,group_source,output):
    matches=list(TAG.finditer(text['text']))
    if not matches:return
    from .localization import UnsupportedLayout
    font_type=text.get('img_tag_font_type_override',group.get('_imgTagFontType'))
    if font_type not in INDEX_BY_FONT_TYPE:raise UnsupportedLayout('Original inline ex font type is unavailable',font_type=font_type)
    objects={o.path_id:o for o in resources.load('Common/Font_bundles').objects}
    obj=objects[ASSET_ID];data=obj.read_typetree();index=INDEX_BY_FONT_TYPE[font_type]
    character=data['m_SpriteCharacterTable'][index]
    glyph=next(g for g in data['m_GlyphTable'] if g['m_Index']==character['m_GlyphIndex'])
    material_obj,material_file=resources.resolve(obj,data['m_Material']);material_data=material_obj.read_typetree()
    shader_obj,shader_file=resources.resolve(material_obj,material_data['m_Shader']);parsed=shader_obj.read_typetree()['m_ParsedForm']
    out=Path(output)/'inline-ex';out.mkdir(parents=True,exist_ok=True)
    tex,tex_file=resources.resolve(obj,data['spriteSheet']);texture=tex.read();png=out/'TextExSprite.png'
    image=texture.image
    if image.size!=(texture.m_Width,texture.m_Height):raise UnsupportedLayout('Original ex sprite texture dimensions changed during decode')
    image.save(png);raw=out/'TextExSprite.texture.bin';raw.write_bytes(bytes(texture.get_image_data()))
    shader_path=out/'sprite-shader.parsed.json';shader_path.write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding='utf8')
    props=material_data['m_SavedProperties'];mat={'id':'original-tmp-ex-material','name':material_data['m_Name'],
        'floats':dict(props['m_Floats']),'ints':dict(props.get('m_Ints',[])),'colors':dict(props['m_Colors']),
        'shader_id':'original-tmp-ex-shader','shader_keywords':material_data.get('m_ValidKeywords',[]),
        'custom_render_queue':material_data['m_CustomRenderQueue'],'textures':[]}
    for name,value in props['m_TexEnvs']:
        if value['m_Texture']['m_PathID'] not in (0,tex.path_id):raise UnsupportedLayout('Original ex sprite secondary texture not recovered',property=name)
        mat['textures'].append({'property':name,'texture_id':'original-tmp-ex-atlas' if value['m_Texture']['m_PathID'] else None,
            'scale':[value['m_Scale'][k] for k in 'xy'],'offset':[value['m_Offset'][k] for k in 'xy']})
    sizes=[c for c in text['companion_components'] if '_exTagFontSize' in c['data']]
    if len(sizes)!=1:raise UnsupportedLayout('Original ex size component is ambiguous',text_id=text['id'])
    size=sizes[0]['data']['_exTagFontSize'] or 23.0
    source=resources.source(shader_obj,shader_file)
    definition={'character':character,'glyph':glyph,'face_info':data['m_FaceInfo'],'font_size':size,
        'asset_source':resources.source(obj,'Common/Font_bundles'),'group_source':group_source,
        'font_type':font_type,'sprite_character_index':index,'size_source':sizes[0],
        'native_method':'0x4A21000','native_index_table_address':'0x1CE0480','tmp_geometry_reference':TMP_SOURCE,
        'texture':{'id':'original-tmp-ex-atlas','png':str(png.resolve()),'width':texture.m_Width,'height':texture.m_Height,
            'unity_settings':{'filter_mode':texture.m_TextureSettings.m_FilterMode,'wrap_u':texture.m_TextureSettings.m_WrapU},
            'source':resources.source(tex,tex_file),'sha256':hashlib.sha256(png.read_bytes()).hexdigest(),
            'raw_file':str(raw.resolve()),'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest()},
        'material':mat,'shader':{'id':'original-tmp-ex-shader','name':parsed['m_Name'],'parsed':str(shader_path.resolve()),
            'source':dict(source,bundle=source['file'])}}
    text['inline_ex']={m[0]:deepcopy(definition) for m in matches}


def sprite_geometry(token,text,font,size):
    """TMP's zero-point-size sprite-face branch, using the current font face."""
    sprite=text['inline_ex'][token[1]];face=font['face_info'];sf=sprite['face_info'];glyph=sprite['glyph']
    point=sprite['font_size'];char_scale=sprite['character']['m_Scale']*glyph['m_Scale']
    if sf['m_PointSize']>0:scale=point/sf['m_PointSize']*sf['m_Scale']*char_scale
    else:scale=face['m_AscentLine']/glyph['m_Metrics']['m_Height']*char_scale*point/face['m_PointSize']*face['m_Scale']
    horizontal=token[3] if len(token)>3 else text.get('horizontal_scale',1)
    metrics=glyph['m_Metrics']
    return {'scale':scale,'advance':metrics['m_HorizontalAdvance']*scale*horizontal,
        'bearing_x':metrics['m_HorizontalBearingX']*scale*horizontal,'bearing_y':metrics['m_HorizontalBearingY']*scale,
        'width':metrics['m_Width']*scale*horizontal,'height':metrics['m_Height']*scale}


def prepare_inline_draws(root,fields,out,ui_size):
    from .special_passes import prepare_scene_draws
    from .compiler import IDENTITY
    draws=[];audit=[]
    for field in fields:
        item=field['inline_sprite'];definition=item['definition'];rect=definition['glyph']['m_GlyphRect'];tex=definition['texture']
        x,y,w,h=field['render_rect_top_left'];left=x-367;top=512-y
        uv=[[rect['m_X']/tex['width'],rect['m_Y']/tex['height']],[(rect['m_X']+rect['m_Width'])/tex['width'],rect['m_Y']/tex['height']],
            [(rect['m_X']+rect['m_Width'])/tex['width'],(rect['m_Y']+rect['m_Height'])/tex['height']],[rect['m_X']/tex['width'],(rect['m_Y']+rect['m_Height'])/tex['height']]]
        mid=definition['material']['id'];mesh={'vertices':[[left,top-h,0],[left+w,top-h,0],[left+w,top,0],[left,top,0]],
            'uv0':uv,'colors':[[1,1,1,1]]*4,'submeshes':[{'triangles':[[0,1,2],[0,2,3]]}]}
        scene={'textures':[tex],'shaders':[definition['shader']],'objects':[{'id':field['id'],'name':field['id'],
            'world_matrix':IDENTITY,'renderer':{'mesh':'glyph','material_slots':[mid]}}]}
        projection=[[2/734,0,0,0],[0,2/1024,0,0],[0,0,1,.5],[0,0,0,1]]
        more,info,_=prepare_scene_draws(root,out,scene,{'glyph':mesh},{mid:definition['material']},out,projection,
            harness_uniforms={'glstate_matrix_projection':projection,'_ScreenParams':[ui_size[0],ui_size[1],1+1/ui_size[0],1+1/ui_size[1]],
                '_TextureSampleAdd':[0,0,0,0]}) # Original color atlas, no Alpha8 canvas expansion.
        for draw in more:draw['queue']=3100;draw['provenance']['inline_sprite']=item
        draws.extend(more);audit.extend(info)
    return draws,audit
