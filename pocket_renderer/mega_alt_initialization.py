"""Source-bound Awake/LateUpdate frame for Mega SR, SSR and relief SR.

Original managed fields, native method bodies and source settings are retained
in reports/mega-alt-initialization-20261022/evidence.json. Frontal Z rotations
and an explicitly static first frame are supported; later animation is separate.
"""
import json
import math
from pathlib import Path
from .mrr_initialization import load_component,evaluate_initial_curve,f32
from .uv_shadow_initialization import frontal_z_rotation,initial_shadow_properties

CLASSES={'CardMSRObject':'MSRAnimationSettings',
         'CardMSSRObject':'MSSRAnimationSettings',
         'CardMReliefSRObject':'ReliefMSRAnimationSettings'}


def initial_properties(class_name,settings,data,rotation=(0.0,1.0)):
    def curve(name):
        value=f32(evaluate_initial_curve(settings[name]))
        if not math.isfinite(value):raise ValueError('Nonfinite original Mega initial curve: '+name)
        return value
    if not math.isfinite(data['_animStartDegree']):raise ValueError('Nonfinite original Mega start angle')
    effect=lambda:{'_Switch':curve('EffSwitchColor'),'_AdditiveIntensity':curve('EffAdditiveIntensity'),
        '_Color3Blend':curve('EffColor3Blend'),'_EmissiveIntensity':curve('EffEmissiveIntensity')}
    if class_name=='CardMSRObject':
        offset=data['_timeOffset']
        if not math.isfinite(offset):raise ValueError('Nonfinite original Mega time offset')
        # Awake resets AnimState/ReflectPDState to Idle and their progress and
        # speed fields to zero. LateUpdate at deltaTime=0 cannot cross the
        # animation's start-angle threshold. The idle reflection branch does
        # not call PerlinNoise, even when its configured strength is nonzero.
        stopped=bool(data['_isAnimationStopped'])
        return {'Card-Aura':{'_Transparency':curve('AuraTransparency'),'_TimeParam':f32(offset)},
            'Card_Parallax_Transparent_Translate':{'_Transparency':0.0 if stopped else curve('ParallaxTransparency'),
                '_Translate':0.0 if stopped else curve('ParallaxTranslate')},
            'ShadowBox_MSR':{'_ReflectCenterX':curve('OutlineReflectCenterX'),
                '_ReflectIntensity':curve('OutlineReflectColorIntensity'),'_FlipAnim':0.0,'_FlipBlend':0.0}}
    if class_name=='CardMSSRObject':return {'Card-Effect-Emit':effect()}
    if class_name=='CardMReliefSRObject':
        shadow=initial_shadow_properties(data,rotation)
        shadow.update(_Switch=curve('EffSwitchColor'),_ShadowDecay=curve('ShadowDecayCurve'))
        relief_effect=effect()
        # This controller's native cctor binds EffColor3Blend to _FlashBlend,
        # while CardMSSRObject binds the same settings label to _Color3Blend.
        relief_effect['_FlashBlend']=relief_effect.pop('_Color3Blend')
        return {'MRR-ChangeColor-Lighting':{'_ChangeColor':curve('ChangeColorCurve'),
                '_LightColorIntensity':curve('LightColorIntensityCurve'),
                '_LightEmitIntensity':curve('LightEmitIntensityCurve'),'_LightPower':curve('LightPower')},
            'Card-UVShadow':shadow,'Card-Effect-Emit':relief_effect}
    raise ValueError('Unknown Mega alternate-art initialization class: '+class_name)


def renderer_overrides(root,scene_dir,scene,materials):
    candidates=[(o,c) for o in scene['objects'] if o.get('active_in_hierarchy',True)
        for c in o.get('serialized_components',[]) if c.get('data',{}).get('m_Enabled',True)
        and '_animStartDegree' in c.get('data',{}) and '_settings' in c.get('data',{})]
    if not candidates:return {},[]
    result={};audit=[];shaders={s['id']:s for s in scene['shaders']}
    parents={o['transform_id']:o.get('parent_transform_id') for o in scene['objects']}
    for owner,component in candidates:
        resources,original,data,identity=load_component(root,scene,component)
        cls=identity['m_ClassName']
        if cls not in CLASSES:continue # CardMRRObject/unknown classes are checked by the MRR dispatcher.
        if identity.get('m_Namespace')!='Lettuce.Infrastructure.Card.Core':raise ValueError('Mega controller namespace differs')
        rotation=frontal_z_rotation(scene,owner)
        settings,relative=resources.resolve(original,data['_settings'])
        raw=settings.read_typetree();script,_=resources.resolve(settings,raw['m_Script'])
        if script.read_typetree()['m_ClassName']!=CLASSES[cls]:raise ValueError('Mega settings class differs from native controller')
        properties=initial_properties(cls,raw,data,rotation)
        for obj in scene['objects']:
            renderer=obj.get('renderer')
            if not renderer or not obj.get('active_in_hierarchy',True):continue
            transform=obj['transform_id']
            while transform is not None and transform!=owner['transform_id']:transform=parents.get(transform)
            if transform is None:continue
            tags=set()
            for mid in renderer['material_slots']:
                parsed=json.loads((Path(scene_dir)/shaders[materials[mid]['shader_id']]['parsed']).read_text(encoding='utf8'))
                tags.update(v for sub in parsed['m_SubShaders'] for p in sub['m_Passes']
                    for k,v in p['m_State']['m_Tags']['tags'] if k=='SearchTag' and v in properties)
            if not tags:continue
            if obj['id'] in result:raise ValueError('Overlapping Mega alternate controllers need original execution order')
            values={}
            for tag in properties:
                if tag in tags:values.update(properties[tag])
            result[obj['id']]=values
            audit.append(dict(object=obj['id'],component=component['id'],component_class=cls,
                settings=resources.source(settings,relative),search_tags=sorted(tags),property_block=values,
                progress=0,delta_time=0,scope='All material slots on source SearchTag-selected renderers',
                frame='Original Awake invokes LateUpdate, frontal source transforms, declared static first frame',
                native_evidence='reports/mega-alt-initialization-20261022/evidence.json',
                limits='No later animation, tilt interaction or live game clock/camera equivalence.'))
    return result,audit
