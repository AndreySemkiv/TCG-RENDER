"""Original owner-name flex layout and Pokémon category Images.

No display names or collection/card allowlist is supplied by this module. The
native flex selector is decoded from the selected, inspected binary/metadata;
localized names and category flags come from the selected original master.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib
import re
import struct

from .localization import LANGUAGES, UnsupportedLayout
from .skills import _field, _path


@lru_cache(maxsize=4)
def native_name_contract(root):
    """Read cctor literals from ARM64 1.7.5; never infer future native rules."""
    root=Path(root)
    paths=[root/'input/native-code/libil2cpp.so',
        root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat']
    expected=['eefba3938aa7bc1751135abb56b82f12d46c290c0a598b59e459d6f1a15c4d5c',
        '905b9505a935828ac7174b7086d15e23640dcf61451e604e0085ae98e06c8618']
    data=[]
    for path,digest in zip(paths,expected):
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise UnsupportedLayout('Owner-name native source differs from inspected original',file=str(path))
        data.append(raw)
    binary,metadata=data
    start=struct.unpack_from('<Q',binary,32)[0];size,count=struct.unpack_from('<HH',binary,54)
    segments=[struct.unpack_from('<IIQQQQQQ',binary,start+i*size) for i in range(count)]
    def offset(address):
        segment=next(s for s in segments if s[0]==1 and s[3]<=address<s[3]+s[5])
        return segment[2]+address-segment[3]
    start=struct.unpack_from('<Q',binary,40)[0];size,count=struct.unpack_from('<HH',binary,58)
    relocations={}
    for i in range(count):
        section=struct.unpack_from('<IIQQQQIIQQ',binary,start+i*size)
        if section[1]!=4:continue
        for pos in range(section[4],section[4]+section[5],section[9]):
            address,kind,value=struct.unpack_from('<QQq',binary,pos)
            if kind&0xffffffff==1027:relocations[address]=value
    def qword(address):
        return relocations.get(address,struct.unpack_from('<Q',binary,offset(address))[0])
    literal_offset,literal_size,text_offset,text_size=struct.unpack_from('<4I',metadata,8)
    def literal(address):
        value=qword(address)
        encoded=value if value>>29==5 else qword(value)
        if encoded>>29!=5:raise UnsupportedLayout('Original name literal is unavailable',slot=hex(address))
        index=(encoded&0x1fffffff)>>1
        if index>=literal_size//8:raise UnsupportedLayout('Original name literal index is invalid')
        length,pos=struct.unpack_from('<II',metadata,literal_offset+index*8)
        if pos+length>text_size:raise UnsupportedLayout('Original name literal bounds are invalid')
        return metadata[text_offset+pos:text_offset+pos+length].decode('utf8')
    return {'flex_card_id_fragments':[literal(address) for address in range(0x72e0a18,0x72e0a48,8)],
        'suffix_regex':literal(0x72e0a48),'split_regex':literal(0x72e0a50),
        'language_bitmask':0x12c,
        'provenance':{'native_sha256':expected[0],'metadata_sha256':expected[1],
            'methods':{'ShouldFlexLayout':'0x4812BAC','.ctor':'0x48138D8','.cctor':'0x4813A9C'},
            'fragment_literal_slots':[hex(v) for v in range(0x72e0a18,0x72e0a48,8)]}}


def split_ex_name(root,card,locale,value):
    """Native terminal removal, plus the native owner flex split when selected."""
    from .ms_references import expand_additional_names
    references=[]
    value=expand_additional_names(value,locale,references)
    contract=native_name_contract(str(Path(root).resolve()))
    if contract['suffix_regex']!=r'\s?-?ex$' or contract['split_regex']!=r'(?<=(?:\s|-)ex)(?=[^\w\s])':
        raise UnsupportedLayout('Original owner-name regex contract is not recovered')
    language=LANGUAGES[locale.language]['asset_enum']
    # CardData.Id is the original card identifier; native String.Contains tests
    # fragments, not names or a catalogue of supported cards.
    flex=bool((1<<language)&contract['language_bitmask']) and any(
        fragment in card['card_id'] for fragment in contract['flex_card_id_fragments'])
    right=''
    if flex:
        match=re.search(r'(?:\s|-)ex(?=[^\w\s])',value)
        if match:right,value=value[match.end():],value[:match.end()]
    clean=re.sub(contract['suffix_regex'],'',value)
    if clean==value:raise UnsupportedLayout('Original EX owner name suffix is not recovered',name=value)
    return clean,right,{'native_contract':contract['provenance'],'flex':flex,
        'reference_audit':references,'original_formatted_name':value+right}


def additional_name_field(resources,parent,value,locale,fonts):
    """The native UpdateText forwards the primary field's font and material."""
    views=[c for c in parent['companion_components'] if '_additionalText' in c['data']]
    if len(views)!=1:raise UnsupportedLayout('Original owner name controller is ambiguous')
    pid=views[0]['data']['_additionalText']['m_PathID']
    item=_field(resources,pid,'name_additional',value,
        {'authority':'original-native-PokemonCardNameView.SetText','virtual_address':'0x4812FE8',
            'original_formatted_right_part':value},locale,fonts,'bold')
    # The extra TMP has its own CardTextConfig. It has no CardTextView because
    # UpdateText assigns font/material from the main view at 0x4812C70/0x4812C88.
    item['companion_components'].append(deepcopy(views[0]))
    item['native_font_inheritance']={'method':'PokemonCardNameView.UpdateText',
        'virtual_address':'0x4812C50','primary_text_component':parent['source_node']['object_id'],
        'additional_text_component':pid}
    item.update(wrap=False,fit_font=True,
        source_layer=resources.gos[resources.trees[pid]['m_GameObject']['m_PathID']]['m_Layer'])
    return item


def bind_owner_layout(spec,card,fonts):
    """Evaluate LayoutExBetweenTexts with source widths/spaces and common baseline."""
    if not (card.get('pokemon') or {}).get('OwnerNameID'):return spec
    from .text_layout import measure_text
    from .font_fit import fit_single_line
    texts={t['id']:t for t in spec['texts']};name=texts['name']
    info=spec.setdefault('owner_name',{})
    additional=texts.get('name_additional')
    # SwitchSingle/MultiLine has not been recovered for owner-name fields.
    # A short multiline value can otherwise bypass single-line fitting entirely.
    for field in (name,additional):
        if field and re.search(r'[\r\n]|<br\s*/?>',field['text'],flags=re.I):
            raise UnsupportedLayout('Original owner multi-line name layout is not recovered',text_id=field['id'])
    def width(field):
        source=field.get('auto_condense_source_input',{})
        probe=dict(field,wrap=False,fit_font=False,character_width_scale=1)
        for key in ('font_size','horizontal_scale','character_spacing_tmp_units'):
            if key in source:probe[key]=source[key]
        return measure_text(probe,fonts[field['font_id']],fonts=fonts)['width']+.01
    try:
        left=width(name)
        right=width(additional) if additional else None
    except ValueError as error:
        if 'Missing original glyph' not in str(error):raise
        info['pending_original_glyphs']=True;return spec
    if additional is None:
        if not card['pokemon'].get('IsEX') and left>name['render_rect_top_left'][2]:
            fit_single_line(name,fonts[name['font_id']],name['render_rect_top_left'][2],fonts)
        info.update(pending_original_glyphs=False,flex=False)
        return spec
    data=next(c['data'] for c in name['companion_components'] if '_textMaxWidthForRocketPt' in c['data'])
    maximum=data['_textMaxWidthForRocketPt']
    # UpdateExLayout clamps its left preferred width before calling the flex
    # helper, at 0x481374C-0x4813778; the helper then applies its minWidth.
    left=max(data['_minWidth'],min(data['_textMaxWidthForEx'],left));right=max(data['_minWidth'],right)
    available=max(0,maximum-data['_exWidth']-data['_spaceLeftCenter']-data['_spaceCenterRight'])
    total=left+right
    if total>available:left,right=left*available/total,right*available/total
    left_rect=list(name['static_rect_top_left']);left_rect[2]=left
    name['render_rect_top_left']=left_rect
    fit_single_line(name,fonts[name['font_id']],left,fonts)
    ex_x=left+data['_spaceLeftCenter']
    right_x=ex_x+data['_exWidth']+data['_spaceCenterRight']
    rect=list(additional['static_rect_top_left']);rect[0]+=right_x;rect[2]=right
    additional['render_rect_top_left']=rect
    fit_single_line(additional,fonts[additional['font_id']],right,fonts)
    rule=spec['pokemon_rules'];delta=ex_x-rule['name_ex_old_anchor']
    for binding in spec['sprite_bindings']:
        if binding.get('source_path','').startswith(rule['name_prefix']):
            binding['render_rect_top_left']=list(binding['original_static_rect'])
            binding['render_rect_top_left'][0]+=delta
    info.update(pending_original_glyphs=False,flex=True,left_width=left,right_width=right,
        available_text_width=available,ex_x=ex_x,right_x=right_x,source_controller=deepcopy(data),
        native_method='PokemonCardNameView.LayoutExBetweenTexts',virtual_address='0x4812DB0')
    return spec


def add_category_resources(spec,card,locale,resources,output_dir):
    categories=(card.get('pokemon') or {}).get('AdditionalCategories') or []
    if not categories:return spec
    from .pokemon_rules import card_design,_active_function
    from .dynamic_ui import original_image_binding
    design=card_design(resources.root,card,resources)
    active=_active_function(resources,card,locale.language,design['dynamic_ui_overrides'])
    prefixes=('PokemonCardUI/UltraBeastView/','PokemonCardUI/AncientView/','PokemonCardUI/FutureView/')
    order=[]
    def visit(go):
        order.append(go)
        for child in resources.trees[resources.transforms[go]]['m_Children']:
            visit(resources.trees[child['m_PathID']]['m_GameObject']['m_PathID'])
    for go in resources.gos:
        if not resources.trees[resources.transforms[go]]['m_Father']['m_PathID']:visit(go)
    bindings=[]
    for go in order:
        if not _path(resources,go).startswith(prefixes) or not active(go):continue
        for component in resources.gos[go]['m_Component']:
            pid=component['component']['m_PathID'];data=resources.trees[pid]
            if not data.get('m_Sprite',{}).get('m_PathID'):continue
            sid='pokemon-category-'+str(pid)
            sprite=resources.export_sprite(sid,resources.objects[pid],data['m_Sprite'],output_dir)
            spec['sprite_assets'].append(sprite)
            bindings.append(original_image_binding(resources,go,pid,sid))
    if not bindings:raise UnsupportedLayout('Original Pokémon category images are unavailable',categories=categories)
    spec['category_sprite_bindings']=bindings
    spec['sprite_bindings'].extend(deepcopy(bindings))
    spec['pokemon_categories']={'master_categories':categories,'design':design,
        'source':'Original PokemonCardUI registered conditional Images, original sibling order and layers'}
    return spec
