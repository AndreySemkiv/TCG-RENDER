"""Original owner-name flex layout and Pokémon category Images.

No display names or collection/card allowlist is supplied by this module. The
native flex selector is decoded from the selected, inspected binary/metadata;
localized names and category flags come from the selected original master.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re

from .localization import LANGUAGES, UnsupportedLayout
from .skills import _field, _path


@lru_cache(maxsize=4)
def native_name_contract(root):
    """Resolve current literals and one of the inspected native flex branches."""
    from .native_text_contract import source
    native=source(str(Path(root).resolve()))
    namespace='Lettuce.Infrastructure.Card.Core';name='PokemonCardNameView'
    def require(method, digest):
        if native.normalized_method(namespace,name,method)!=digest:
            raise UnsupportedLayout('Owner-name native method structure is not recovered',method=method)
    require('.cctor','17b7e6963c9df4b23ab45abedcded0b96d38fe6e050b479b2c8b88678339b9db')
    def literals(method):
        result=[]
        for ref in native.method_references(namespace,name,method):
            try:result.append((ref['target'],native.literal(ref['target'])))
            except ValueError:pass
        return result
    strings={value for _,value in literals('.cctor')}
    suffix=r'\s?-?ex$';split=r'(?<=(?:\s|-)ex)(?=[^\w\s])'
    if not {suffix,split}<=strings:
        raise UnsupportedLayout('Original owner-name regex literals are not recovered')
    flex_digest=native.normalized_method(namespace,name,'ShouldFlexLayout')
    result={'flex_card_id_fragments':[],'suffix_regex':suffix,'split_regex':split,
            'language_bitmask':0x12c,'provenance':dict(native.provenance,
                methods={m:hex(native.method(namespace,name,m)) for m in ('ShouldFlexLayout','.ctor','.cctor')})}
    if flex_digest=='747adebe43edbf9f2ceaf37b8f71d6f9fe143a4823c5d038c944efe6d0931bf5':
        require('.ctor','0b9b7e8cc033f51089b366a35e60d4f7982179238928277f997e1dfaf5eda75e')
        fragments={value:address for address,value in literals('.ctor') if re.fullmatch(r'[0-9]{6}',value)}
        if len(fragments)!=6:
            raise UnsupportedLayout('Original owner-name flex fragment array is not recovered')
        result['flex_card_id_fragments']=list(fragments)
        result['provenance']['fragment_literal_slots']=[hex(v) for v in fragments.values()]
        result['flex_selector']='card-id-fragments'
    elif flex_digest=='1bf86bf3f69c21dae7383c8fed8bfc1c24b177a54ea3909571df499ea042f215':
        data_namespace=namespace+'.Data'
        checks=[('PokemonRegionFormTypeExtensions','FromRegionFormId',
                 'b1ee9d03b800f1cf051933eec7a0bdc981f1019faf49bb2990593e50f4b7637f'),
                ('PokemonCardData','get_PokemonRegionFormType',
                 '2567803b2657720ccf21bc84b638b4e96b979bb1e6b3102a8d03cdf0f468aae0')]
        for owner,method,digest in checks:
            if native.normalized_method(data_namespace,owner,method)!=digest:
                raise UnsupportedLayout('Original region-form flex selector is not recovered',method=method)
        regions=set()
        for ref in native.method_references(data_namespace,'PokemonRegionFormTypeExtensions','FromRegionFormId'):
            try:regions.add(native.literal(ref['target']))
            except ValueError:pass
        if len(regions)!=1:
            raise UnsupportedLayout('Original flex region-form literal is ambiguous')
        result.update(flex_selector='region-form',flex_region_form_id=regions.pop())
        result['provenance']['region_form_source']='original PokemonCard.RegionFormID -> PokemonCardEntity.RegionFormId -> FromRegionFormId'
    else:
        raise UnsupportedLayout('Owner-name native flex selector is not recovered')
    return result


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
    selected=(card['card'].get('RegionFormID')==contract['flex_region_form_id']
              if contract['flex_selector']=='region-form' else any(
                  fragment in card['card_id'] for fragment in contract['flex_card_id_fragments']))
    flex=bool((1<<language)&contract['language_bitmask']) and selected
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
