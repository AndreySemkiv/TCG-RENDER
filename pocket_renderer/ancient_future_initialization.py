"""Original Ancient/Future Awake shader properties for a fresh frontal frame.

Evidence: reports/additional-scene-families-20261022/. These initial states
follow their own controllers, not the similarly named MRR serialized fields.
Later motion, elapsed animation and nonempty particle systems stay unsupported.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math

from .mrr_initialization import f32, validate_native_source, evaluate_initial_curve
from .shiny_initialization import _frontal


@lru_cache(maxsize=4)
def validate_source(root):
    root = Path(root).resolve()
    validate_native_source(str(root))
    path = root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat'
    if hashlib.sha256(path.read_bytes()).hexdigest() != '905b9505a935828ac7174b7086d15e23640dcf61451e604e0085ae98e06c8618':
        raise ValueError('Ancient/Future initialization metadata differs from inspected source')


def _playing(data):
    if data.get('<IsAnimationStopped>k__BackingField', False):
        raise ValueError('Ancient/Future stopped initialization is not recovered')


def _integer(data, key):
    value = data[key]
    if not isinstance(value, int) or isinstance(value, bool) or not -(1<<31)<=value<(1<<31):
        raise ValueError('Original controller requires an Int32 field: '+key)
    return value


def initial_future_properties(data):
    _playing(data)
    count = _integer(data, '_animationFrameCount')
    atlas = _integer(data, '_animationTexFrameCount')
    offset = _integer(data, '_animFrameOffset')
    if count<1 or atlas<1 or not all(math.isfinite(data[key]) for key in ('_animSwitchSpeed','_accellRatio')):
        raise ValueError('Original Future frame count/settings are outside the recovered initial contract')
    # Original cctor: front=(0,0,-1); light=(1,1,0), light2=(1,-1,0).
    # Under _frontal their two normalized dot products are exactly zero.
    # UpdateGoal rounds(0*10+5) to even, clamps0..10, then uses signed Int32
    # multiplication and division-by10. Awake converts that integer directly.
    product = (5*(count-1)) & 0xffffffff
    signed = product-(1<<32) if product&(1<<31) else product
    goal = math.trunc(signed/10)
    return {'_AnimFrame':f32(f32(goal)+f32(offset)), '_TiltDot':0.0}


def initial_ancient_properties(data, settings):
    _playing(data)
    values = [value for key,value in data.items() if not key.startswith('m_') and isinstance(value,(int,float))]
    values += list(data['_scrolls'])
    values += [value for key in ('_shakeAIntensity','_shakeAFrequency','_shakeBIntensity','_shakeBFrequency')
               for value in data[key].values()]
    if not all(math.isfinite(value) for value in values) or len(data['_scrolls'])<6 or data['_goalThreshold']<=0:
        raise ValueError('Original Ancient initial threshold/scroll/settings are outside the recovered contract')
    # Initialize zeros six StrataData records and six remapped faults. Frontal
    # UpdateTilt has goal0; UpdateStrataFault therefore takes abs(0)<threshold,
    # bypassing Random.Range and all movement curves. No random seed is chosen.
    # ShakeIntensity.Evaluate(0) is the original final shake multiplier. Only
    # its zero branch is recovered: the preceding finite noise becomes unused.
    if evaluate_initial_curve(settings['ShakeIntensity'])!=0:
        raise ValueError('Original Ancient initial nonzero shake multiplier is not recovered')
    return {'_StrataFaults':[0.0]*6, '_Shake':[0.0,0.0,0.0,0.0]}


def _pointer(resources, owner, pointer, relative):
    if pointer['m_FileID']==0:
        if not pointer['m_PathID']:
            raise ValueError('Original initialization reference is empty')
        return owner.assets_file.objects[pointer['m_PathID']], relative
    return resources.resolve(owner,pointer)


def _empty_ancient_particles(resources, original, data, relative):
    audit = []
    for field in ('_sandParticleSystems','_sand2ParticleSystems'):
        if len(data[field])!=4:
            raise ValueError('Original Ancient sand list differs from native SandNum4')
        for pointer in data[field]:
            particle,file = _pointer(resources,original,pointer,relative)
            if particle.type.name!='ParticleSystem':
                raise ValueError('Original Ancient sand reference is not a ParticleSystem')
            tree = particle.read_typetree()
            emission = tree['EmissionModule']
            rates = [emission['rateOverTime'],emission['rateOverDistance']]
            if (tree['prewarm'] or emission['m_BurstCount'] or emission['m_Bursts']
                    or any(rate['minMaxState']!=0 or rate['scalar']!=0 for rate in rates)):
                raise ValueError('Original Ancient nonempty initial particles are not recovered')
            audit.append({'source':resources.source(particle,file),'prewarm':False,'bursts':0,
                'constant_emission_rates':[0.0,0.0],
                'scope':'Fresh component, no prewarm/bursts and zero source rates; no particle geometry synthesized.'})
    return audit


def renderer_overrides(root, scene_dir, scene, materials):
    candidates = [(obj,component) for obj in scene['objects'] if obj.get('active_in_hierarchy',True)
        for component in obj.get('serialized_components',[]) if component.get('data',{}).get('m_Enabled',True)
        and (('_animationTexFrameCount' in component.get('data',{}) and '_animSwitchSpeed' in component['data'])
             or ('_animCurveSettings' in component.get('data',{}) and '_scrolls' in component['data']))]
    if not candidates:
        return {},[]
    root = Path(root).resolve()
    validate_source(str(root))
    from .localization import _resources
    resources = _resources(str(root))
    shaders = {shader['id']:shader for shader in scene['shaders']}
    parents = {obj['transform_id']:obj.get('parent_transform_id') for obj in scene['objects']}
    blocks, audit = {},[]
    for owner,component in candidates:
        _frontal(scene,owner)
        relative = owner['source']['bundle']
        original = next(obj for obj in resources.load(relative).objects
            if obj.assets_file.name.lower()+'__'+str(obj.path_id)==component['id'])
        data = original.read_typetree()
        script,_ = resources.resolve(original,data['m_Script'])
        script_data = script.read_typetree()
        name = script_data['m_ClassName']
        if script_data['m_Namespace']!='Lettuce.Infrastructure.Card.Core':
            raise ValueError('Unknown original Ancient/Future controller namespace')
        extra = {}
        if name=='CardFutureObject':
            properties,tag = initial_future_properties(data),'Parallax-Led'
        elif name=='CardAncientObject':
            settings,file = _pointer(resources,original,data['_animCurveSettings'],relative)
            settings_script,_ = resources.resolve(settings,settings.read_typetree()['m_Script'])
            if settings_script.read_typetree()['m_ClassName']!='AncientBGAnimationSettings':
                raise ValueError('Unknown original Ancient settings class')
            properties = initial_ancient_properties(data,settings.read_typetree())
            tag = 'Parallax-Strata'
            extra = {'settings':resources.source(settings,file),
                'initial_particles':_empty_ancient_particles(resources,original,data,relative),
                'rng':'Not sampled: frontal goal0 takes the native abs(goal-fault)<positive threshold branch.'}
        else:
            raise ValueError('Unknown original Ancient/Future initialization component '+name)
        for obj in scene['objects']:
            renderer = obj.get('renderer')
            if not renderer or not obj.get('active_in_hierarchy',True) or not renderer.get('enabled',True):
                continue
            transform = obj['transform_id']
            while transform is not None and transform!=owner['transform_id']:
                transform = parents.get(transform)
            if transform is None:
                continue
            selected = False
            for mid in renderer['material_slots']:
                shader = shaders[materials[mid]['shader_id']]
                parsed = json.loads((Path(scene_dir)/shader['parsed']).read_text(encoding='utf8'))
                selected |= any(key=='SearchTag' and value==tag for sub in parsed['m_SubShaders']
                    for pa in sub['m_Passes'] for key,value in pa['m_State']['m_Tags']['tags'])
            if not selected:
                continue
            for slot in range(len(renderer['material_slots'])):
                key = (obj['id'],slot)
                if key in blocks:
                    raise ValueError('Overlapping original Ancient/Future property blocks require execution order')
                blocks[key] = dict(properties)
                audit.append({'object':obj['id'],'slot':slot,'component':component['id'],
                    'component_class':name,'source':owner['source'],'search_tag':tag,
                    'property_block':dict(properties),'frame':'Fresh Awake frontal initialization, time0/delta0',
                    'native_evidence':'reports/additional-scene-families-20261022/',**extra})
    return blocks,audit
