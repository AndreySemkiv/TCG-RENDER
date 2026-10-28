"""Original trainer family UI; unknown rich text remains explicit."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from .localization import (_OriginalUIResources,LocaleRepository,LANGUAGES,
    _read_json,normalize_language,glyph_coverage,UnsupportedLayout)
from .skills import _field,_path,_rect,format_description
from .layout import preferred_advance


class _TrainerResources(_OriginalUIResources):
    UI='Common/CardNew/System/Prefabs/TrainersCardUI.prefab_bundles'


@lru_cache(maxsize=4)
def _resources(root):return _TrainerResources(Path(root))


def validate_trainer(card,allow_pending_masks=False):
    trainer=card.get('trainer') or {}
    if trainer.get('TrainerType') not in (1,2,3,4,5):raise UnsupportedLayout('Original trainer type is unavailable')
    if set(trainer.get('AdditionalCategories') or [])-{2,3}:
        raise UnsupportedLayout('Original trainer category is not recovered')
    if card['card'].get('IsSerial'):raise UnsupportedLayout('Original trainer serial number UI is not recovered')
    # Rarity/mirror design selection is recovered from this card's own settings.
    # Actual scene shaders and all text commands are still checked downstream.


def _fonts(root,language,resources,output):
    fonts=deepcopy(_read_json(str(root/'original-render-pipeline/text/font-metrics.json')))
    for role,font in fonts.items():
        file={'bold':'TPC_GillSansNovaBold.otf','medium':'TPC_GillSansNovaMedium.otf','number':'TPC_FuturaLTProHeavy.otf'}[role]
        font['original_font_file']=str(root/'offline-render/bulbasaur-original/fonts'/file)
        for atlas in font['atlases']:
            atlas['png']=str(root/'original-render-pipeline/text'/atlas['png'])
            atlas['raw']['file']=str(root/'original-render-pipeline/text'/atlas['raw']['file'])
    if language=='ja-JP':
        from .japanese_fonts import load_japanese_fonts
        fonts.update(load_japanese_fonts(root,resources,output/'original-fonts'))
    return fonts


def bind_trainer_layout(spec,card,fonts):
    texts={t['id']:t for t in spec['texts']}
    name=texts.get('name')
    if name:
        from .text_layout import measure_text
        from .font_fit import fit_single_line
        try:
            measured=measure_text(dict(name,wrap=False,fit_font=False),fonts[name['font_id']],fonts=fonts)
            if measured['width']>name['render_rect_top_left'][2]+.01:
                fit_single_line(name,fonts[name['font_id']],name['render_rect_top_left'][2],fonts)
        except ValueError as error:
            if 'Missing original glyph' not in str(error):raise
    prefix=texts['illustrator_prefix'];artist=texts['illustrator']
    x,y,total,height=spec['artist_group_rect']
    width=preferred_advance(prefix['text'],fonts[prefix['font_id']],prefix['font_size'],prefix['character_spacing_tmp_units'])
    if width is not None:
        width=max(15.600000381469727,width)
        prefix['render_rect_top_left']=[x,y,width,height]
        artist['render_rect_top_left']=[x+width,y,total-width,height]
    else:
        prefix['render_rect_top_left']=[x,y,30,height]
        artist['render_rect_top_left']=[x+30,y,total-30,height]
    for field in texts.values():field['layout_confidence']='original-trainer-rect-and-language-parameters'
    if 'hp_value' in texts:
        hp=texts['hp_value'];group=spec['fossil_hp_group_rect']
        width=preferred_advance(hp['text'],fonts[hp['font_id']],hp['font_size'],hp['character_spacing_tmp_units'])
        if width is not None:
            right=group[0]+group[2]
            hp['render_rect_top_left']=[right-width,group[1],width,group[3]]
            texts['hp_label']['render_rect_top_left']=[right-width-36,group[1]+20,40,30]
    return spec


def localize_trainer(root,card,language,output_dir,allow_pending_masks=False):
    validate_trainer(card,allow_pending_masks=allow_pending_masks)
    root=Path(root).resolve();language=normalize_language(language);output=Path(output_dir)
    resources=_resources(str(root));locale=LocaleRepository(root,language)
    fonts=_fonts(root,language,resources,output);trainer=card['trainer']
    from .card_design import load_card_design,design_activation
    settings,settings_file,design,design_file=load_card_design(resources,card)
    if settings.read_typetree()['_promotionLogo']['m_PathID']:
        raise UnsupportedLayout('Original separate promotion logo prefab is not recovered',card_settings=settings_file)
    design_data=design.read_typetree();overrides=design_activation(resources,design)
    enum=LANGUAGES[language]['asset_enum'];asset_type={1:1,2:0,3:3,4:4,5:2}[trainer['TrainerType']]
    controllers=[t for t in resources.trees.values() if '_cardConditionViews' in t]
    if len(controllers)!=1:raise UnsupportedLayout('Original trainer UI controller is ambiguous')
    registered_conditions={p['m_PathID'] for p in controllers[0]['_cardConditionViews']}
    from .card_conditions import native_condition_contract,trainer_condition_matches
    condition_contract=native_condition_contract(str(root))
    def condition(c):
        return trainer_condition_matches(condition_contract,c,asset_type,
            trainer.get('AdditionalCategories') or [],enum)
    active_cache={}
    def active(go):
        if go not in active_cache:
            tree=resources.gos[go];switches=[resources.trees[c['component']['m_PathID']]['_conditions'] for c in tree['m_Component'] if c['component']['m_PathID'] in registered_conditions and resources.trees[c['component']['m_PathID']].get('m_Enabled',True)]
            own=any(condition(c) for choices in switches for c in choices) if switches else bool(tree['m_IsActive'])
            own=overrides.get(go,own)
            parent=resources.trees[resources.transforms[go]]['m_Father']['m_PathID']
            active_cache[go]=own and (active(resources.trees[parent]['m_GameObject']['m_PathID']) if parent else True)
        return active_cache[go]
    def text_slot(key):
        candidates=[t['_text']['m_PathID'] for t in resources.trees.values() if t.get('_key')==key and '_text' in t and active(t['m_GameObject']['m_PathID'])]
        if len(candidates)!=1:raise UnsupportedLayout('Original active trainer text slot is ambiguous',key=key,candidates=candidates)
        return candidates[0]
    selected={'name':text_slot(200),
        'description':text_slot(203),'rule':text_slot(204),
        'illustrator':-6518472013036924244,'illustrator_prefix':-3685445872072076628}
    name,np=locale.lookup('Master',card['name_key'],262)
    description,dp=locale.lookup('Master',trainer['DescriptionMSID'],190)
    dp=dict(dp,original_template=description,original_parameters=trainer.get('TrainerLogicParameters') or [])
    reference_audit=[]
    description=format_description(description,trainer.get('TrainerLogicParameters') or [],rich_text=True,locale=locale,reference_audit=reference_audit)
    dp['reference_audit']=reference_audit
    rule_key={0:'goods_description',1:'support_description',2:'stadium_description',3:'pokemon_tool_description',4:'goods_description'}[asset_type]
    rule,rp=locale.lookup('UI',rule_key,102)
    artist,ap=locale.lookup('Master',card.get('artist_key'),162,allow_empty=True)
    prefix,pp=locale.lookup('UI','illustrator',102)
    values={'name':(name,np,'bold'),'description':(description,dp,'medium'),'rule':(rule,rp,'medium'),
        'illustrator':(artist,ap,'number'),'illustrator_prefix':(prefix,pp,'number')}
    spec={'card_id':card['asset_id'],'card_selector':card['id'],'language':language,'locale':locale.directory,
        'font_metrics':fonts,'original_card':card,'texts':[],'render_size':[1468,2048],
        'sprites':[],'sprite_assets':[],'sprite_bindings':[],'mask_sprite_bindings':[],'layout_profile':'trainer-families-v2',
        'artist_group_rect':_rect(resources,9173784981664354988),
        'limitations':['Original Item/Supporter/Tool/Fossil/Stadium UI; unsupported MS/TMP commands fail explicitly.',
            'Serialized rectangles, original font/language conditions and preferred-width artist group are evaluated; full Unity runtime is not executed.']}
    if asset_type==4:
        parameters=trainer.get('TrainerLogicParameters') or []
        try:hp=str(int(parameters[0]))
        except (ValueError,IndexError):raise UnsupportedLayout('Original fossil HP parameter is invalid',parameters=parameters)
        selected.update(hp_value=text_slot(101),hp_label=text_slot(117))
        values.update(hp_value=(hp,{'authority':'original-master-and-native-method','field':'TrainerLogicParameters[0]','method':'TrainerEntity.GetFossilHp','virtual_address':'0x44EE18C'},'number'),
            hp_label=(*locale.lookup('UI','hp',102),'bold'))
        hpgo=resources.trees[selected['hp_value']]['m_GameObject']['m_PathID']
        parent=resources.trees[resources.transforms[hpgo]]['m_Father']['m_PathID']
        spec['fossil_hp_group_rect']=_rect(resources,resources.trees[parent]['m_GameObject']['m_PathID'])
    for sid,pid in selected.items():
        value,provenance,role=values[sid]
        field=_field(resources,pid,sid,value,provenance,locale,fonts,role)
        field['wrap']=sid in ('description','rule')
        if sid=='name':field['fit_font']=True
        spec['texts'].append(field)
    font_conditions,font_file=resources.resolve(design,design_data['_fonts'])
    spec['typography_source_audit']=resources.typography(spec,card,language,fonts,font_file,output_dir=output)
    spec['original_design_source']={'design':resources.source(design,design_file),'dynamic_ui_overrides':overrides,
        'font_conditions':resources.source(font_conditions,font_file),'card_settings':resources.source(settings,settings_file),
        'master_trainer_type':trainer['TrainerType'],'asset_trainer_type':asset_type,'rule_label':rule_key}
    order={}
    def visit(go):
        order[go]=len(order)
        for p in resources.trees[resources.transforms[go]]['m_Children']:
            visit(resources.trees[p['m_PathID']]['m_GameObject']['m_PathID'])
    for go in resources.gos:
        if not resources.trees[resources.transforms[go]]['m_Father']['m_PathID']:visit(go)
    for go in sorted(resources.gos,key=order.get):
        tree=resources.gos[go]
        path=_path(resources,go)
        if not path.startswith(('TrainersCardUI/Base/','TrainersCardUI/AncientView/','TrainersCardUI/FutureView/')) or not active(go):continue
        for component in tree['m_Component']:
            pid=component['component']['m_PathID'];data=resources.trees[pid]
            if 'm_Sprite' not in data or not data['m_Sprite']['m_PathID'] or not data.get('m_Enabled',True):continue
            sid='trainer-'+str(pid);sprite=resources.export_sprite(sid,resources.objects[pid],data['m_Sprite'],output/'ui-resources')
            spec['sprite_assets'].append(sprite)
            binding={'id':sid,'source_sprite':sid,'render_rect_top_left':_rect(resources,go),
                'confidence':'original-trainer-image-and-conditional-activation','source_path':path,'source_layer':tree['m_Layer']}
            if path.startswith(('TrainersCardUI/AncientView/','TrainersCardUI/FutureView/')):
                from .dynamic_ui import original_image_binding
                binding=original_image_binding(resources,go,pid,sid)
            if tree['m_Layer']==18:spec['mask_sprite_bindings'].append(deepcopy(binding))
            spec['sprite_bindings'].append(binding)
    spec['category_mask_complete']=True
    spec['category_mask_routing']='Source layer18 Images are partitioned into Holo before rasterization; a texture is consumed only by original DynamicUI components.'
    bind_trainer_layout(spec,card,fonts)
    spec['glyph_coverage']=glyph_coverage(spec['texts'],fonts)
    return spec
