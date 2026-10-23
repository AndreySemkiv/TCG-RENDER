"""Source-bound MRR initialization at the original EvaluateAnim(0) progress.

Static native evidence: reports/mrr-initialization-evidence-20261022/evidence.json.
Weighted inversion follows libunity.so 0xc9d42c with float32 intermediate
rounding. Host libm calls are rounded to float32 as the ARM64 calls are;
cross-libm bit identity is unverified. Only source initialization progress=0
is provided: this is not a complete animation/tilt/camera runtime.
"""
from bisect import bisect_right
from pathlib import Path
from functools import lru_cache
import hashlib
import math
import struct


@lru_cache(maxsize=4)
def validate_native_source(root):
    expected = {'libil2cpp.so': 'eefba3938aa7bc1751135abb56b82f12d46c290c0a598b59e459d6f1a15c4d5c',
                'libunity.so': 'e6f33ccdad2fe323785c673ef8a5b6850f4a12100fa04c284c3f0264980b5bd9'}
    for name, digest in expected.items():
        path = Path(root) / 'input/native-code' / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('MRR initialization native source differs from inspected original: ' + name)


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _cuberoot_log(value):
    # libunity: logf(abs(value)), promote to double, divide by 3, exp(double),
    # cast to float; preserve the sign after the double exponential.
    if value == 0:
        return 0.0
    logarithm = f32(math.log(abs(value)))
    result = f32(math.exp(logarithm / 3.0))
    return -result if value < 0 else result


def invert_bezier_x(t, x1, x2):
    """Reconstruct libunity ARM64 float solver at VA 0xc9d42c."""
    t, x1, x2 = map(f32, (t, x1, x2))
    c = f32(x1 * 3)
    thrice_x2 = f32(x2 * 3)
    a = f32(f32(c - thrice_x2) + 1)
    b = f32(thrice_x2 + f32(x1 * -6))
    threshold = struct.unpack('<f', struct.pack('<I', 0x3a83126f))[0]
    fallback = 0.0 if t < 0.5 else 1.0
    if abs(a) > threshold:
        three_a = f32(a * 3)
        shift = f32(-b / three_a)
        denominator = f32(f32(a * 6) * a)
        qb = f32(f32(c * b) + f32(three_a * t))
        qterm = f32(qb / denominator)
        pterm = f32(c / three_a)
        shift2 = f32(shift * shift)
        q = f32(f32(shift * shift2) + qterm)
        p = f32(pterm - shift2)
        q2 = f32(q * q)
        p3 = f32(p * f32(p * p))
        discriminant = f32(q2 + p3)
        if discriminant < 0:
            angle = f32(math.atan2(f32(math.sqrt(-discriminant)), q))
            radius = f32(math.sqrt(f32(q2 - discriminant)))
            radius = _cuberoot_log(radius)
            twice_radius = f32(radius + radius)
            angle = f32(angle / 3)
            root = f32(shift + f32(twice_radius * f32(math.cos(angle))))
            negative_turn = struct.unpack('<f', struct.pack('<I', 0xc0060a92))[0]
            positive_turn = -negative_turn
            second_cosine = f32(math.cos(f32(angle + negative_turn)))
            if 0 <= root <= 1:
                return root
            root = f32(shift + f32(twice_radius * f32(math.cos(f32(angle + positive_turn)))))
            if 0 <= root <= 1:
                return root
            root = f32(shift + f32(twice_radius * second_cosine))
            return root if 0 <= root <= 1 else fallback
        square_root = f32(math.sqrt(discriminant))
        root_a = _cuberoot_log(f32(q + square_root))
        root_b = _cuberoot_log(f32(q - square_root))
        root = f32(shift + f32(root_a + root_b))
        return root if 0 <= root <= 1 else fallback
    if abs(b) > threshold:
        square_root = f32(math.sqrt(f32(f32(c * c) + f32(f32(b * 4) * t))))
        two_b = f32(b + b)
        root = f32(f32(-c - square_root) / two_b)
        if 0 <= root <= 1:
            return root
        root = f32(f32(square_root - c) / two_b)
        return root if 0 <= root <= 1 else fallback
    return f32(t / c) if abs(c) > threshold else 0.0


def evaluate_initial_curve(curve):
    """Evaluate source AnimationCurve at t=0, rejecting other infinity modes."""
    keys = curve['m_Curve']
    if not keys:
        return 0.0
    if len(keys) == 1:
        return keys[0]['value']
    if 0 <= keys[0]['time']:
        if curve['m_PreInfinity'] != 2:
            raise ValueError('MRR initial curve needs unrecovered pre-infinity mode')
        return keys[0]['value']
    if 0 >= keys[-1]['time']:
        if curve['m_PostInfinity'] != 2:
            raise ValueError('MRR initial curve needs unrecovered post-infinity mode')
        return keys[-1]['value']
    index = bisect_right([k['time'] for k in keys], 0.0) - 1
    left, right = keys[index:index + 2]
    if math.isinf(left['outSlope']) or math.isinf(right['inSlope']):
        return left['value'] if max(left['outSlope'], right['inSlope']) == math.inf else right['value']
    if not (left['weightedMode'] & 2 or right['weightedMode'] & 1):
        # libunity caches an unweighted polynomial. No observed MRR initial
        # interior segment takes that route; fail until its float path is added.
        raise ValueError('MRR initial interior unweighted segment is not recovered')
    dt = f32(right['time'] - left['time'])
    if dt == 0:
        return left['value']
    weight_out = left['outWeight'] if left['weightedMode'] & 2 else f32(1 / 3)
    weight_in = right['inWeight'] if right['weightedMode'] & 1 else f32(1 / 3)
    normalized_t = f32(f32(-left['time']) / dt)
    u = invert_bezier_x(normalized_t, weight_out, f32(1 - weight_in))
    one_u = f32(1 - u)
    slope_out = f32(dt * left['outSlope'])
    slope_in = f32(dt * right['inSlope'])
    control_1 = f32(left['value'] + f32(weight_out * slope_out))
    control_2 = f32(right['value'] - f32(weight_in * slope_in))
    u2 = f32(u * u)
    one_u2 = f32(one_u * one_u)
    left_value = f32(left['value'] * f32(one_u * one_u2))
    term_1 = f32(control_1 * f32(f32(u * 3) * one_u2))
    term_2 = f32(control_2 * f32(one_u * f32(u2 * 3)))
    right_value = f32(right['value'] * f32(u * u2))
    return f32(right_value + f32(term_2 + f32(left_value + term_1)))


def initial_properties(settings, component):
    values = {k: evaluate_initial_curve(v) for k, v in settings.items()
              if isinstance(v, dict) and 'm_Curve' in v}
    main = {'_ChangeColor': values['ChangeColorCurve'], '_LightColorIntensity': values['LightColorIntensityCurve'],
            '_LightEmitIntensity': values['LightEmitIntensityCurve'], '_LightPower': values['LightPower'],
            '_Layer2UVTranslate': [values['Layer2UVXTranslateByTiltingLeft'], 0, 0, 0],
            '_Layer2ColorPower': values['Layer2ColorPower'],
            '_Layer2EmissiveIntensity': values['Layer2EmissiveIntensity']}
    effect = {'_Switch': values['EffSwitchColor'], '_AdditiveIntensity': values['EffAdditiveIntensity'],
              '_Color3Blend': values['EffColor3Blend'], '_EmissiveIntensity': values['EffEmissiveIntensity']}
    offset = f32(component['_flashRadialStartOffset'])
    radial_keys = settings['FlashRadialScaling']['m_Curve']
    first, last = radial_keys[0]['value'], radial_keys[-1]['value']
    span = f32(last - first)
    if span == 0:
        raise ValueError('Original MRR radial-scale expression divides by zero')
    # Preserve ARM64 operation order, including its divide instead of reducing
    # the expression to a mathematically equivalent linear interpolation.
    scale = f32(f32(f32(span * f32(1 - offset)) * f32(values['FlashRadialScaling'] - first)) / span)
    scale = f32(f32(first + f32(offset * span)) + scale)
    radial_anim = f32(offset + f32(f32(1 - offset) * values['FlashRadialAnim']))
    flash = {'_FlashIntensity': values['FlashIntensity'], '_RadialScaling': scale, '_RadialAnim': radial_anim}
    return {'MRR-ChangeColor-Lighting': main, 'Frame-Holo-2Layer': main,
            'Card-Effect-Emit': effect, 'MRR-Parallax-Flash': flash}


def load_component(root, scene, component):
    """Resolve MonoScript identity before interpreting similarly named fields."""
    from pocket_renderer.localization import _resources
    validate_native_source(str(Path(root).resolve()))
    resources = _resources(str(Path(root).resolve()))
    cab, pid = component['id'].rsplit('__', 1)
    owner = next(obj for obj in resources.load(scene['prefab']).objects
                 if obj.path_id == int(pid) and obj.assets_file.name.lower() == cab)
    data = owner.read_typetree()
    script, _ = resources.resolve(owner, data['m_Script'])
    identity = script.read_typetree()
    return resources, owner, data, identity


def load_settings(root, scene, component):
    """Resolve the actual component and external settings PPtr from originals."""
    resources, owner, data, identity = load_component(root, scene, component)
    if identity['m_ClassName'] != 'CardMRRObject':
        raise ValueError('MRR initialization component class differs from inspected source')
    settings, relative = resources.resolve(owner, data['_settings'])
    settings_script, _ = resources.resolve(settings, settings.read_typetree()['m_Script'])
    if settings_script.read_typetree()['m_ClassName'] != 'MRRAnimationSettings':
        raise ValueError('MRR initialization settings class differs from inspected source')
    return settings.read_typetree(), data, resources.source(settings, relative)


def renderer_overrides(root, scene_dir, scene, materials):
    """Return per-object property blocks, limited to each enabled MRR subtree."""
    import json
    if not any('_animStartDegree' in c.get('data', {}) and '_settings' in c.get('data', {})
               and c['data'].get('m_Enabled', True) and o.get('active_in_hierarchy', True)
               for o in scene['objects'] for c in o.get('serialized_components', [])):
        return {}, []
    shaders = {s['id']: s for s in scene['shaders']}
    parents = {o['transform_id']: o.get('parent_transform_id') for o in scene['objects']}
    result, provenance = {}, []
    for owner in scene['objects']:
        if not owner.get('active_in_hierarchy', True):
            continue
        for component in owner.get('serialized_components', []):
            data = component.get('data', {})
            if '_animStartDegree' not in data or '_settings' not in data or not data.get('m_Enabled', True):
                continue
            _, _, _, identity = load_component(root, scene, component)
            if (identity.get('m_Namespace') == 'Lettuce.Infrastructure.Card.Core'
                    and identity['m_ClassName'] in ('CardMSRObject', 'CardMSSRObject', 'CardMReliefSRObject')):
                # These are distinct native controllers, handled by the Mega
                # alternate-art initializer. Never cast them as CardMRRObject.
                continue
            settings, original, source = load_settings(root, scene, component)
            by_tag = initial_properties(settings, original)
            for obj in scene['objects']:
                renderer = obj.get('renderer')
                if not renderer or not obj.get('active_in_hierarchy', True):
                    continue
                transform = obj['transform_id']
                while transform is not None and transform != owner['transform_id']:
                    transform = parents.get(transform)
                if transform is None:
                    continue
                tags = set()
                for mid in renderer['material_slots']:
                    shader = shaders[materials[mid]['shader_id']]
                    parsed = json.loads((Path(scene_dir) / shader['parsed']).read_text(encoding='utf8'))
                    for sub in parsed['m_SubShaders']:
                        for pa in sub['m_Passes']:
                            tags.update(v for k, v in pa['m_State']['m_Tags']['tags'] if k == 'SearchTag' and v in by_tag)
                if tags:
                    if obj['id'] in result:
                        raise ValueError('Overlapping MRR initial property blocks require original execution order')
                    values = {}
                    for tag in by_tag:
                        if tag in tags:
                            values.update(by_tag[tag])
                    result[obj['id']] = values
                    provenance.append({'object': obj['id'], 'component': component['id'], 'settings': source,
                        'progress': 0, 'search_tags': sorted(tags), 'property_block': values,
                        'scope': 'All material slots on this renderer, as CardMRRObject.ApplyParams',
                        'float_evaluator': 'Original float32 solver structure; host libm cross-platform bit identity unverified'})
    return result, provenance
