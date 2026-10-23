"""Original Text/Holo UI camera targets and per-renderer texture routing.

Native DynamicUITypeExtensions.GetLayer (0x4815C5C) selects CardUIText
for Text=0 and CardUIMetallic for Holo=1. The recovered UI prefab stores
those layers as 17 and 18. Image and TMP layers are per GameObject: a
layer-17 child under a layer-18 parent still belongs to the text target.
"""
from copy import deepcopy

LAYERS={0:17,1:18}
NAMES={0:'Text',1:'Holo'}


def _checked_image_sources(resources,material,file,raw):
    """Check the actual selected installation, not a historical export manifest.

    These are the inspected neutral Image material and its no-keyword shader.
    Pinning both source bundles preserves all material/stencil properties while
    allowing newly imported card catalogs to use the same shared UI contract.
    """
    from .source_validation import load_profile
    from .localization import UnsupportedLayout
    profile=load_profile()['bundles']
    def checked(obj,relative,expected_file,expected_pid,kind):
        source=resources.source(obj,relative)
        if (source['file'].replace('\\','/')!=expected_file
                or source['path_id']!=expected_pid
                or source['bundle_sha256']!=profile.get(expected_file)):
            raise UnsupportedLayout('Original category Image '+kind+' differs from prepared UI shader',**{kind:source})
        return source
    source=checked(material,file,'Common/CardNew/Common/UI/Materials/UI-Default-ToRT.mat_bundles',
        -5098138999901745883,'material')
    shader,shader_file=resources.resolve(material,raw['m_Shader'])
    shader_source=checked(shader,shader_file,'Common/Shader/Common/CardNew/UI/Card_UI_Default_ToRT.shader_bundles',
        5313265162950190389,'shader')
    return source,shader_source


def original_image_binding(resources,go,pid,sid):
    """Recover a simple Image using the exact prepared UI-to-RT material.

    Category masks are ordinary source Sprites on CardUIMetallic. They are
    not stencil masks to apply to the completed Text image.
    """
    import json
    from .localization import UnsupportedLayout
    from .skills import _path,_rect
    data=resources.trees[pid];tree=resources.gos[go]
    if data.get('m_Type')!=0 or data.get('m_PreserveAspect') or data.get('m_UseSpriteMesh'):
        raise UnsupportedLayout('Original category Image geometry is not recovered',image=pid)
    if tree['m_Layer'] not in LAYERS.values():
        raise UnsupportedLayout('Original category Image camera layer is not recovered',layer=tree['m_Layer'])
    current=resources.transforms[go]
    while current:
        transform=resources.trees[current]
        if any(abs(transform['m_LocalScale'][k]-1)>1e-6 for k in ('x','y','z')) or any(
                abs(transform['m_LocalRotation'][k]-(1 if k=='w' else 0))>1e-6 for k in ('x','y','z','w')):
            raise UnsupportedLayout('Original category Image transform requires mesh evaluation',transform=current)
        current=transform['m_Father']['m_PathID']
    material,file=resources.resolve(resources.objects[pid],data['m_Material'])
    raw=json.loads(json.dumps(material.read_typetree()))
    source,shader_source=_checked_image_sources(resources,material,file,raw)
    return {'id':sid,'source_sprite':sid,'render_rect_top_left':_rect(resources,go),
        'confidence':'original-image-material-rect-and-camera-layer',
        'source_path':_path(resources,go),'source_layer':tree['m_Layer'],
        'vertex_color_rgba':[data['m_Color'][k] for k in ('r','g','b','a')],
        'image_source':resources.source(resources.objects[pid],resources.UI),
        'material_source':source,'shader_source':shader_source,
        'original_image':data,'original_material':raw,
        'camera_routing':'per-GameObject source layer; partition before rasterization'}


def ui_for_type(spec,target_type):
    """Partition source geometry before drawing; never mask a raster result."""
    from .localization import UnsupportedLayout
    if target_type not in LAYERS:
        raise UnsupportedLayout('Original dynamic UI target is not recovered',dynamic_ui_type=target_type)
    layer=LAYERS[target_type];result=deepcopy(spec)
    # Legacy fields have already been recovered as Text-only. New families
    # must retain source_layer when they add a non-Text object.
    def selected(value):return value.get('source_layer',17)==layer
    result['texts']=[v for v in result.get('texts',[]) if selected(v)]
    result['sprite_bindings']=[v for v in result.get('sprite_bindings',[]) if selected(v)]
    result['sprites']=[v for v in result.get('sprites',[]) if selected(v)]
    result['dynamic_ui_target']={'type':target_type,'name':NAMES[target_type],
        'layer':layer,'source_method':'DynamicUITypeExtensions.GetLayer',
        'virtual_address':'0x4815C5C','partition':'original per-GameObject layer, before rasterization'}
    return result


def renderer_dynamic_type(obj,property_type=0):
    """Read the exact DynamicUI component attached to this renderer.

    Property type 0 is _DynamicUITex (DynamicShaderPropertyType.DynamicUI).
    An absent component is distinct from a recovered Text target.
    """
    from .compiler import CompilationError
    renderer=obj.get('renderer',{});rid=renderer.get('id','').rsplit('__',1)[-1]
    matches=[]
    for component in obj.get('serialized_components',[]):
        data=component.get('data',{})
        if '_dynamicUIType' not in data or data.get('_dynamicPropertyType')!=property_type:continue
        pointer=data.get('_renderer',{})
        if not data.get('m_Enabled',True):continue
        if rid and str(pointer.get('m_PathID'))!=rid:
            raise CompilationError('Original DynamicUI points to another renderer: '+obj.get('name',''))
        matches.append(data['_dynamicUIType'])
    if len(matches)>1:
        raise CompilationError('Original DynamicUI texture target is ambiguous: '+obj.get('name',''))
    return matches[0] if matches else None


def required_types(scene):
    """List active original DynamicUI targets; no unsupported-type fallback."""
    from .compiler import CompilationError
    found={0}
    for obj in scene.get('objects',[]):
        if not obj.get('active_in_hierarchy',True) or not obj.get('renderer',{}).get('enabled',True):continue
        kind=renderer_dynamic_type(obj)
        if kind is None:continue
        if kind not in LAYERS:
            raise CompilationError('Original dynamic UI camera target is not recovered: '+str(kind))
        found.add(kind)
    return sorted(found)
