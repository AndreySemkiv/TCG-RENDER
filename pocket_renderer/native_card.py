"""Render the game's stored medium card through its unchanged Simple shader.

This is explicitly the native medium presentation, not an HD substitute. The
front texture and localized text stay at their stored resolution. It includes
the game's static art/text layout, including unsupported large-card families,
but does not reproduce animated foil/parallax or user-selected decorations.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import struct

from .catalog import project_root
from .config import normalize_language
from .assets import _Store, _trs, _multiply, _sha
from .compiler import _columns, _inverse
from .shader_import import extract_shader, write_manifest, spirv_entries

PROFILE = 'original-medium-simple-v1'
SIMPLE = 'Common/Shader/Common/CardNew/Card_Simple.shader_bundles'
SIMPLE_HASH = 'b4cc502bfa1e4d59d3960a7a836a7b64528e77ace6b6aba9f561cbb9fec03f17'
STAGE_HASHES = {
    4:('58f5a5f67667ca6f10159df84dfdae9fb79b9b9361cce84718ef69b4d42da674',
       'd77e75b13231bc36af345369d7691867cfeb2d8137cffa3e9dccce168dfe7d4b'),
    0:('4a2061f2412496434a503d637c40bb6f48231500af183f7c10835411807eaa0a',
       'ddf974a521e98f5677fa38519c36e3cb57531c1732feaff7f1a0287251970c8e'),
}
LANGUAGE_SUFFIX = {'en-US':'US', 'pt-BR':'BR', 'ja-JP':'JP'}
LIMITS = [
    'Original medium card presentation, at the stored base/text dimensions; this output is not HD and is never upscaled.',
    'The unchanged original Simple shader combines the stored base and localized UI textures on the original front mesh.',
    'Animated large-card foil/parallax/lighting and user-selected frames/rental decorations are not reproduced by the medium presentation.',
    'Front orthographic projection and neutral dynamic-overlay clear states are explicit offline harness settings; complete Unity runtime equivalence is not certified.',
]


class NativeCardError(ValueError):
    pass


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str),encoding='utf8')


@lru_cache(maxsize=4)
def native_cards_index(root):
    """Index only downloaded file paths; shader compatibility is checked later."""
    source = Path(root)/'input/aladin-complete/output'
    result = {}
    def add(asset,row):
        if asset in result and result[asset]['prefab'] != row['prefab']:
            raise NativeCardError('Ambiguous original medium asset filename: '+asset)
        result[asset] = row
    for parent, _, files in os.walk(source/'Common/CardNew/Face'):
        parent = Path(parent)
        if parent.name != 'Prefabs' or parent.parent.name != 'M':
            continue
        for name in files:
            if not name.endswith('_M.prefab_bundles'):
                continue
            asset = name[:-len('_M.prefab_bundles')]
            directory = parent.parent
            row = {'prefab':(parent/name).relative_to(source).as_posix(),
                'base':(directory/'Textures'/(asset+'_M_BT.png_bundles')).relative_to(source).as_posix(),
                'languages':{lang:(directory/'Textures'/(asset+'_M_'+suffix+'_UT.png_bundles')).relative_to(source).as_posix()
                             for lang,suffix in LANGUAGE_SUFFIX.items()}}
            add(asset,row)
            # Some downloaded extraction filenames were decoded as Windows-1252
            # instead of UTF-8 (Nidoran gender symbols, Zygarde fullwidth percent).
            # Only a reversible byte-level filename correction is accepted.
            try:
                canonical = asset.encode('cp1252').decode('utf8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                canonical = asset
            if canonical != asset and canonical.encode('utf8').decode('cp1252') == asset:
                add(canonical,dict(row, filename_encoding_alias={
                    'catalog_asset_id':canonical,'extracted_asset_id':asset,
                    'operation':'reversible UTF-8 bytes decoded as Windows-1252'}))
    return result


def native_availability(root, card, language=None):
    root = project_root(root)
    row = native_cards_index(str(root)).get(card['asset_id'])
    if row is None:
        return {'available':False,'reason':'original-medium-prefab-not-downloaded','profile':PROFILE}
    source = root/'input/aladin-complete/output'
    needed = ['prefab','base']
    missing = [row[key] for key in needed if not (source/row[key]).is_file()]
    languages = [normalize_language(language)] if language else list(LANGUAGE_SUFFIX)
    missing.extend(row['languages'][lang] for lang in languages if not (source/row['languages'][lang]).is_file())
    return {'available':not missing,'profile':PROFILE,'missing':missing,'native_size_expected':[367,512],
            'shader_validation_pending':True,'paths':row}


@lru_cache(maxsize=4)
def _native_store(root, prefab):
    store = _Store(Path(root),prefab)
    return store, store.graph(prefab)


def _shader_pair(root):
    path = root/'input/aladin-complete/output'/SIMPLE
    if not path.is_file() or _sha(path) != SIMPLE_HASH:
        raise NativeCardError('Original medium Simple shader differs from the audited byte contract')
    out = root/'render-cache/native-shaders'/SIMPLE_HASH
    manifest = out/'decode-manifest.json'
    records = None
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding='utf8'))
            selected = data.get('decoded_programs',[])
            if len(selected) == 2 and all(
                    r['shader_bundle'] == SIMPLE and r['source_bundle_sha256'] == SIMPLE_HASH and
                    r['returncode'] == 0 and
                    (r['source_sha256'],r['spirv_sha256']) == STAGE_HASHES[r['entry_points'][0]['execution_model']] and
                    (root/r['source_smolv']).is_file() and _sha(root/r['source_smolv']) == r['source_sha256'] and
                    (root/r['spirv_file']).is_file() and _sha(root/r['spirv_file']) == r['spirv_sha256'] and
                    spirv_entries((root/r['spirv_file']).read_bytes()) == r['entry_points']
                    for r in selected):
                records = selected
        except (ValueError, KeyError, OSError, TypeError, IndexError):
            records = None
    if records is None:
        records = extract_shader(root,SIMPLE,out/'raw',out/'decoded','card-simple')
        if any((r['source_sha256'],r['spirv_sha256']) != STAGE_HASHES.get(r['entry_points'][0]['execution_model']) for r in records):
            raise NativeCardError('Losslessly decoded medium stages differ from the audited original bytes')
        write_manifest(manifest,records)
    by_stage = {r['entry_points'][0]['execution_model']:str(root/r['spirv_file']) for r in records}
    if set(by_stage) != {0,4}:
        raise NativeCardError('Original medium shader must have one vertex and one fragment stage')
    return {'vertex':by_stage[0],'fragment':by_stage[4]}, records, manifest


def prepare_native_card(root, card, language, outdir):
    """Prepare a one-pass original-shader manifest; never opens the GPU."""
    from UnityPy.helpers.MeshHelper import MeshHandler
    from PIL import Image
    root, out = project_root(root), Path(outdir).resolve()
    language = normalize_language(language)
    available = native_availability(root,card,language)
    if not available['available']:
        raise NativeCardError(available.get('reason') or 'Native card resources missing: '+', '.join(available.get('missing',[])))
    paths = available['paths']
    source = root/'input/aladin-complete/output'
    store, keys = _native_store(str(root),paths['prefab'])
    gos = [(k,store.tree(k)) for k in keys if store.objects[k].type.name == 'GameObject']
    fronts = [(k,t) for k,t in gos if t['m_Name'] == 'Card_F' and t['m_IsActive']]
    if len(fronts) != 1:
        raise NativeCardError('Original medium prefab has no unique Card_F')
    key, front = fronts[0]
    components = [store.resolve(key,v['component']) for v in front['m_Component']]
    renderer = next(k for k in components if store.objects[k].type.name == 'MeshRenderer')
    meshfilter = next(k for k in components if store.objects[k].type.name == 'MeshFilter')
    transform = next(k for k in components if store.objects[k].type.name == 'Transform')
    localized_bindings = [store.tree(k) for k in components if store.objects[k].type.name == 'MonoBehaviour'
                          and store.tree(k).get('_dynamicPropertyType') == 0
                          and store.tree(k).get('_dynamicUIType') == 2]
    if len(localized_bindings) != 1:
        raise NativeCardError('Original medium prefab has no unique LocalizedText binding')
    renderer_data = store.tree(renderer)
    if not renderer_data['m_Enabled']:
        raise NativeCardError('Original medium front renderer is disabled')
    material_key = store.resolve(renderer,renderer_data['m_Materials'][0])
    material = store.tree(material_key)
    shader_key = store.resolve(material_key,material['m_Shader'])
    if store.provenance[shader_key]['bundle'].replace('\\','/') != SIMPLE:
        raise NativeCardError('Unsupported original medium front shader')
    if material.get('m_ValidKeywords'):
        raise NativeCardError('Unsupported original medium shader keywords')
    mesh_key = store.resolve(meshfilter,store.tree(meshfilter)['m_Mesh'])
    mesh = store.objects[mesh_key].read()
    handler = MeshHandler(mesh)
    handler.process()
    vertices, normals, uv = handler.m_Vertices, handler.m_Normals, handler.m_UV0
    if len(normals) != len(vertices) or len(uv) != len(vertices):
        raise NativeCardError('Original medium front requires normals and UV0')
    triangles = handler.get_triangles()[0]
    base_vertex = mesh.m_SubMeshes[0].baseVertex
    indices = [int(i+base_vertex) for tri in triangles for i in tri]
    def world(key):
        data = store.tree(key)
        parent = store.resolve(key,data['m_Father'])
        local = _trs(data)
        return _multiply(world(parent),local) if parent else local
    matrix = world(transform)
    # Front model bounds define a face-on export; no raster image resampling.
    points = [[sum(matrix[r][c]*p[c] for c in range(3))+matrix[r][3] for r in range(3)] for p in vertices]
    xmin,xmax = min(p[0] for p in points),max(p[0] for p in points)
    ymin,ymax = min(p[1] for p in points),max(p[1] for p in points)
    projection = [[2/(xmax-xmin),0,0,-(xmax+xmin)/(xmax-xmin)],
                  [0,2/(ymax-ymin),0,-(ymax+ymin)/(ymax-ymin)],
                  [0,0,0,.5],[0,0,0,1]]
    out.mkdir(parents=True,exist_ok=True)
    texture_dir = out/'textures'
    texture_dir.mkdir(exist_ok=True)
    props = {name:value for name,value in material['m_SavedProperties']['m_TexEnvs']}
    textures = {}
    def save_original_texture(target_key,label):
        texture = store.objects[target_key].read()
        image = texture.image
        if image.size != (texture.m_Width,texture.m_Height):
            raise NativeCardError('Native source texture size changed during decode')
        path = texture_dir/(label+'.png')
        image.save(path)
        return {'file':str(path),'pixels':list(image.size),'png_sha256':_sha(path),
            'source':store.provenance[target_key],'source_texture_name':texture.m_Name,
            'original_format':int(texture.m_TextureFormat),'sampler_settings':store.objects[target_key].read_typetree()['m_TextureSettings']}
    base_key = store.resolve(material_key,props['_BaseTex']['m_Texture'])
    textures['_BaseTex'] = save_original_texture(base_key,'base')
    locale_relative = paths['languages'][language]
    store.load(locale_relative)
    locale_objects = [k for k,p in store.provenance.items() if p['bundle'] == locale_relative and store.objects[k].type.name == 'Texture2D']
    if len(locale_objects) != 1:
        raise NativeCardError('Original medium locale bundle has no unique texture')
    textures['_DynamicUITex'] = save_original_texture(locale_objects[0],'localized-ui')
    if textures['_BaseTex']['pixels'] != textures['_DynamicUITex']['pixels']:
        raise NativeCardError('Original medium base/text dimensions differ; no resizing is allowed')
    width,height = textures['_BaseTex']['pixels']
    if not (1 <= width <= 734 and 1 <= height <= 1024):
        raise NativeCardError('Unexpected original medium source dimensions')
    # Neutral offscreen dynamic layers are render-target clear states, not
    # generated art or replacement raster details. Keep their semantics explicit.
    for label,rgba in [('neutral-opacity',(0,0,0,0)),('neutral-inverse',(0,0,0,255))]:
        path = texture_dir/(label+'.png')
        Image.new('RGBA',(1,1),rgba).save(path)
    neutral_opacity = str(texture_dir/'neutral-opacity.png')
    neutral_inverse = str(texture_dir/'neutral-inverse.png')
    for property_name in ('_AdditionalFrameTex','_AdditionalFrameTrainersHeaderTex','_RentalTex'):
        textures[property_name] = {'file':neutral_opacity,'source':'Neutral no-user-overlay render-target clear state RGBA(0,0,0,0)','pixels':[1,1]}
    textures['_DecorationTex'] = {'file':neutral_inverse,'source':'Neutral no-decoration inverse-alpha render-target clear state RGBA(0,0,0,1)','pixels':[1,1]}
    decoration_key = store.resolve(material_key,props['_DecorationMask']['m_Texture'])
    textures['_DecorationMask'] = save_original_texture(decoration_key,'decoration-mask')
    stages, records, shader_manifest = _shader_pair(root)
    parsed = store.tree(shader_key)['m_ParsedForm']['m_SubShaders'][0]['m_Passes'][0]
    names = {value:name for name,value in parsed['m_NameIndices']}
    common = parsed['progVertex']['m_CommonParameters']
    bindings = []
    values = {'unity_ObjectToWorld':matrix,'unity_WorldToObject':_inverse(matrix),'unity_MatrixVP':projection,
              '_Blend':[material_value for material_name,material_value in material['m_SavedProperties']['m_Colors'] if material_name == '_Blend'][0]}
    values['_Blend'] = [values['_Blend'][c] for c in 'rgba']
    for cb in common['m_ConstantBuffers']:
        raw = bytearray((cb['m_Size']+15)//16*16)
        for field in cb['m_MatrixParams']+cb['m_VectorParams']:
            name = names[field['m_NameIndex']]
            if name not in values:
                raise NativeCardError('Unresolved original medium uniform: '+name)
            value = values[name]
            if isinstance(value[0],list):
                value = _columns(value)
            struct.pack_into('<%df'%len(value),raw,field['m_Index'],*value)
        binding = next(b for b in common['m_ConstantBufferBindings'] if b['m_NameIndex'] == cb['m_NameIndex'])
        path = out/('globals-'+str(binding['m_Index'])+'.bin')
        path.write_bytes(raw)
        bindings.append({'set':(binding['m_Index']>>16)&255,'binding':binding['m_Index']&65535,
            'type':'uniform_buffer','file':str(path),'stages':['fragment' if (binding['m_Index']>>24)==8 else 'vertex']})
    for param in common['m_TextureParams']:
        name = names[param['m_NameIndex']]
        if name not in textures:
            raise NativeCardError('Unresolved original medium texture: '+name)
        settings = textures[name].get('sampler_settings')
        sampler,address = 'linear','clamp-to-edge'
        if settings:
            if settings['m_FilterMode'] not in (0,1) or settings['m_WrapU'] != settings['m_WrapV'] or settings['m_WrapU'] not in (0,1):
                raise NativeCardError('Original native sampler requires an unsupported filter/address mode')
            sampler = 'nearest' if settings['m_FilterMode'] == 0 else 'linear'
            address = 'repeat' if settings['m_WrapU'] == 0 else 'clamp-to-edge'
        bindings.append({'set':(param['m_Index']>>16)&255,'binding':param['m_Index']&65535,
            'type':'combined_image_sampler','file':textures[name]['file'],'stages':['fragment'],
            'sampler':sampler,'address_mode':address})
    vfile,ifile = out/'front.vertices.bin',out/'front.indices.bin'
    packed = [list(p[:3])+[1]+list(n[:3])+list(u[:2]) for p,n,u in zip(vertices,normals,uv)]
    vfile.write_bytes(b''.join(struct.pack('<9f',*v) for v in packed))
    ifile.write_bytes(struct.pack('<%dI'%len(indices),*indices))
    state = parsed['m_State']['rtBlend0']
    factors = {0:'zero',1:'one',5:'src_alpha',10:'one_minus_src_alpha'}
    blend = {'enabled':True,'src_color':factors[int(state['srcBlend']['val'])],
             'dst_color':factors[int(state['destBlend']['val'])],
             'src_alpha':factors[int(state['srcBlendAlpha']['val'])],
             'dst_alpha':factors[int(state['destBlendAlpha']['val'])]}
    draw = {'name':'Original medium Card_F','queue':2000,
        'vertices':{'file':str(vfile),'stride':36,'attributes':[{'location':0,'format':'float4','offset':0},
            {'location':1,'format':'float3','offset':16},{'location':2,'format':'float2','offset':28}]},
        'indices':{'file':str(ifile)},'shaders':stages,'bindings':bindings,
        'state':{'cull':'back','front_face':'clockwise','depth_test':True,'depth_write':True,'depth_compare':'less_or_equal','blend':blend},
        'provenance':{'prefab':store.provenance[key],'front_mesh':store.provenance[mesh_key],
            'material':store.provenance[material_key],'material_values':material,'textures':textures,
            'shader_bundle_sha256':SIMPLE_HASH,'shader_records':records,'language':language,
            'filename_encoding_alias':paths.get('filename_encoding_alias'),
            'language_asset_code':LANGUAGE_SUFFIX[language],'dynamic_property_type':0,'dynamic_ui_type':2,
            'dynamic_binding_source':'Original M prefab DynamicUI component: dynamicPropertyType0 / DynamicUIType.LocalizedText2; locale bundle suffix US/BR/JP.',
            'source_rasters_resized':False}}
    manifest = out/'native-manifest.json'
    _write(manifest,{'draws':[draw],'limits':LIMITS,'profile':PROFILE,'card':card['id'],'language':language,'output_size':[width,height]})
    # The generic Vulkan driver reads these before honoring --skip-base. All
    # actual geometry/materials are carried by the explicit original pass above.
    _write(out/'scene.json',{'objects':[],'meshes':[],'textures':[]})
    _write(out/'materials.json',[])
    result = {'profile':PROFILE,'native':True,'scene_dir':out,'assets_dir':out,
        'consumer_manifest':manifest,'ui_manifest':None,'ui_raw_path':None,
        'decoded_shaders':shader_manifest,'output_size':[width,height],
        'limits':LIMITS,'counts':{'base_draws':1,'ui_draws':0,'rarity_draws':0},'source_rasters_resized':False}
    _write(out/'prepared-native.json',result)
    return result
