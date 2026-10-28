"""Source-bound shiny initialization, restricted to the original frontal frame.

KiraPuyo Awake evaluates serialized settings at its source offset. CircularKira
Awake calls Initialize then LateUpdate within one Unity frame: time/delta=0,
speed=0. Thus morphing is independent of Random.Range; no seed is fabricated.
Native evidence is retained in reports/shiny-initialization-20261022/native.
"""
from bisect import bisect_right
from copy import deepcopy
from pathlib import Path
import math

from pocket_renderer.mrr_initialization import f32,invert_bezier_x,validate_native_source


def evaluate_curve(curve,t):
    """The recovered libunity weighted curve solver, at a source-selected time."""
    t=f32(t);keys=curve['m_Curve']
    if not keys:return 0.0
    if len(keys)==1:return keys[0]['value']
    if t<=keys[0]['time']:
        if curve['m_PreInfinity']!=2:raise ValueError('Shiny curve pre-infinity is not recovered')
        return keys[0]['value']
    if t>=keys[-1]['time']:
        if curve['m_PostInfinity']!=2:raise ValueError('Shiny curve post-infinity is not recovered')
        return keys[-1]['value']
    left,right=keys[bisect_right([k['time'] for k in keys],t)-1:][:2]
    if math.isinf(left['outSlope']) or math.isinf(right['inSlope']):
        return left['value'] if max(left['outSlope'],right['inSlope'])==math.inf else right['value']
    if not (left['weightedMode']&2 or right['weightedMode']&1):
        raise ValueError('Shiny interior unweighted curve segment is not recovered')
    dt=f32(right['time']-left['time'])
    if dt==0:return left['value']
    wo=left['outWeight'] if left['weightedMode']&2 else f32(1/3)
    wi=right['inWeight'] if right['weightedMode']&1 else f32(1/3)
    u=invert_bezier_x(f32(f32(t-left['time'])/dt),wo,f32(1-wi));v=f32(1-u)
    p1=f32(left['value']+f32(wo*f32(dt*left['outSlope'])))
    p2=f32(right['value']-f32(wi*f32(dt*right['inSlope'])))
    u2=f32(u*u);v2=f32(v*v)
    a=f32(left['value']*f32(v*v2));b=f32(p1*f32(f32(u*3)*v2))
    c=f32(p2*f32(v*f32(u2*3)));d=f32(right['value']*f32(u*u2))
    return f32(d+f32(c+f32(a+b)))


def puyo_properties(data,settings):
    # Native ctor LocalFront=(0,0,-1), LightDir=(.5,.5). With the
    # accepted frontal transforms xy=0, UpdateAnimation produces anim=.5.
    anim=f32(.5)
    progress=f32(f32(data['_vertScaleSpeed']*anim)+data['_scaleAnimationOffset'])
    progress=f32(progress-math.floor(progress))
    return {'_RampRepeat':data['_rampRepeat'],'_ScrollScale':data['_scrollScale'],
            '_ScrollOffset':data['_scrollOffset'],'_KiraScale':evaluate_curve(settings['Curve'],progress),'_Anim':anim}


def circular_properties(data):
    """Reproduce the first, untilted Awake→LateUpdate state, not a later frame."""
    if data.get('<IsAnimationStopped>k__BackingField',False):
        raise ValueError('Shiny stopped Awake skips initial ApplyParams; not recovered')
    prims=data['_prims']
    if any(not math.isfinite(p['MaxRotateSpeed']) or p['MaxRotateSpeed']<=0 for p in prims):
        raise ValueError('Shiny initial RNG elimination requires positive MaxRotateSpeed')
    if data['_tiltThreshold']<0 or data['_tiltStateChangeDelay']<0:
        raise ValueError('Shiny initial tilt state outside recovered frontal branch')
    defaults={1:50.0,2:132.0,3:35.0}
    pattern=data['_defaultCircularAnglePattern']
    if pattern not in (0,1,2,3):raise ValueError('Unknown original circular-angle preset')
    angle=defaults[pattern] if pattern else data['_defaultCircularAngleManual']
    # +0x44 speed is explicitly reset to0 by Initialize, delta0 by Validate.
    # Both noise/sine branches are finite; clamp(speed/positiveMax)=0 makes
    # their random phase unobservable in the resulting morphing array.
    properties={'_CircularDefaultAngle':angle,'_MoveAngle':-0.0,'_Tilt':0.0,
        '_PrimAngles':[f32(-p['StartAngle'] if p['ReverseRotation'] else p['StartAngle']) for p in prims],
        '_PrimBaseScales':[p['BaseScale'] for p in prims],
        '_PrimBaseIntensities':[p['BaseIntensity'] for p in prims],
        '_PrimMinIntensities':[p['MinIntensity'] for p in prims],
        '_PrimMaxIntensities':[p['MaxIntensity'] for p in prims],
        '_PrimFlickerScaling':[p['FlickerScaling'] for p in prims],
        '_PrimFlickerAnimOffsets':[0.0]*len(prims),
        '_PrimTypes':[float(p['PrimType']) for p in prims],
        '_PrimMorphing':[0.0]*len(prims)}
    for p in prims:
        if p['PrimType'] not in (0,1,2):raise ValueError('Unknown original shiny primitive type')
        if any(not math.isfinite(float(v)) for v in p.values()):raise ValueError('Nonfinite original shiny primitive')
    return properties


def circular_trail_uvs(data,source_uvs):
    """ApplyVerticesParams writes brightness to source UV.y and repairs seam."""
    n=data['_meshDivideCount'];center=data['_centerIntensity']
    if n<2 or len(source_uvs)!=2*n+2:raise ValueError('Shiny source trail mesh/count mismatch')
    if center<data['_fadeOutEnd'] or center<=0:
        raise ValueError('Shiny initial trail threshold branch not recovered')
    if data['_useLengthLimit'] and int(f32(data['_limitLengthRatio']*n))<1:
        raise ValueError('Shiny initial trail length adjustment branch not recovered')
    if not all(math.isfinite(data[k]) for k in ('_expandPower','_fadeOut','_fadeOutEnd','_limitLengthRatio','_distanceFadeOutCurvePower')):
        raise ValueError('Nonfinite original shiny trail parameters')
    # Initial index=round((moveAngle+360)*n/360)=n. _tempBrightnesses[2n]
    # starts at0; only index n receives center. Expansion is multiplied by
    # initial tilt0 and decay by delta0. Fold max(temp[i],temp[n+i]).
    brightnesses=[center]+[0.0]*(n-1)
    uvs=deepcopy(source_uvs)
    for index,value in enumerate(brightnesses):
        uvs[-2-index*2][1]=value;uvs[-1-index*2][1]=value
    uvs[0][1]=uvs[3][1]
    # Native Enumerable.Last<Vector2> then copies its y into entries1 and2.
    uvs[1][1]=uvs[-1][1];uvs[2][1]=uvs[-1][1]
    return uvs


def _frontal(scene,owner):
    bytransform={o['transform_id']:o for o in scene['objects']}
    current=owner
    while current:
        q=current['local_rotation_xyzw']
        # Rotations about local Z preserve the original (0,0,-1) front.
        if q[0]!=0 or q[1]!=0 or abs(q[2]*q[2]+q[3]*q[3]-1)>1e-6:
            raise ValueError('Shiny initialization supports source frontal Z-only transforms')
        current=bytransform.get(current.get('parent_transform_id'))


def scene_initialization(root,scene,meshes,materials):
    """Return per-slot MPBs and per-renderer copies of original mutated meshes."""
    from pocket_renderer.localization import _resources
    selected=[(o,c) for o in scene['objects'] if o.get('active_in_hierarchy',True)
              for c in o.get('serialized_components',[]) if c.get('data',{}).get('m_Enabled',True)
              and ('_rampRepeat' in c.get('data',{}) or '_prims' in c.get('data',{}))]
    if not selected:return {},{},[]
    resources=_resources(str(Path(root).resolve()))
    blocks,modified,audit={},{},[]
    renderer_objects={o['renderer']['id']:o for o in scene['objects'] if o.get('renderer')}
    def oid(obj):return obj.assets_file.name.lower()+'__'+str(obj.path_id)
    def target(original,data,field):
        pointer=data[field]
        if pointer['m_FileID']==0:
            obj=original.assets_file.objects[pointer['m_PathID']]
        else:obj,_=resources.resolve(original,pointer)
        result=renderer_objects.get(oid(obj))
        if result is None:raise ValueError('Original shiny renderer PPtr missing from selected scene')
        return result
    def add(obj,values,source):
        key=(obj['id'],0)
        if key in blocks:raise ValueError('Overlapping shiny MPBs need original execution order')
        blocks[key]=values;audit.append({'object':obj['id'],'slot':0,'property_block':values,**source})
    for owner,component in selected:
        _frontal(scene,owner)
        original=next(o for o in resources.load(owner['source']['bundle']).objects if oid(o)==component['id'])
        data=original.read_typetree();script,_=resources.resolve(original,data['m_Script']);name=script.read_typetree()['m_ClassName']
        source={'component':component['id'],'component_class':name,'source':owner['source'],
                'frame':'Original Awake initial frontal state; time0/delta0; no later animation or tilt',
                'native_evidence':'reports/shiny-initialization-20261022/native/evidence.json'}
        validate_native_source(str(Path(root).resolve()),name)
        if name=='KiraPuyoObject':
            settings,relative=resources.resolve(original,data['_settings'])
            settings_script,_=resources.resolve(settings,settings.read_typetree()['m_Script'])
            if settings_script.read_typetree()['m_ClassName']!='KiraPuyoAnimationSettings':raise ValueError('Unknown original shiny settings class')
            source['settings']=resources.source(settings,relative)
            add(target(original,data,'_renderer'),puyo_properties(data,settings.read_typetree()),source)
        elif name=='CircularKiraObject':
            values=circular_properties(data)
            source['rng']='Not sampled: finite morph function is multiplied by initial speed0/positive MaxRotateSpeed; only this frame is independent of RNG'
            for field in ('_movingKiraARenderer','_movingKiraBRenderer'):
                obj=target(original,data,field)
                for mid in obj['renderer']['material_slots'][:1]:
                    material=materials[mid]
                    if material['floats'].get('_PrimCount')-material['floats'].get('_PrimDelete')!=len(data['_prims']):
                        raise ValueError('Shiny primitive count/delete differs from original initialized arrays')
                add(obj,values,source)
            # Playing runtime: Initialize gets a copy of B's mesh and assigns
            # it also to A. Both share the subsequent original UV mutation.
            source_b=target(original,data,'_trailKiraBRenderer')
            mesh=deepcopy(meshes[source_b['renderer']['mesh']])
            mesh['uv0']=circular_trail_uvs(data,mesh['uv0'])
            for field in ('_trailKiraARenderer','_trailKiraBRenderer'):
                obj=target(original,data,field);modified[obj['id']]=mesh
                trail_source=dict(source,mesh_source=meshes[source_b['renderer']['mesh']]['source'],
                    mesh_mutation='Original ApplyVerticesParams at initial frame; positions/topology untouched, UV.y brightness and seam updated')
                add(obj,{'_CircularDefaultAngle':values['_CircularDefaultAngle'],'_NoiseTime':0.0},trail_source)
        else:raise ValueError('Unknown original shiny runtime component '+name)
    return blocks,modified,audit
