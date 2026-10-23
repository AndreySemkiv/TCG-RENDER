"""Recovered PokemonCardUIBehaviour skill line allocation and text fitting.

ARM64 1.7.5: UpdateSkillText 0x47F8F88, ClampLineCount 0x47F98C8,
GetScaleText 0x47ED7DC. The controller budgets six description lines, reduces
horizontal scale by the original -0.05 double, then point size by 1. It does
not shrink an entire card or its source atlas to make a rectangle look right.
"""
from copy import deepcopy
import math
import struct

from .localization import UnsupportedLayout


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def clamp_line_counts(base, maximum=6):
    """Preserve native first-index tie breaking and single precision ratios."""
    if any(n < 0 for n in base):
        raise UnsupportedLayout('Original skill line count must be nonnegative')
    result = list(base)
    for _ in range(max(0, sum(base)-maximum)):
        ratios = [_f32((n-1)/original) if original else -math.inf
                  for n, original in zip(result, base)]
        index = ratios.index(max(ratios))
        result[index] -= 1
    return result


def vertical_group_heights(minimums, preferred, spacing, available):
    """uGUI SetChildrenAlongAxis with no flexible/scale/force-expand children.

    Original source: HorizontalOrVerticalLayoutGroup.cs 6000.0, lines159-191;
    installed ARM64 method0x6D58D24. Allocated rectangles may be shorter than
    preferred text, as permitted by the selected TMP Overflow mode.
    """
    total_min=sum(minimums)+spacing*(len(preferred)-1)
    total_preferred=sum(preferred)+spacing*(len(preferred)-1)
    if total_min > available:
        raise UnsupportedLayout('Original skill minimum layout exceeds recovered container',minimum=total_min,available=available)
    ratio=(min(1.,max(0.,(available-total_min)/(total_preferred-total_min)))
           if total_preferred!=total_min else 0.)
    assigned=[minimum+(wanted-minimum)*ratio for minimum,wanted in zip(minimums,preferred)]
    return assigned, {'minimum_height':total_min,'preferred_height':total_preferred,
        'available_height':available,'min_max_lerp':ratio,'assigned_group_heights':assigned[:],
        'native_method':'0x6D58D24','text_point_size_changed_by_vertical_layout':False}


def restore_description(field):
    """Layout runs before and after dynamic glyph population; fitting is idempotent."""
    original = field.get('native_skill_source_input')
    if original is None:
        original = {k:deepcopy(field[k]) for k in ('text', 'font_size')}
        field['native_skill_source_input'] = original
    field.update(deepcopy(original))
    field.pop('native_skill_fit_audit', None)


def fit_description(field, font, maximum, measure):
    """Use the selected serialized CardTextAutoSizeCondition, retaining nested sizes."""
    components = [c for c in field['companion_components'] if '_autoSizeConditions' in c['data']]
    if len(components) != 1:
        raise UnsupportedLayout('Original skill auto-size controller is ambiguous', text_id=field['id'])
    mask = field['font_size_source']['language_mask']
    conditions = [v for v in components[0]['data']['_autoSizeConditions'] if v['_language'] & mask]
    # Native GetAutoSizeCondition returns the first matching condition.
    if not conditions:
        return measure(field, font, 628)
    condition = conditions[0]
    min_scale = condition['_autoMinScale']; min_size = condition['_autoMinSize']
    if not (0 < min_scale <= 1 and 0 < min_size <= field['font_size']):
        raise UnsupportedLayout('Original skill auto-size bounds are outside recovered contract', condition=condition)
    source = field['native_skill_source_input']; scale=1.; size=source['font_size']; steps=[]
    def layout():
        field['font_size'] = size
        field['text'] = source['text'] if scale == 1 else '<scale='+format(scale,'.17g')+'>'+source['text']+'</scale>'
        result=measure(field, font, 628)
        steps.append({'horizontal_scale':scale, 'point_size':size, 'lines':len(result['lines'])})
        return result
    result=layout()
    while len(result['lines']) > maximum and scale > min_scale:
        scale=max(scale-.05, min_scale)
        result=layout()
    while len(result['lines']) > maximum and size > min_size:
        size=max(_f32(size-1), min_size)
        result=layout()
    field['native_skill_fit_audit'] = {
        'native_method':'0x47ED7DC', 'line_budget':maximum,
        'condition_component_id':components[0]['id'], 'selected_condition':deepcopy(condition),
        'source_text':source['text'], 'source_point_size':source['font_size'], 'steps':steps,
        'horizontal_scale':scale, 'point_size':size, 'final_lines':len(result['lines']),
        'original_nested_inline_point_sizes_retained':True, 'source_atlas_resized':False,
        'scope':'Recovered controller over bounded TMP metrics; complete TMP runtime is not executed.'}
    if len(result['lines']) > maximum:
        raise UnsupportedLayout('Original skill reaches serialized auto-size bounds before its line budget',
                                text_id=field['id'], audit=field['native_skill_fit_audit'])
    return result
