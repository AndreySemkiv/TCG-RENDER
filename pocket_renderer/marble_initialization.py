"""Original CardMarbleLayer Awake state and its generated linear R16 texture.

Static source: reports/shadow-marble-native-20261022/native. This is the
explicit Awake→Initialize→UpdateRemapTex→FixedUpdate frame, not later motion.
In the original frontal hierarchy localFront.z=-1, so native atan2(0,-1)
makes _Tilt=1; _currentTilt remains zero behind the initial delay queue.
"""
from bisect import bisect_right
from pathlib import Path
import hashlib,json,math,struct
from .mrr_initialization import f32,validate_native_source
from .uv_shadow_initialization import source_component,frontal_z_rotation


def evaluate_curve(curve,t):
    """Unity's cached unweighted polynomial (libunity C9DF58/C9DB3C)."""
    t=f32(t);keys=curve['m_Curve']
    if not keys:return 0.0
    if any(not math.isfinite(k[f]) for k in keys for f in ('time','value','inSlope','outSlope')):
        raise ValueError('Nonfinite original Marble curve key')
    if any(a['time']>=b['time'] for a,b in zip(keys,keys[1:])):
        raise ValueError('Original Marble curve key times are not increasing')
    if len(keys)==1:return f32(keys[0]['value'])
    if t<=keys[0]['time']:
        if curve['m_PreInfinity']!=2:raise ValueError('Marble curve pre-infinity is not recovered')
        return f32(keys[0]['value'])
    if t>=keys[-1]['time']:
        if curve['m_PostInfinity']!=2:raise ValueError('Marble curve post-infinity is not recovered')
        return f32(keys[-1]['value'])
    a,b=keys[bisect_right([k['time'] for k in keys],t)-1:][:2]
    if a['weightedMode']&2 or b['weightedMode']&1:
        from .shiny_initialization import evaluate_curve as weighted
        return weighted(curve,t)
    dt=max(f32(b['time']-a['time']),struct.unpack('<f',struct.pack('<I',0x38d1b717))[0])
    dy=f32(b['value']-a['value']);inv=f32(1/dt);inv2=f32(inv*inv)
    m0=f32(dt*a['outSlope']);m1=f32(dt*b['inSlope'])
    cubic=f32(f32(inv2*f32(f32(f32(m0+m1)-dy)-dy))*inv)
    quad=f32(inv2*f32(f32(f32(f32(dy+f32(dy+dy))-m0)-m0)-m1))
    q=f32(t-a['time'])
    return f32(a['value']+f32(q*f32(a['outSlope']+f32(q*f32(quad+f32(q*cubic))))))


def unorm16(value):
    """libunity BAC878: float32 clamp*65535 + .5, FCVTZU, STRH."""
    if not math.isfinite(value):raise ValueError('Nonfinite original Marble LUT sample')
    return int(f32(f32(min(1.0,max(0.0,f32(value)))*65535.0)+0.5))


def remap_texture(settings):
    exponent=settings['Resolution']
    if type(exponent)!=int or not 0<=exponent<=12:
        raise ValueError('Original Marble texture resolution exceeds recovered allocation bounds')
    size=1<<exponent # Original ldexpf(1.0,Resolution), not Resolution pixels.
    left=[evaluate_curve(settings['DefaultRemapCurve'],f32(x/size)) for x in range(size)]
    right=[evaluate_curve(settings['TiltRemapCurve'],f32(x/size)) for x in range(size)]
    data=bytearray()
    for y in range(size):
        blend=min(1.0,max(0.0,evaluate_curve(settings['RemapRemapCurve'],f32(y/size))))
        for a,b in zip(left,right):
            value=f32(a+f32(blend*f32(b-a)))
            data.extend(struct.pack('<H',unorm16(value)))
    return size,bytes(data)


def initial_properties(data):
    if (not math.isfinite(data['_tiltPower']) or data['_tiltPower']<=0
        or not data['_useMarbleDelay'] or not math.isfinite(data['_delayTime2'])
        or data['_delayTime2']<=0 or not math.isfinite(data['_goalThreshold'])
        or data['_goalThreshold']<=0):
        raise ValueError('Original Marble state is outside the recovered initial frontal/delay branch')
    points=data['_points']
    attrs=[[p['DefaultPosition']['x'],p['DefaultPosition']['y'],p['DefaultForce'],0.0] for p in points]
    if not points or any(not math.isfinite(x) for row in attrs for x in row):
        raise ValueError('Original Marble default points are empty or nonfinite')
    # Initialize copies default positions into BOTH current/goal positions and
    # clears speeds. With positive goalThreshold, UpdatePosition returns early.
    return {'_PointCount':len(points),'_Attributes':attrs,'_Tilt':1.0,
            '_Front':[0.0,0.0,0.0,0.0],'_WorldFront':[0.0,0.0,-1.0,0.0],'_TiltRotation':0.5}


def renderer_overrides(root,scene,out):
    """Return slot-0 blocks, slot-0 texture bindings and retained source audit."""
    selected=[(o,c) for o in scene['objects'] if o.get('active_in_hierarchy',True)
        for c in o.get('serialized_components',[]) if c.get('data',{}).get('m_Enabled',True)
        and '_defaultNoiseRemapSettings' in c.get('data',{}) and '_points' in c.get('data',{})]
    if not selected:return {},{},[]
    root=Path(root).resolve();validate_native_source(str(root),'CardMarbleLayer')
    from .localization import _resources
    resources=_resources(str(root));blocks={};textures={};audit=[];out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for owner,component in selected:
        original,data=source_component(resources,owner,component,'CardMarbleLayer')
        frontal_z_rotation(scene,owner) # LocalFront remains (0,0,-1) through Z-only rotations.
        pointer=data['_renderer']
        if pointer['m_FileID']==0:
            renderer=original.assets_file.objects[pointer['m_PathID']]
        else:renderer,_=resources.resolve(original,pointer)
        rid=renderer.assets_file.name.lower()+'__'+str(renderer.path_id)
        targets=[o for o in scene['objects'] if o.get('renderer',{}).get('id')==rid]
        if len(targets)!=1:raise ValueError('Original Marble renderer reference is not unique in scene')
        target=targets[0];key=(target['id'],0)
        if key in blocks:raise ValueError('Overlapping original Marble controllers require execution order')
        values=initial_properties(data);settings=data['_defaultNoiseRemapSettings']
        size,raw=remap_texture(settings);digest=hashlib.sha256(raw).hexdigest()
        path=out/('marble-remap-'+digest[:16]+'.r16.raw');path.write_bytes(raw)
        binding={'type':'combined_image_sampler','file':str(path.resolve()),'image_format':'r16-unorm',
            'image_size':[size,size],'row_origin':'bottom-left','sampler':'linear','address_mode':'clamp-to-edge'}
        blocks[key]=values;textures[key]={settings['CurveLabel']:binding}
        audit.append({'object':target['id'],'slot':0,'component':component['id'],'component_class':'CardMarbleLayer',
            'source':owner['source'],'property_block':values,'texture_bindings':textures[key],
            'texture_sha256':digest,'texture_bytes':len(raw),'resolution_exponent':settings['Resolution'],
            'curve_settings':settings,'frame':'Original Awake explicitly calls FixedUpdate after Initialize and UpdateRemapTex',
            'native_evidence':'reports/shadow-marble-native-20261022/native/marble.asm',
            'limits':'Initial frontal frame with positive delay; no later tilt/time simulation or runtime capture.'})
    return blocks,textures,audit
