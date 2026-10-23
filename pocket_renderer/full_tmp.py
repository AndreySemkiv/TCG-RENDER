"""Original non-mobile TMP material: intrinsic outline and face textures.

The full DistanceField shader implements outlines without OUTLINE_ON. Selecting
the mobile shader for this material silently drops its dark outline.
"""
from functools import lru_cache
from pathlib import Path

FULL_SHADER='Common/Shader/Common/CardNew/UI/Card_TextMeshPro_DistanceField_ToRT.shader_bundles'


@lru_cache(maxsize=32)
def _contract(root,material_file,material_id):
    from .localization import _OriginalUIResources
    from .special_passes import _stages,reflect_spirv,_restore_names
    from .compiler import CompilationError
    resources=_OriginalUIResources(root)
    material=next(o for o in resources.load(material_file).objects if o.path_id==material_id)
    data=material.read_typetree()
    shader,relative=resources.resolve(material,data['m_Shader'])
    if relative!=FULL_SHADER or data['m_ValidKeywords']:
        raise CompilationError('Unsupported full TMP shader or keyword variant')
    parsed=shader.read_typetree()['m_ParsedForm']
    passes=parsed['m_SubShaders'][0]['m_Passes']
    if len(passes)!=1:raise CompilationError('Original full TMP pass count changed')
    source=resources.source(shader,relative)
    descriptor={'id':'full-tmp','name':parsed['m_Name'],
        'source':{'bundle':relative,'bundle_sha256':source['bundle_sha256']}}
    stages,records=_stages(Path(root),descriptor,parsed,passes[0],[])
    reflections={stage:reflect_spirv(path) for stage,path in stages.items()}
    _restore_names(reflections,Path(root),descriptor,parsed,passes[0],[])
    return stages,reflections,parsed,dict(data['m_SavedProperties']['m_TexEnvs']),source


def prepare_bindings(root,material,out,prefix,atlas_path,ui_size,projection,identity):
    from .compiler import CompilationError
    from .special_passes import _pack_uniform
    from .shader_clock import shader_clock
    from .engine_texture_defaults import default_texture_color
    from PIL import Image
    source=material['source']
    stages,reflections,parsed,textures,shader_source=_contract(str(Path(root).resolve()),source['file'],source['path_id'])
    values=dict(material['floats']);values.update(material['colors'])
    values.update(shader_clock())
    values.update(_WorldSpaceCameraPos=[0,0,-10],
        _ScreenParams=[ui_size[0],ui_size[1],1+1/ui_size[0],1+1/ui_size[1]],
        unity_ObjectToWorld=identity,unity_WorldToObject=identity,
        glstate_matrix_projection=projection,unity_MatrixVP=projection)
    # This exporter explicitly uses a frontal identity object/environment pose
    # and an unmasked offscreen Canvas. These are harness choices, not a capture
    # of the game's camera or CanvasRenderer. Preserve any saved material value.
    values.setdefault('_EnvMatrix',identity)
    values.setdefault('_UIMaskSoftnessX',0)
    values.setdefault('_UIMaskSoftnessY',0)
    for name,texture in textures.items():
        values[name+'_ST']=[texture['m_Scale']['x'],texture['m_Scale']['y'],texture['m_Offset']['x'],texture['m_Offset']['y']]
    bindings=[]
    for stage,reflection in reflections.items():
        for block in reflection['uniforms']:
            raw=bytearray(block['size'])
            for field in block['fields']:
                if field['name'] not in values:raise CompilationError('Unsupported full TMP uniform: '+field['name'])
                _pack_uniform(raw,field,values[field['name']])
            file=out/(prefix+'.full-'+stage+'.bin');file.write_bytes(raw)
            bindings.append({'set':block['set'],'binding':block['binding'],'type':'uniform_buffer','file':str(file),'stages':[stage]})
        for sample in reflection['samplers']:
            name=sample['name']
            if name=='_MainTex':file=atlas_path
            else:
                texture=textures.get(name)
                if texture is None or texture['m_Texture']['m_PathID']:
                    raise CompilationError('Unsupported full TMP texture: '+name)
                prop=next(p for p in parsed['m_PropInfo']['m_Props'] if p['m_Name']==name)
                color=default_texture_color(root,prop['m_DefTexture']['m_DefaultName'])
                file=out/('tmp-default-'+''.join('%02x'%v for v in color)+'.png')
                if not file.is_file():Image.new('RGBA',(1,1),color).save(file)
            bindings.append({'set':sample['set'],'binding':sample['binding'],'type':'combined_image_sampler',
                'file':str(file),'stages':[stage],'sampler':'linear'})
    return stages,bindings,dict(shader_source,offscreen_context={
        'world_camera_position':[0,0,-10],'environment_matrix':'identity frontal pose',
        'ui_mask_softness':[values['_UIMaskSoftnessX'],values['_UIMaskSoftnessY']],
        'authority':'Saved material values preserved; absent CanvasRenderer mask softness uses explicit unmasked offscreen context',
        'game_camera_or_canvas_captured':False},
        texture_defaults={name:next(p['m_DefTexture'] for p in parsed['m_PropInfo']['m_Props'] if p['m_Name']==name)
            for name in ('_FaceTex','_OutlineTex')})
