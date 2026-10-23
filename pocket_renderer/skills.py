"""Bounded original Pokémon skill prefab evaluation.

All text slots, language overrides and layout dimensions come from the selected
PokemonCardUI prefab. Unsupported MS commands fail before they reach rendering.
The historical single-attack/no-description proof is deliberately unchanged.
"""
from copy import deepcopy
from functools import lru_cache
import re

from .localization import ENERGY_NAMES, LANGUAGES, UnsupportedLayout, apply_language_parameters

_TAG = re.compile(r'\[([^\[\]]+)\]')
_ATTR = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
_SLOTS = {
    'single': [(1415002257506047165, 4395453779550868669, -2488084847741430595)],
    'double': [(-1195088466168245059, -2769447925079337795, 7202721263710835901),
               (-9221819427690179395, 7834478688417879229, 3584186022435296445)],
    'ability': [(2456827237534602429, 7410157511985534141, -5289639847359277891)],
}


def validate_skills(card):
    attacks = card.get('attacks') or []
    abilities = card.get('pokemon', {}).get('PokemonAbilityIDs') or []
    if len(attacks) not in (1, 2):
        raise UnsupportedLayout('Original Pokémon prefab exposes one or two attack slots', attack_count=len(attacks))
    if len(abilities) > 1 or (abilities and len(attacks) != 1):
        raise UnsupportedLayout('Original ability layout exposes one ability and one attack')
    for attack in attacks:
        cost = attack.get('AttackCost', [])
        if len(cost) > 5 or any(v not in ENERGY_NAMES for v in cost):
            raise UnsupportedLayout('Original attack cost slots unavailable', cost=cost)
        if attack.get('DamageSymbol', 0) not in (0, 1, 2, 3):
            raise UnsupportedLayout('Original damage symbol glyph is not recovered', symbol=attack['DamageSymbol'])


def format_description(value, parameters, rich_text=False,locale=None,reference_audit=None):
    """Expand original numeric/count MS tokens; never guess a missing command.

    ``ref`` is the count parameter index (default zero); token ``id`` on
    Gr:Count is not a parameter reference. Num:Int's plural_only suffix is
    emitted only for a count other than one, along with the number.
    """
    def integer(index):
        try:
            raw = parameters[int(index)]
            if not re.fullmatch(r'[+-]?\d+', str(raw)):
                raise ValueError()
            return int(raw)
        except (IndexError, ValueError, TypeError):
            raise UnsupportedLayout('Invalid original numeric skill parameter', index=index, parameters=parameters)
    def replace(match):
        body = match[1]
        kind = body.split(None, 1)[0]
        attrs = dict(_ATTR.findall(body[len(kind):]))
        if rich_text and locale is not None:
            from .ms_references import resolve_reference
            resolved=resolve_reference(kind,attrs,parameters,locale,reference_audit)
            if resolved is not None:return resolved
        if kind == 'Num:Int':
            number = integer(attrs.get('id', '0'))
            if 'plural_only' in attrs:
                return '' if number == 1 else str(number) + attrs['plural_only']
            return str(number)
        if kind == 'Gr:Count':
            number = integer(attrs.get('ref', '0'))
            return attrs.get('s' if number == 1 else 'p', '')
        if rich_text and kind in ('Ctrl:Bold','/Ctrl:Bold','Ctrl:Italic','/Ctrl:Italic','Ctrl:NoBreak','/Ctrl:NoBreak'):
            return match[0]
        if rich_text and kind in ('Ctrl:OL','/Ctrl:OL','Ctrl:UL','/Ctrl:UL','Ctrl:LI','/Ctrl:LI'):
            return match[0]
        if rich_text and kind in ('Img:Element','Img:ex'):return match[0]
        if rich_text and kind in ('C:Lsb','C:Rsb') and not attrs:return match[0]
        if rich_text and kind=='Text:Char' and attrs=={'v':'THREE-PER-EM-SPACE'}:return match[0]
        raise UnsupportedLayout('Original skill MS command is not implemented', command=kind, original_tag=match[0])
    result=value
    for _ in range(8):
        updated=_TAG.sub(replace,result)
        if updated==result:break
        result=updated
    else:raise UnsupportedLayout('Original description reference nesting exceeds recovered limit')
    if rich_text:
        from .list_controls import expand_list_controls
        result=expand_list_controls(result,locale,reference_audit)
    unresolved=re.sub(r'\[/?Ctrl:(?:Bold|Italic|NoBreak)(?:\s+[^\[\]]*)?\]','',result) if rich_text else result
    if rich_text:unresolved=re.sub(r'\[Img:Element[^\[\]]*\]','',unresolved)
    if rich_text:unresolved=re.sub(r'\[Img:ex\s*\]','',unresolved)
    if rich_text:unresolved=re.sub(r'\[C:(?:Lsb|Rsb)\s*\]','',unresolved)
    if rich_text:unresolved=re.sub(r'\[Text:Char\s+v\s*=\s*"THREE-PER-EM-SPACE"\s*\]','',unresolved)
    if rich_text:unresolved=re.sub(r'<size=[+]?(?:\d+(?:\.\d*)?|\.\d+)%>|</size>','',unresolved,flags=re.I)
    if rich_text:unresolved=re.sub(r'<indent=\d+(?:\.\d+)?em>|</indent>','',unresolved)
    if '[' in unresolved or ']' in unresolved or '<' in unresolved:
        raise UnsupportedLayout('Unresolved original skill markup', value=result)
    return result


def _path(resources, go):
    tr = resources.trees[resources.transforms[go]]
    parent = tr['m_Father']['m_PathID']
    return (_path(resources, resources.trees[parent]['m_GameObject']['m_PathID'])+'/' if parent else '') + resources.gos[go]['m_Name']


def _rect(resources, go):
    x, y, w, h = resources.rect(go)
    return [x, 1024-y-h, w, h]


def _field(resources, pid, sid, value, provenance, locale, fonts, role):
    raw = deepcopy(resources.trees[pid])
    go = raw['m_GameObject']['m_PathID']
    companions = [{'id':c['component']['m_PathID'], 'data':deepcopy(resources.trees[c['component']['m_PathID']])}
                  for c in resources.gos[go]['m_Component']
                  if c['component']['m_PathID'] != pid
                  and resources.objects[c['component']['m_PathID']].type.name == 'MonoBehaviour']
    rect = _rect(resources, go)
    item = {'id':sid, 'kind':'text', 'text':value, 'content_provenance':provenance,
            'source_node':{'source':resources.UI, 'object_id':pid, 'game_object_id':go, 'path':_path(resources, go)},
            'static_rect_top_left':rect, 'render_rect_top_left':rect,
            'horizontal_alignment':{1:'left',2:'center',4:'right',8:'justified'}[raw['m_HorizontalAlignment']],
            'vertical_alignment':{256:'top',512:'middle',1024:'bottom'}[raw['m_VerticalAlignment']],
            'font_style_bits':raw['m_fontStyle'], 'font_id':role,
            'font_file':fonts[role]['original_font_file'], 'original_font_file':fonts[role]['original_font_file'],
            'original_tmp':raw, 'companion_components':companions,
            'layout_engine':'tmp-subset-v2', 'wrap':'description' in sid,
            'layout_confidence':'original-skill-prefab-and-language-condition',
            'notes':['Original skill TMP component; original UI vertical groups evaluated after glyph preparation.']}
    return apply_language_parameters(item, locale.language)


def _vertical_contract(resources, group_pid):
    """Retain and verify the exact non-flexible hierarchy evaluated below."""
    group=resources.trees[group_pid]
    go=group['m_GameObject']['m_PathID']
    records=[]
    def components(tid):
        go=resources.trees[tid]['m_GameObject']['m_PathID']
        return [(v['component']['m_PathID'],resources.trees[v['component']['m_PathID']])
                for v in resources.gos[go]['m_Component'] if v['component']['m_PathID'] != tid]
    def validate_group(pid,data):
        expected={'m_ChildControlHeight':1,'m_ChildForceExpandHeight':0,'m_ChildScaleHeight':0,
                  'm_ReverseArrangement':0}
        if any(data.get(k,0)!=v for k,v in expected.items()) or any(data['m_Padding'][k] for k in ('m_Top','m_Bottom')):
            raise UnsupportedLayout('Original skill vertical group contract changed',component_id=pid)
        records.append({'id':pid,'data':deepcopy(data)})
    validate_group(group_pid,group)
    for child in resources.trees[resources.transforms[go]]['m_Children']:
        groups=[(pid,t) for pid,t in components(child['m_PathID']) if 'm_ChildControlHeight' in t]
        if len(groups)!=1 or groups[0][1]['m_Spacing']!=0:
            raise UnsupportedLayout('Original skill child vertical group changed')
        validate_group(*groups[0])
        children=resources.trees[child['m_PathID']]['m_Children']
        if len(children)!=3:
            raise UnsupportedLayout('Original skill header/margin/description hierarchy changed')
        for index,leaf in enumerate(children):
            comps=components(leaf['m_PathID'])
            layouts=[(pid,t) for pid,t in comps if 'm_LayoutPriority' in t]
            if len(layouts)!=1:
                raise UnsupportedLayout('Original skill leaf LayoutElement is ambiguous')
            pid,layout=layouts[0]
            if layout['m_IgnoreLayout'] or layout['m_FlexibleHeight']>0:
                raise UnsupportedLayout('Original skill flexible/ignored child layout is not recovered',component_id=pid)
            if index==0 and (layout['m_MinHeight']!=40 or layout['m_PreferredHeight'] not in (40,-1)):
                raise UnsupportedLayout('Original skill header height changed',component_id=pid)
            if index==1 and (layout['m_MinHeight']!=7 or layout['m_PreferredHeight']!=7):
                raise UnsupportedLayout('Original skill margin height changed',component_id=pid)
            if index==2:
                text=[t for _,t in comps if 'm_text' in t]
                if layout['m_MinHeight'] not in (0,-1) or layout['m_PreferredHeight']!=-1 or len(text)!=1 or text[0]['m_overflowMode']!=0 or text[0]['m_VerticalAlignment']!=256:
                    raise UnsupportedLayout('Original skill description min/preferred/overflow contract changed',component_id=pid)
            records.append({'id':pid,'data':deepcopy(layout)})
    return records


@lru_cache(maxsize=4)
def _ability_tables(root):
    from .catalog import Catalog
    catalog = Catalog(root)
    return ({r['PokemonAbilityID']:r for r in catalog.table('PokemonAbility')},
            {r['PokemonAbilityNameID']:r for r in catalog.table('PokemonAbilityName')})


def _description(locale, record, param_key, category):
    key = record.get('DescriptionMSID')
    if not key:
        return '', {'authority':'original-master-empty', 'key':key}
    raw, provenance = locale.lookup('Master', key, category)
    parameters = record.get(param_key) or []
    references=[]
    value = format_description(raw, parameters, rich_text=True,locale=locale,reference_audit=references)
    return value, dict(provenance, original_template=raw, original_parameters=parameters,reference_audit=references)


def add_skill_texts(spec, card, locale, resources, fonts, output_dir=None):
    """Prepare all fields before typography/glyph population; no raster work."""
    validate_skills(card)
    has_ability = bool(card['pokemon'].get('PokemonAbilityIDs'))
    attacks = card['attacks']
    mode = 'ability' if has_ability else 'single' if len(attacks) == 1 else 'double'
    legacy = mode == 'single' and not attacks[0].get('DescriptionMSID') and not attacks[0].get('DamageSymbol', 0)
    if legacy:
        spec['skills'] = {'mode':mode, 'legacy':True}
        return spec
    needed_ids = [pid for slot in _SLOTS[mode] for pid in slot]
    if has_ability:
        needed_ids += [-210828893033851715,-5926574228905978691]
    missing = [pid for pid in needed_ids if pid not in resources.trees]
    if missing:
        raise UnsupportedLayout('Installed original skill prefab differs from recovered slot contract',missing_component_ids=missing)
    # Idempotent calls are safe when rebuilding a prepared source snapshot.
    spec['texts'] = [t for t in spec['texts'] if not (t['id'] in ('attack','damage','ability_name','ability_description')
                    or t['id'].startswith(('attack_', 'damage_')))]
    keys = card.get('attack_name_keys') or [v['NameMSID'] for v in card['attack_names']]
    metadata = {'mode':mode, 'legacy':False, 'attack_count':len(attacks),
                'prefab_source':resources.UI, 'container_rect_top_left':[30,543,674,300],
                'container_alignment':'UpperCenter' if has_ability else 'MiddleCenter', 'spacing':30,
                'container_group_component_id':{'single':-8383852158022249283,
                    'double':-5014854185218075459,'ability':4622754740962923709}[mode],
                'source_component_ids':needed_ids}
    spacing_controllers = [{'id':pid, 'data':deepcopy(t)} for pid,t in resources.trees.items()
                           if t.get('_verticalLayoutGroup',{}).get('m_PathID') == metadata['container_group_component_id']]
    if mode != 'single' and len(spacing_controllers) != 1:
        raise UnsupportedLayout('Original skill container spacing controller is ambiguous', mode=mode)
    metadata['spacing_controller'] = spacing_controllers[0] if spacing_controllers else None
    metadata['vertical_layout_components'] = _vertical_contract(resources,metadata['container_group_component_id'])
    for index, (attack, ids) in enumerate(zip(attacks, _SLOTS[mode])):
        suffix = '' if index == 0 else '_'+str(index)
        name, source = locale.lookup('Master', keys[index], 50)
        description, desc_source = _description(locale, attack, 'AttackLogicParameters', 190)
        master_source = {'authority':'original-local-master', 'attack_id':attack['PokemonAttackID'],
                         'sha256':card['provenance']['sha256']}
        fields = [('attack'+suffix, ids[0], name, source, 'bold'),
                  ('damage'+suffix, ids[2], '' if attack.get('IsNoDamage') else str(attack['Damage']),
                   dict(master_source, field='Damage'), 'number')]
        if description:
            fields.append(('attack_description'+suffix, ids[1], description, desc_source, 'medium'))
        for sid, pid, value, provenance, role in fields:
            spec['texts'].append(_field(resources,pid,sid,value,provenance,locale,fonts,role))
        if attack.get('DamageSymbol'):
            name_path = _path(resources, resources.trees[ids[0]]['m_GameObject']['m_PathID'])
            header_path = name_path.rsplit('/', 1)[0]
            candidates = [pid for pid,t in resources.trees.items() if 'm_text' in t
                          and _path(resources,t['m_GameObject']['m_PathID']) == header_path+'/damage_num_elm/plus_txt']
            if len(candidates) != 1:
                raise UnsupportedLayout('Original damage plus component is ambiguous')
            pid = candidates[0]
            from .damage_symbols import VALUES
            spec['texts'].append(_field(resources,pid,'damage_symbol'+suffix,VALUES[attack['DamageSymbol']][0],
                                        dict(master_source,field='DamageSymbol'),locale,fonts,'number'))
    if has_ability:
        abilities,names = _ability_tables(str(resources.root))
        ability = abilities.get(card['pokemon']['PokemonAbilityIDs'][0])
        if not ability or ability['PokemonAbilityNameID'] not in names:
            raise UnsupportedLayout('Original ability master/name unavailable')
        name,source = locale.lookup('Master',names[ability['PokemonAbilityNameID']]['NameMSID'],258)
        description,desc_source = _description(locale,ability,'AbilityLogicParameters',154)
        spec['texts'].append(_field(resources,-210828893033851715,'ability_name',name,source,locale,fonts,'bold'))
        if description:
            spec['texts'].append(_field(resources,-5926574228905978691,'ability_description',description,desc_source,locale,fonts,'medium'))
        metadata['ability_id'] = ability['PokemonAbilityID']
    spec['skills'] = metadata
    return spec


def bind_skill_layout(spec, card, fonts, measure=None):
    """Return cost/badge bindings after actual original glyphs are available.

    Run after legacy bind_layout. Replace its cost_* bindings with this result's
    energy_bindings only when legacy is false. Missing glyphs defer placement;
    no guessed metrics are used.
    """
    if 'skills' not in spec or spec['skills'].get('legacy'):
        return {'legacy':True, 'energy_bindings':[]}
    if measure is None:
        from .text_layout import measure_text
        measure = lambda text,font,width=None:measure_text(text,font,width,fonts=fonts)
    texts = {t['id']:t for t in spec['texts']}
    mode = spec['skills']['mode']
    heights = []
    minimums = []
    title_sizes = {}
    def title_size(sid, available):
        field = texts[sid]
        measured = measure(field,fonts[field['font_id']])
        layouts = [c['data'] for c in field['companion_components'] if 'm_LayoutPriority' in c['data']]
        min_width = max([0]+[c.get('m_MinWidth',-1) for c in layouts])
        preferred_width = max([measured['width']+.01]+[c.get('m_PreferredWidth',-1) for c in layouts])
        min_height = max([0]+[c.get('m_MinHeight',-1) for c in layouts])
        width = min(available,max(min_width,preferred_width))
        if measured['width'] > width+.01:
            from .font_fit import fit_single_line
            measured=fit_single_line(field,fonts[field['font_id']],width,fonts)
        return width,max(min_height,measured['height']+.01)
    def preferred(sid):
        if sid not in texts:
            return 0
        field = texts[sid]
        measured = measure(field, fonts[field['font_id']], 628)
        field['preferred_height_audit'] = {k:measured[k] for k in ('height','line_step','font_size')}
        # TMP GetPreferredValues adds .01 after calculating preferred height;
        # source saved 23.01/57.05/91.09 at JP23 corroborates this epsilon.
        return measured['height'] + .01
    try:
        from .skill_fit import restore_description, clamp_line_counts, fit_description
        descriptions = [texts.get('ability_description')]
        descriptions += [texts.get('attack_description'+('' if i==0 else '_'+str(i))) for i in range(len(card['attacks']))]
        for field in descriptions:
            if field:restore_description(field)
        base_counts = [len(measure(field, fonts[field['font_id']], 628)['lines']) if field else 0 for field in descriptions]
        line_budgets = clamp_line_counts(base_counts)
        for field, budget in zip(descriptions, line_budgets):
            if field:fit_description(field, fonts[field['font_id']], budget, measure)
        controller=spec['skills'].get('spacing_controller')
        spacing=0
        if controller:
            data=controller['data']
            spacing=data['_greaterSpacing'] if sum(line_budgets)>data['_spaceSizeThreshold'] else data['_lEqualSpacing']
        spec['skills']['native_line_allocation']={'base_counts':base_counts,'line_budgets':line_budgets,
            'maximum':6,'native_method':'0x47F98C8','spacing_method':'0x47ECB0C','spacing':spacing}
        spec['skills']['spacing']=spacing
        if mode == 'ability':
            h = preferred('ability_description')
            heights.append(40+(7+h if h else 0))
            minimums.append(47 if h else 40)
            title_sizes['ability_name'] = title_size('ability_name',330)
        for index in range(len(card['attacks'])):
            suffix = '' if index == 0 else '_'+str(index)
            h = preferred('attack_description'+suffix)
            heights.append(40+(7+h if h else 0))
            minimums.append(47 if h else 40)
            title_sizes['attack'+suffix] = title_size('attack'+suffix,378 if len(card['attacks'][index]['AttackCost']) <= 4 else 334)
    except ValueError as error:
        if 'Missing original glyph' not in str(error):
            raise
        spec['skills']['pending_original_glyphs'] = str(error)
        return {'legacy':False,'pending_original_glyphs':True,'energy_bindings':[]}
    total = sum(heights)+spacing*(len(heights)-1)
    from .skill_fit import vertical_group_heights
    heights,vertical_audit=vertical_group_heights(minimums,heights,spacing,300)
    spec['skills']['native_vertical_layout']=vertical_audit
    top = 543 if mode == 'ability' else 543+max(0,(300-total)/2)
    def place(sid, rect):
        if sid in texts:
            texts[sid]['render_rect_top_left'] = rect
            texts[sid]['layout_confidence'] = 'original-vertical-layout-groups-and-TMP-preferred-height'
    if mode == 'ability':
        place('ability_name',[271,top+4,*title_sizes['ability_name']])
        place('ability_description',[53,top+47,628,heights[0]-47])
        spec['skills']['ability_badge_rect_top_left'] = [53,top+4,162,36]
        top += heights.pop(0)+spacing
    bindings = []
    for index,(attack,height) in enumerate(zip(card['attacks'],heights)):
        suffix = '' if index == 0 else '_'+str(index)
        padding = 174 if len(attack['AttackCost']) <= 4 else 218
        # HorizontalLayoutGroup controls width without force expansion: use
        # preferredWidth clamped to original minWidth and available content.
        place('attack'+suffix,[52+padding,top+4,*title_sizes['attack'+suffix]])
        place('damage'+suffix,[581,top+2,100,40])
        place('damage_symbol'+suffix,[676,top,30,40])
        if 'damage_symbol'+suffix in texts:
            from .damage_symbols import bind_symbol
            bind_symbol(texts['damage_symbol'+suffix],attack['DamageSymbol'],[676,top,30,40])
        place('attack_description'+suffix,[53,top+47,628,height-47])
        for energy_index,value in enumerate(attack['AttackCost'] or [None]):
            left = 47+42.5*energy_index
            prefix = 'cost%s_%d' % (suffix,energy_index)
            bindings.extend([{'id':prefix+'_outline','source_sprite':'energy_outline','render_rect_top_left':[left,top,41.5,41.5]},
                             {'id':prefix,'source_sprite':'empty' if value is None else ENERGY_NAMES[value],'render_rect_top_left':[left+1.8,top+1.8,37.9,37.9]}])
        top += height+spacing
    spec['skills'].update(preferred_height=total, pending_original_glyphs=False, original_group_heights=heights)
    return {'legacy':False,'energy_bindings':bindings}


def add_ability_resources(spec, card, locale, resources, output_dir):
    """Export the original localized ability badge, after sprite setup."""
    if not spec.get('skills',{}).get('ability_id'):
        return spec
    enum = LANGUAGES[locale.language]['asset_enum']
    candidates = []
    for go,data in resources.gos.items():
        if not data['m_Name'].startswith('ability_icn_txt_img_'):
            continue
        ids = [c['component']['m_PathID'] for c in data['m_Component']]
        conditions = [c for pid in ids for c in resources.trees[pid].get('_conditions',[])]
        if any(c.get('_language') == enum for c in conditions):
            images = [pid for pid in ids if 'm_Sprite' in resources.trees[pid]]
            if len(images) == 1:
                candidates.append((go,images[0],conditions))
    if len(candidates) != 1:
        raise UnsupportedLayout('Original localized ability badge is ambiguous', language=locale.language)
    backgrounds = [(go,pid) for go,data in resources.gos.items() if data['m_Name']=='ability_icn_img'
                   for pid in [c['component']['m_PathID'] for c in data['m_Component']]
                   if 'm_Sprite' in resources.trees[pid]]
    if len(backgrounds) != 1:
        raise UnsupportedLayout('Original ability badge background is ambiguous')
    _,background_pid = backgrounds[0]
    background = resources.export_sprite('ability_background',resources.objects[background_pid],resources.trees[background_pid]['m_Sprite'],output_dir)
    spec['sprite_assets'].append(background)
    spec['sprites'].append({'id':'ability_background','kind':'sprite','file':background['texture']['png'],
                           'original_pixels':[background['texture']['width'],background['texture']['height']],
                           'render_rect_top_left':None,'provenance':background['source']})
    spec['sprite_bindings'].append({'id':'ability_badge_background','source_sprite':'ability_background',
        'render_rect_top_left':spec['skills']['ability_badge_rect_top_left'],
        'confidence':'original-ability-background-image'})
    go,pid,conditions = candidates[0]
    sprite = resources.export_sprite('ability',resources.objects[pid],resources.trees[pid]['m_Sprite'],output_dir)
    sprite['language_condition'] = {'enum':enum,'conditions':conditions,'source_game_object':go}
    spec['sprite_assets'].append(sprite)
    spec['sprites'].append({'id':'ability','kind':'sprite','file':sprite['texture']['png'],
                           'original_pixels':[sprite['texture']['width'],sprite['texture']['height']],
                           'render_rect_top_left':None,'provenance':sprite['source']})
    spec['sprite_bindings'].append({'id':'ability_badge','source_sprite':'ability',
        'render_rect_top_left':spec['skills']['ability_badge_rect_top_left'],
        'confidence':'original-language-condition-and-vertical-layout-group'})
    return spec
