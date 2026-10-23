"""Original CardUVShadow Awake→LateUpdate property blocks, frontal frame.

Native evidence: reports/shadow-marble-native-20261022/native/uvshadow.asm.
The light is projected onto the actual controller plane, then transformed back
to local space and added to the serialized offset. No missing uniform is filled
with an arbitrary zero. Other orientations remain explicitly unsupported.
"""
from pathlib import Path
import json,math
from .mrr_initialization import f32,validate_native_source


def source_component(resources,owner,component,expected_class):
    def oid(obj):return obj.assets_file.name.lower()+'__'+str(obj.path_id)
    original=next(o for o in resources.load(owner['source']['bundle']).objects if oid(o)==component['id'])
    data=original.read_typetree();script,_=resources.resolve(original,data['m_Script'])
    if script.read_typetree()['m_ClassName']!=expected_class:
        raise ValueError('Original initialization component class differs: '+expected_class)
    return original,data


def frontal_z_rotation(scene,owner):
    """Recover TransformDirection's Z rotation, excluding Transform scale."""
    nodes={o['transform_id']:o for o in scene['objects']};current=owner
    z,w=0.0,1.0;visited=set()
    while current:
        if current['transform_id'] in visited:raise ValueError('Cyclic original transform hierarchy')
        visited.add(current['transform_id'])
        q=current['local_rotation_xyzw']
        if (len(q)!=4 or not all(math.isfinite(v) for v in q) or q[0]!=0 or q[1]!=0
            or abs(q[2]*q[2]+q[3]*q[3]-1)>1e-6):
            raise ValueError('Original initializer requires recovered frontal Z-only transforms')
        z,w=f32(f32(q[3]*z)+f32(q[2]*w)),f32(f32(q[3]*w)-f32(q[2]*z))
        parent=current.get('parent_transform_id')
        if parent and parent not in nodes:raise ValueError('Original transform parent is outside scene')
        current=nodes.get(parent)
    return z,w


def initial_shadow_properties(data,rotation=(0.0,1.0)):
    light=[data['_lightDir'][k] for k in ('x','y','z')]
    offset=[data['_translateOffset'][k] for k in ('x','y')]
    if not all(math.isfinite(v) for v in light+offset+list(rotation)):
        raise ValueError('Nonfinite original UV shadow initialization fields')
    x,y,z=map(f32,light)
    length=f32(math.sqrt(f32(f32(z*z)+f32(f32(x*x)+f32(y*y)))))
    if length>f32(1e-5):x,y=f32(-x/length),f32(-y/length)
    else:x,y=0.0,0.0 # Native Vector3.Normalize threshold branch, not a substitute.
    rz,rw=rotation
    c=f32(1-f32(2*f32(rz*rz)));s=f32(2*f32(rz*rw))
    local_x=f32(f32(c*x)+f32(s*y));local_y=f32(f32(c*y)-f32(s*x))
    return {'_ShadowUVTranslate':[f32(local_x+offset[0]),f32(local_y+offset[1]),0.0,0.0]}


def renderer_overrides(root,scene_dir,scene,materials):
    selected=[(o,c) for o in scene['objects'] if o.get('active_in_hierarchy',True)
        for c in o.get('serialized_components',[]) if c.get('data',{}).get('m_Enabled',True)
        and '_translateOffset' in c.get('data',{}) and '_lightDir' in c.get('data',{})
        and '_settings' not in c.get('data',{})]
    if not selected:return {},[]
    root=Path(root).resolve();validate_native_source(str(root))
    from .localization import _resources
    resources=_resources(str(root));result={};audit=[]
    parents={o['transform_id']:o.get('parent_transform_id') for o in scene['objects']}
    shaders={s['id']:s for s in scene['shaders']}
    for owner,component in selected:
        _,data=source_component(resources,owner,component,'CardUVShadow')
        values=initial_shadow_properties(data,frontal_z_rotation(scene,owner))
        for obj in scene['objects']:
            renderer=obj.get('renderer')
            if not renderer or not obj.get('active_in_hierarchy',True):continue
            transform=obj['transform_id']
            while transform is not None and transform!=owner['transform_id']:transform=parents.get(transform)
            if transform is None:continue
            matched=False
            for mid in renderer['material_slots']:
                parsed=json.loads((Path(scene_dir)/shaders[materials[mid]['shader_id']]['parsed']).read_text(encoding='utf8'))
                matched|=any(['SearchTag','Card-UVShadow'] in p['m_State']['m_Tags']['tags']
                    for sub in parsed['m_SubShaders'] for p in sub['m_Passes'])
            if not matched:continue
            if obj['id'] in result:raise ValueError('Overlapping original UV shadow controllers require execution order')
            result[obj['id']]=values
            audit.append({'object':obj['id'],'component':component['id'],'component_class':'CardUVShadow',
                'source':owner['source'],'property_block':values,'search_tags':['Card-UVShadow'],
                'frame':'Original Awake calls LateUpdate; frontal source transform, original light and offset',
                'scope':'All material slots of renderer matched by a native SearchTag, as ApplyParams',
                'native_evidence':'reports/shadow-marble-native-20261022/native/uvshadow.asm',
                'limits':'Static frontal frame; no later tilt/camera runtime certification.'})
    return result,audit
