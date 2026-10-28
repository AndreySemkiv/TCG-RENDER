"""Source-backed EX/Mega name and rule UI, independent of card artwork.

Design/font/dynamic selectors come from this card's CardSettings, not a rarity
name heuristic. Native name layout evidence is in reports/pokemon-rule-native.
"""
from copy import deepcopy
from pathlib import Path
import re
from .localization import LANGUAGES,UnsupportedLayout,normalize_language
from .skills import _field,_path,_rect
from .layout import preferred_advance


def validate_rule_profile(card):
    """Initial EX/Mega eligibility; source/runtime details are checked later."""
    mon=card.get('pokemon') or {}
    if mon.get('IsMegaEvolution') and not mon.get('IsEX'):
        raise UnsupportedLayout('Original Mega without EX rule is not recovered')
    if not mon.get('IsEX'):return
    if not card.get('name_key'):
        raise UnsupportedLayout('Original EX display name localization key is unavailable')


def card_design(root,card,resources):
    """Follow original CardSettings -> design -> font conditions PPtrs."""
    from .card_design import load_card_design,design_activation
    owner,relative,design,design_file=load_card_design(resources,card)
    settings=owner.read_typetree()
    data=design.read_typetree();font,font_file=resources.resolve(design,data['_fonts'])
    overrides=design_activation(resources,design)
    return {'font_conditions_file':font_file,'design_source':resources.source(design,design_file),
        'card_settings_source':resources.source(owner,relative),'card_settings':settings,
        'font_conditions_source':resources.source(font,font_file),'dynamic_ui_overrides':overrides}


def _active_function(resources,card,language,overrides):
    enum=LANGUAGES[normalize_language(language)]['asset_enum'];mega=bool(card['pokemon'].get('IsMegaEvolution'))
    controllers=[t for t in resources.trees.values() if '_cardConditionViews' in t]
    if len(controllers)!=1:raise UnsupportedLayout('Original Pokémon UI condition controller is ambiguous')
    registered={p['m_PathID'] for p in controllers[0]['_cardConditionViews']};cache={}
    from .card_conditions import native_condition_contract,pokemon_condition_extra
    condition_contract=native_condition_contract(str(resources.root))
    def check(c):
        flag=c['_conditionType']
        if flag&~(489|condition_contract['dual']|condition_contract['rgb']):raise UnsupportedLayout('Original rule UI condition is not recovered',condition=c)
        categories=card['pokemon'].get('AdditionalCategories') or []
        return ((not flag&1 or c['_language']==enum) and
            (not flag&8 or c['_pokemonRuleType']==2) and
            (not flag&64 or bool(c['_isMegaEvolution'])==mega) and
            (not flag&32 or bool(c['_isUltraBeast'])==(1 in categories)) and
            (not flag&128 or bool(c['_isFuture'])==(3 in categories)) and
            (not flag&256 or bool(c['_isAncient'])==(2 in categories)) and
            (not condition_contract['dual'] or pokemon_condition_extra(condition_contract,c,card)))
    def active(go):
        if go not in cache:
            d=resources.gos[go];conditions=[resources.trees[c['component']['m_PathID']]['_conditions']
                for c in d['m_Component'] if c['component']['m_PathID'] in registered
                and resources.trees[c['component']['m_PathID']].get('m_Enabled',True)]
            own=any(check(c) for group in conditions for c in group) if conditions else bool(d['m_IsActive'])
            own=overrides.get(go,own)
            parent=resources.trees[resources.transforms[go]]['m_Father']['m_PathID']
            cache[go]=own and (active(resources.trees[parent]['m_GameObject']['m_PathID']) if parent else True)
        return cache[go]
    return active


def add_rule_texts(spec,card,locale,resources,fonts):
    if not card['pokemon'].get('IsEX'):return spec
    mega=bool(card['pokemon'].get('IsMegaEvolution'))
    design=card_design(resources.root,card,resources)
    active=_active_function(resources,card,locale.language,design['dynamic_ui_overrides'])
    rule_prefix='PokemonCardUI/'+('PokemoMegaExRuleView' if mega else 'PokemonExRuleView')+'/'
    choices=[(pid,t) for pid,t in resources.trees.items() if 'm_text' in t
        and _path(resources,t['m_GameObject']['m_PathID']).startswith(rule_prefix)
        and active(t['m_GameObject']['m_PathID'])]
    if len(choices)!=1:raise UnsupportedLayout('Original active Pokémon rule text is ambiguous',choices=[p for p,t in choices])
    key='megaex_pokemon_down_description' if mega else 'ex_pokemon_down_description'
    value,provenance=locale.lookup('UI',key,102)
    field=_field(resources,choices[0][0],'pokemon_rule',value,provenance,locale,fonts,'medium')
    field['source_layer']=resources.gos[choices[0][1]['m_GameObject']['m_PathID']]['m_Layer']
    # Original TMP TextWrappingModes: NoWrap=0, Normal=1,
    # PreserveWhitespace=2, PreserveWhitespaceNoWrap=3 (dump TypeDef 27758).
    # The Japanese Mega field is NoWrap: forcing wrapping pushes its first
    # line up into the separate localized title Image.
    mode=field['original_tmp']['m_TextWrappingMode']
    field['wrap']=mode not in (0,3)
    field['wrapping_source']={'component':choices[0][0],'serialized_mode':mode,
        'enum':'TMPro.TextWrappingModes','type_definition_index':27758}
    field['img_tag_font_type_override']=2 if mega else 4
    field['img_tag_font_type_source']={'native_method':'PokemonCardUI.GetRuleImgTagFontType',
        'virtual_address':'0x47F8F48','input_IsMegaEvolution':mega,'output':field['img_tag_font_type_override']}
    spec['texts']=[t for t in spec['texts'] if t['id']!='pokemon_rule']+[field]
    # The native regex removes only a terminal optional-space/hyphen + "ex".
    name,np=locale.lookup('Master',card['name_key'],262)
    additional='';owner_audit=None
    if card['pokemon'].get('OwnerNameID'):
        from .owner_names import split_ex_name
        clean,additional,owner_audit=split_ex_name(resources.root,card,locale,name)
    else:clean=re.sub(r'\s?-?ex$','',name)
    if clean==name:raise UnsupportedLayout('Original EX name suffix is not recovered',name=name)
    name_ids=[1171879138902545597,-4586790127004483] if mega else [-3799754503620455235]
    names=[]
    for index,pid in enumerate(name_ids):
        item=_field(resources,pid,'name' if not index else 'name_outline',clean,
            dict(np,original_text=name,native_transform={'method':'PokemonCardNameView.SetText / .cctor',
                'virtual_addresses':['0x4812FE8','0x4813A9C'],'regex':r'\s?-?ex$','replacement':''}),locale,fonts,'bold')
        item.update(wrap=False,fit_font=True)
        item['source_layer']=resources.gos[resources.trees[pid]['m_GameObject']['m_PathID']]['m_Layer']
        names.append(item)
    spec['texts']=[t for t in spec['texts'] if t['id'] not in ('name','name_outline')]+names
    if additional:
        if mega:raise UnsupportedLayout('Original Mega owner flex outline is not recovered')
        from .owner_names import additional_name_field
        spec['texts'].append(additional_name_field(resources,names[0],additional,locale,fonts))
    if owner_audit:spec['owner_name']=owner_audit
    if mega:
        # Native ShouldHideLibrarySpec/Flavor both return IsMegaEvolution.
        for t in spec['texts']:
            if t['id'] in ('dex_line','flavor'):
                t['text']='';t['visibility_source']={'native_methods':['0x47F9898','0x47F98B0'],'IsMegaEvolution':True}
    spec['pokemon_rules']={'mega':mega,'rule_prefix':rule_prefix,'design':design,
        'name_prefix':'PokemonCardUI/'+('mega_name_elm' if mega else 'name_elm')+'/Ex/',
        'rule_text_component_id':choices[0][0],'name_text_component_ids':name_ids,
        'limits':['Recovered EX single-line name and original rule fields. Full runtime flex/multi-line name logic is not executed.']}
    name_view=[c for c in names[0]['companion_components'] if '_textMaxWidthForEx' in c['data']]
    if len(name_view)!=1:raise UnsupportedLayout('Original name controller is ambiguous')
    ex_transform=resources.trees[name_view[0]['data']['_ex']['m_PathID']]
    spec['pokemon_rules']['name_ex_old_anchor']=ex_transform['m_AnchoredPosition']['x']
    spec['pokemon_rules']['name_ex_transform_source']={'prefab':resources.UI,
        'path_id':name_view[0]['data']['_ex']['m_PathID'],'original_transform':ex_transform}
    return spec


def add_rule_resources(spec,card,locale,resources,output_dir):
    if not spec.get('pokemon_rules'):return spec
    info=spec['pokemon_rules'];active=_active_function(resources,card,locale.language,info['design']['dynamic_ui_overrides'])
    prefixes=(info['rule_prefix'],info['name_prefix']);bindings=[]
    roots=[go for go in resources.gos if not resources.trees[resources.transforms[go]]['m_Father']['m_PathID']]
    order=[]
    def walk(go):
        order.append(go)
        for child in resources.trees[resources.transforms[go]]['m_Children']:
            walk(resources.trees[child['m_PathID']]['m_GameObject']['m_PathID'])
    for go in roots:walk(go)
    spec['sprite_assets']=[v for v in spec['sprite_assets'] if not v['id'].startswith('pokemon-rule-')]
    for go in order:
        path=_path(resources,go)
        if not path.startswith(prefixes) or not active(go):continue
        for component in resources.gos[go]['m_Component']:
            pid=component['component']['m_PathID'];d=resources.trees[pid]
            if 'm_Sprite' not in d or not d['m_Sprite']['m_PathID']:continue
            sid='pokemon-rule-'+str(pid)
            sprite=resources.export_sprite(sid,resources.objects[pid],d['m_Sprite'],output_dir)
            # Camera culling uses the Image's own GameObject layer, not its
            # parent's layer. Gold EX fill and rule fill belong to the separate
            # CardUIMetallic target; their outlines belong to CardUIText.
            material,material_file=resources.resolve(resources.objects[pid],d['m_Material'])
            sprite['image_component_source']={'source':resources.source(resources.objects[pid],resources.UI),
                'game_object_id':go,'layer':resources.gos[go]['m_Layer'],
                'color':d['m_Color'],'material':resources.source(material,material_file),
                'material_name':material.read_typetree()['m_Name']}
            spec['sprite_assets'].append(sprite)
            bindings.append({'id':sid,'source_sprite':sid,'render_rect_top_left':_rect(resources,go),
                'original_static_rect':_rect(resources,go),'source_path':path,
                'source_layer':resources.gos[go]['m_Layer'],
                'vertex_color_rgba':[d['m_Color'][k] for k in 'rgba'],
                'confidence':'original-prefab-conditional-activation-and-source-sibling-order'})
    info['sprite_bindings']=bindings
    bind_rule_layout(spec,card,spec['font_metrics'])
    from .owner_names import bind_owner_layout
    bind_owner_layout(spec,card,spec['font_metrics'])
    return spec


def bind_rule_layout(spec,card,fonts):
    if not spec.get('pokemon_rules'):return spec
    info=spec['pokemon_rules'];texts={t['id']:t for t in spec['texts']};name=texts['name']
    view=[c for c in name['companion_components'] if '_textMaxWidthForEx' in c['data']]
    if len(view)!=1:raise UnsupportedLayout('Original EX name controller is ambiguous')
    data=view[0]['data']
    if '<size=' in name['text']:
        from .text_layout import measure_text
        try:preferred=measure_text(dict(name,wrap=False,fit_font=False),fonts[name['font_id']],fonts=fonts)['width']
        except ValueError as error:
            if 'Missing original glyph' not in str(error):raise
            preferred=None
    else:preferred=preferred_advance(name['text'],fonts[name['font_id']],name['font_size'],name['character_spacing_tmp_units'])
    if preferred is None:info['name_pending_original_glyphs']=True;return spec
    preferred+=.01 # TMP GetPreferredValues uses this epsilon.
    maximum=data['_textMaxWidthForEx'];width=min(maximum,preferred)
    if '\n' in name['text']:raise UnsupportedLayout('Original EX multi-line name layout is not recovered')
    for sid in ('name','name_outline'):
        if sid not in texts:continue
        field=texts[sid];rect=list(field['static_rect_top_left'])
        if preferred>maximum:rect[2]=maximum
        field['render_rect_top_left']=rect
        if preferred>maximum:
            from .font_fit import fit_single_line
            fit_single_line(field,fonts[field['font_id']],maximum,fonts)
        field['layout_confidence']='original-PokemonCardNameView-UpdateExLayout-preferred-width-and-300-limit'
    # AnchoredPosition.x of the EX parent becomes the preferred width. Preserve
    # every child's original inset, y, dimensions and hierarchy order.
    old_anchor=info['name_ex_old_anchor']
    delta=width-old_anchor
    bindings=deepcopy(info.get('sprite_bindings',[]))
    for b in bindings:
        b['render_rect_top_left']=list(b['original_static_rect'])
        if b['source_path'].startswith(info['name_prefix']):b['render_rect_top_left'][0]+=delta
    spec['sprite_bindings']=[b for b in spec['sprite_bindings'] if not b['id'].startswith('pokemon-rule-')]+bindings
    info.update(name_pending_original_glyphs=False,name_preferred_width=preferred,name_width_limit=maximum,
        name_ex_width=width,name_ex_delta=delta,name_controller_component=view[0],
        name_layout_source={'native_method':'PokemonCardNameView.UpdateExLayout','virtual_address':'0x4813664',
            'preferred_width_method':'LayoutUtility.GetPreferredWidth','virtual_address_preferred':'0x6D5C628'})
    return spec
