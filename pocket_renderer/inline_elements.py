"""Original Pokesymbol elements from native 0x4A20390; no raster resizing."""
import re
from pathlib import Path
from .japanese_fonts import load_font_set

FONT_ID=1866416259400829261
MATERIAL_IDS={1:-5102697118585943731,2:2417365139982267753,3:1470737543190596764}
# Destinations of exact native string comparisons in rich-text-native/element.asm.
GLYPHS={'Grass':'\ue008','Fire':'\ue009','Water':'\ue00a','Lightning':'\ue00b',
    'Psychic':'\ue00c','Fighting':'\ue00d','Colorless':'\ue00e','Darkness':'\ue00f',
    'Metal':'\ue010','Dragon':'\ue007','Fairy':'\ue005'}
# Exact alternatives from the shipped string-hash dispatch, not case folding.
# Fire is R; F is Fighting. Dragon is N, avoiding Darkness's D.
# Static branch/literal proof: reports/text-controls-20261022/element-aliases.json.
ELEMENT_VALUES={alias:name for name,letter in (
    ('Grass','G'),('Fire','R'),('Water','W'),('Lightning','L'),('Psychic','P'),
    ('Fighting','F'),('Darkness','D'),('Metal','M'),('Colorless','C'),('Dragon','N'),('Fairy','Y'))
    for alias in (name,name.lower(),letter,letter.lower())}
TAG=re.compile(r'\[Img:Element\s*([^\[\]]*)\]')
ATTR=re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')


def bind_inline_elements(root,resources,text,fonts,group,group_source,output):
    matches=list(TAG.finditer(text['text']))
    if not matches:return
    from .localization import UnsupportedLayout
    if 'pokesymbol' not in fonts:
        fonts.update(load_font_set(root,resources,Path(output)/'original-fonts/inline',
            {'pokesymbol':FONT_ID},manifest_name='inline-fonts.json'))
    variant=group.get('_imgTagFontType')
    if variant not in MATERIAL_IDS:raise UnsupportedLayout('Original element material type is not recovered',variant=variant)
    objects={o.path_id:o for o in resources.load('Common/Font_bundles').objects}
    material_obj=objects[MATERIAL_IDS[variant]];material=material_obj.read_typetree()
    sizes=[c for c in text['companion_components'] if '_elementTagFontSize' in c['data']]
    if len(sizes)!=1:raise UnsupportedLayout('Original element size component is ambiguous',text_id=text['id'])
    size=sizes[0]['data']['_elementTagFontSize'] or 23.0
    props=material['m_SavedProperties'];colors=dict(props['m_Colors'])
    style={'font_id':'pokesymbol','font_file':fonts['pokesymbol']['original_font_file'],
        'original_font_file':fonts['pokesymbol']['original_font_file'],'font_size':size,
        'color_rgba':[colors['_FaceColor'][c] for c in ('r','g','b','a')],
        'material_overrides':{'floats':dict(props['m_Floats']),'colors':colors,'keywords':material['m_ValidKeywords'],
            'material_id':material_obj.path_id,'name':material['m_Name'],'source':resources.source(material_obj,'Common/Font_bundles')},
        'font_provenance':{'font':resources.source(objects[FONT_ID],'Common/Font_bundles'),
            'material':resources.source(material_obj,'Common/Font_bundles'),'img_tag_font_type':variant,
            'group':group_source,'size_component':sizes[0],'native_method':'0x4A20390',
            'source_command':'<size={0}><font="{1}" material="{2}">GLYPH</font></size>'}}
    text.setdefault('rich_styles',{})['element']=style
    text['inline_elements']={}
    parameters=text.get('content_provenance',{}).get('original_parameters',[])
    for match in matches:
        attrs=dict(ATTR.findall(match[1]))
        if set(attrs)-{'id','name'}:raise UnsupportedLayout('Original element attributes are not recovered',attributes=attrs)
        if 'name' in attrs:value=attrs['name']
        else:
            try:value=parameters[int(attrs.get('id','0'))]
            except (ValueError,IndexError):raise UnsupportedLayout('Original element parameter is unavailable',attributes=attrs)
        canonical=ELEMENT_VALUES.get(value)
        if canonical is None:raise UnsupportedLayout('Original element value is not recovered',value=value)
        text['inline_elements'][match[0]]={'character':GLYPHS[canonical],'style':'element',
            'original_value':value,'canonical_value':canonical,'native_method':'0x4A20390'}
