"""GlitterFlowMaps.OnEnable→Initialize→Update at the declared initial frame.

Native1.7.5 Initialize0x47F2640 and Update0x47F26B0. Validate initializes
startTime in this same frame, producing time/delta0. UV offsets/light remain0;
rotations come from serialized source fields, through original fmodf(2*pi).
No later animation, reused live component state or elapsed game time is claimed.
"""
import math
from pathlib import Path

from .mrr_initialization import f32,validate_native_source

ANGLE_PERIOD=6.2831854820251465 # Original float atVA0x1CE2ECC, passed to fmodf.


def initial_flow_properties(data):
    fields=('_accelIntensity','_maxFlowSpeed','_minFlowSpeed','_initFlowSpeed','_resistance',
            '_minTiltPower','_lightSpeed','_flowAMinRotateSpeed','_flowAMaxRotateSpeed',
            '_flowBMinRotateSpeed','_flowBMaxRotateSpeed','_flowAInitialRotation','_flowBInitialRotation')
    if any(not math.isfinite(data[name]) for name in fields):
        raise ValueError('Original flow initialization requires finite source fields')
    if data['_maxFlowSpeed']<=data['_minFlowSpeed'] or data['_minFlowSpeed']<0:
        raise ValueError('Original flow speed interval is outside recovered initial contract')
    # Bound this recovery to the actual freshly initialized zero-speed branch;
    # nonzero speeds need all controller intermediates/normalization recovered.
    if data['_initFlowSpeed']!=0:
        raise ValueError('Original nonzero initial flow speed is not recovered')
    return {'_FlowParams':[[0.0,-0.0,0.0,0.0],
                          [f32(math.fmod(data['_flowAInitialRotation'],ANGLE_PERIOD)),
                           f32(math.fmod(data['_flowBInitialRotation'],ANGLE_PERIOD)),0.0,0.0]]}


def renderer_overrides(root,scene):
    selected=[(o,c) for o in scene['objects'] if o.get('active_in_hierarchy',True)
              for c in o.get('serialized_components',[]) if c.get('data',{}).get('m_Enabled',True)
              and '_flowAInitialRotation' in c.get('data',{})]
    if not selected:return {},[]
    root=Path(root).resolve();validate_native_source(str(root))
    from .localization import _resources
    resources=_resources(str(root));blocks={};audit=[]
    targets={o['renderer']['id']:o for o in scene['objects'] if o.get('renderer')}
    def oid(obj):return obj.assets_file.name.lower()+'__'+str(obj.path_id)
    for owner,component in selected:
        original=next(o for o in resources.load(owner['source']['bundle']).objects if oid(o)==component['id'])
        data=original.read_typetree();script,_=resources.resolve(original,data['m_Script'])
        if script.read_typetree()['m_ClassName']!='GlitterFlowMaps':
            raise ValueError('Unknown original flow component class')
        pointer=data['_renderer']
        if not pointer['m_PathID']:raise ValueError('Original flow renderer is absent')
        if pointer['m_FileID']==0:renderer=original.assets_file.objects[pointer['m_PathID']]
        else:renderer,_=resources.resolve(original,pointer)
        obj=targets.get(oid(renderer))
        if obj is None:raise ValueError('Original flow renderer is outside the selected scene')
        key=(obj['id'],0)
        if key in blocks:raise ValueError('Overlapping flow property blocks require original order')
        values=initial_flow_properties(data);blocks[key]=values
        audit.append({'object':obj['id'],'slot':0,'component':component['id'],'component_class':'GlitterFlowMaps',
                      'source':owner['source'],'property_block':values,
                      'frame':'Fresh OnEnable→Initialize→Update, time0/delta0, source initial rotations',
                      'native_evidence':'reports/flow-map-initialization-20261022/',
                      'limits':'Only freshly created initial frame with zero initial flow speed; no later animation.'})
    return blocks,audit
