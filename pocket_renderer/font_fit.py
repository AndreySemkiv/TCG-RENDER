"""Bounded original CardTextConfig/TMP single-line width condensation.

Native CardTextConfig.Apply (0x4810E84) / SetCondensedInfo (0x4810DD0)
activate TMP autosizing with 50% character-width adjustment, the selected
language point size as maximum, and one tenth of that size as minimum.

Unity's 6000.0 TMP source applies width adjustment before reducing point size:
https://raw.githubusercontent.com/Unity-Technologies/uGUI/6000.0/com.unity.ugui/Runtime/TMP/TextMeshProUGUI.cs
Lines 3490-3522 and 3764. Complete TMP font sizing/kerning is not executed here.
"""
from copy import deepcopy
from .text_layout import measure_text
from .localization import UnsupportedLayout


def fit_single_line(text,font,width,fonts=None):
    """Mutate a plain title only when original AutoCondensed permits this fit.

    Glyph vertices and their original spacing are condensed; source atlas pixels
    remain untouched. Requests that need point-size reduction or richer layout
    remain explicit unsupported cases rather than guessed substitutions.
    """
    if width<=0:raise UnsupportedLayout('Original title has no available width',text_id=text['id'])
    import re
    plain=re.sub(r'<size=[+]?(?:\d+(?:\.\d*)?|\.\d+)%>|</size>','',text['text'],flags=re.I)
    if text.get('rich_styles') or text.get('inline_ex') or '\n' in plain or '<' in plain or '[' in plain:
        raise UnsupportedLayout('Mixed/multiline title condensation is not recovered',text_id=text['id'])
    configs=[c for c in text.get('companion_components',[]) if '_shrinkSetting' in c['data'] and '_parameters' in c['data']]
    if len(configs)!=1 or configs[0]['data']['_shrinkSetting']!=0:
        raise UnsupportedLayout('Original title does not enable recovered AutoCondensed mode',text_id=text['id'])
    source=text.get('auto_condense_source_input')
    if source is None:
        source={k:deepcopy(text.get(k)) for k in ('font_size','character_spacing_tmp_units','horizontal_scale')}
        source['horizontal_scale']=source['horizontal_scale'] or 1.0
    probe=deepcopy(text);probe['font_size']=source['font_size'];probe['horizontal_scale']=source['horizontal_scale']
    probe['character_width_scale']=1.0
    probe['character_spacing_tmp_units']=source['character_spacing_tmp_units'];probe['fit_font']=False;probe['wrap']=False
    measured=measure_text(probe,font,fonts=fonts)
    original_width=measured['width']
    # The native parameter is exactly 50.0f; this is not a chosen visual limit.
    maximum_adjustment=.5
    scale=min(1.0,(width-.0001)/original_width) if original_width else 1.0
    if scale<1-maximum_adjustment:
        raise UnsupportedLayout('Original title needs additional TMP point-size fitting beyond recovered condensation',
            text_id=text['id'],original_width=original_width,available_width=width,
            required_scale=scale,minimum_original_horizontal_scale=1-maximum_adjustment)
    text.update(font_size=source['font_size'],horizontal_scale=source['horizontal_scale'],character_width_scale=scale,
        character_spacing_tmp_units=source['character_spacing_tmp_units'],fit_font=False,
        auto_condense_source_input=source)
    text['auto_condense_audit']={'source_component':configs[0],
        'native_methods':{'CardTextConfig.Apply':'0x4810E84','CardTextConfig.SetCondensedInfo':'0x4810DD0'},
        'point_size':source['font_size'],'runtime_point_size_min':source['font_size']*.1,
        'runtime_point_size_max':source['font_size'],'maximum_character_width_adjustment_percent':50,
        'original_width':original_width,'available_width':width,'horizontal_factor':scale,
        'source_atlas_resized':False,'point_size_changed':False,
        'implementation_scope':'Plain one-line original metrics, width condensation only. Complete TMP layout/kerning/point-size search is not executed.',
        'primary_reference':'https://raw.githubusercontent.com/Unity-Technologies/uGUI/6000.0/com.unity.ugui/Runtime/TMP/TextMeshProUGUI.cs'}
    return measure_text(text,font,fonts=fonts)
