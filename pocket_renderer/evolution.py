"""Source-bound Stage1/Stage2 badges, previous-Pokémon text and miniature.

Uses the selected original PokemonCardUI and locale. EvolutionStage values
1/2/3 mean Basic/Stage1/Stage2 in the original master/Asset3D stage conditions.
No card artwork or translated wording is borrowed from another card.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib

from .localization import LANGUAGES, UnsupportedLayout, apply_language_parameters, normalize_language

SOURCE_TEXT_ID = 88252315953337533
SOURCE_IMAGE_ID = 157969315679638717


def validate_evolution(card):
    pokemon = card.get('pokemon') or {}
    stage = pokemon.get('EvolutionStage')
    if stage not in (1, 2, 3):
        raise UnsupportedLayout('Unknown original Pokémon evolution stage', stage=stage)
    if stage > 1 and not pokemon.get('PreevolvedCharacterID'):
        raise UnsupportedLayout('Evolved Pokémon has no original predecessor character')


@lru_cache(maxsize=4)
def _characters(root):
    from .catalog import Catalog
    return {v['CharacterID']:v for v in Catalog(root).table('Character')}


def predecessor(card, root, catalog=None):
    validate_evolution(card)
    pokemon = card['pokemon']
    if pokemon['EvolutionStage'] == 1:
        return None
    character_id = pokemon['PreevolvedCharacterID']
    chars = {v['CharacterID']:v for v in catalog.table('Character')} if catalog else _characters(str(Path(root).resolve()))
    character = chars.get(character_id)
    if not character or not character.get('DisplayNameMSID'):
        raise UnsupportedLayout('Original predecessor name is unavailable', character_id=character_id)
    return character


def _top_rect(resources, go):
    x,y,w,h = resources.rect(go)
    return [x, 1024-y-h, w, h]


def _path(resources, go):
    tr = resources.trees[resources.transforms[go]]
    parent = tr['m_Father']['m_PathID']
    prefix = _path(resources,resources.trees[parent]['m_GameObject']['m_PathID'])+'/' if parent else ''
    return prefix+resources.gos[go]['m_Name']


def add_evolution_text(spec, card, language, resources, locale, catalog=None):
    """Append the original source_txt before resources.typography(spec,...).

    Call only once before typography selects each field's font/material. The
    selected source's Character table supplies the previous Pokémon name.
    """
    validate_evolution(card)
    if card['pokemon']['EvolutionStage'] == 1:
        return spec
    if any(t['id'] == 'evolution_source' for t in spec['texts']):
        raise ValueError('Evolution source text is already bound')
    character = predecessor(card,resources.root,catalog)
    name,name_source = locale.lookup('Master',character['DisplayNameMSID'],262)
    pattern,pattern_source = locale.lookup('UI','evolution_from',102)
    # This is a native dynamic text slot, not a different locale/reference name.
    accepted = ('[Mst:CardCharacterName ]','[Mst:CardCharacterName]')
    count = sum(pattern.count(tag) for tag in accepted)
    if count != 1:
        raise UnsupportedLayout('Unsupported original evolution_from reference', value=pattern)
    value = pattern
    for tag in accepted:
        value = value.replace(tag,name)
    raw = deepcopy(resources.trees[SOURCE_TEXT_ID])
    go = raw['m_GameObject']['m_PathID']
    companions = [{'id':c['component']['m_PathID'],
                   'data':deepcopy(resources.trees[c['component']['m_PathID']])}
                  for c in resources.gos[go]['m_Component']
                  if resources.objects[c['component']['m_PathID']].type.name == 'MonoBehaviour'
                  and c['component']['m_PathID'] != SOURCE_TEXT_ID]
    rect = _top_rect(resources,go)
    item = {'id':'evolution_source','kind':'text','text':value,
        'content_provenance':{'authority':'original-local-master-and-locale',
            'previous_character_id':character['CharacterID'],'name':name_source,
            'format':pattern_source,'format_template':pattern},
        'source_node':{'source':resources.UI,'object_id':SOURCE_TEXT_ID,
                       'game_object_id':go,'path':_path(resources,go)},
        'static_rect_top_left':rect,'render_rect_top_left':rect,
        'horizontal_alignment':{1:'left',2:'center',4:'right'}[raw['m_HorizontalAlignment']],
        'vertical_alignment':{256:'top',512:'middle',1024:'bottom'}[raw['m_VerticalAlignment']],
        'font_style_bits':raw['m_fontStyle'],'font_file':'TPC_GillSansNovaMedium.otf',
        'original_tmp':raw,'companion_components':companions,
        'layout_confidence':'original-static-evolution-source-rect',
        'notes':['Original Pokemon.PreevolvedCharacterID -> Character.DisplayNameMSID and UI evolution_from slot.']}
    apply_language_parameters(item,language)
    spec['texts'].append(item)
    spec['evolution'] = {'stage':card['pokemon']['EvolutionStage'],
        'previous_character_id':character['CharacterID'],'previous_name':name,
        'text_source':resources.source(resources.objects[SOURCE_TEXT_ID],resources.UI)}
    return spec


def stage_badge(resources, stage, language, output_dir):
    enum = LANGUAGES[normalize_language(language)]['asset_enum']
    candidates = []
    for go,value in resources.gos.items():
        if not value['m_Name'].startswith('phase_txt_img_'):
            continue
        components = [c['component']['m_PathID'] for c in value['m_Component']]
        conditions = [entry for pid in components for entry in resources.trees[pid].get('_conditions',[])]
        if any(c.get('_stage') == stage and c.get('_language') == enum for c in conditions):
            images = [pid for pid in components if 'm_Sprite' in resources.trees[pid]]
            if len(images) != 1:
                raise UnsupportedLayout('Original stage badge Image pointer is ambiguous')
            candidates.append((go,images[0],conditions))
    if len(candidates) != 1:
        raise UnsupportedLayout('Original stage badge locale condition is ambiguous',stage=stage,language=language)
    go,image_id,conditions = candidates[0]
    sprite = resources.export_sprite('stage',resources.objects[image_id],resources.trees[image_id]['m_Sprite'],output_dir)
    sprite['language_condition'] = {'enum':enum,'stage':stage,'conditions':conditions,
        'source_game_object':go,'source_name':resources.gos[go]['m_Name']}
    return sprite,_top_rect(resources,go)


def _miniature(resources, character_id, output_dir):
    # Native Addressables path pattern for the original CharacterId image.
    if (not character_id or character_id in ('.', '..') or character_id != character_id.rstrip(' .')
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in character_id)):
        raise UnsupportedLayout('Unexpected original character image identifier',character_id=character_id)
    relative = 'Common/CardNew/Pokemon/%s/%s.png_bundles' % (character_id,character_id)
    source = resources.src/relative
    alias = None
    if not source.is_file():
        # Some extracted Windows filenames preserve UTF-8 bytes as cp1252.
        # The Sprite's own canonical Unicode name must still match below.
        try:
            extracted = character_id.encode('utf8').decode('cp1252')
        except UnicodeError:
            extracted = character_id
        if extracted != character_id and extracted.encode('cp1252').decode('utf8') == character_id:
            candidate = 'Common/CardNew/Pokemon/%s/%s.png_bundles' % (extracted, extracted)
            if (resources.src/candidate).is_file():
                relative, source = candidate, resources.src/candidate
                alias = {'canonical_character_id': character_id, 'extracted_character_id': extracted,
                         'operation': 'reversible UTF-8 bytes decoded as Windows-1252'}
    if not source.resolve().is_relative_to(resources.src.resolve()):
        raise UnsupportedLayout('Original predecessor miniature path leaves source directory')
    if not source.is_file():
        raise UnsupportedLayout('Original predecessor miniature is not downloaded',file=relative)
    env = resources.load(relative)
    sprites = [o for o in env.objects if o.type.name == 'Sprite' and o.read().m_Name == character_id]
    if len(sprites) != 1:
        raise UnsupportedLayout('Original predecessor Sprite is ambiguous',file=relative)
    sprite = sprites[0]
    data = sprite.read_typetree()
    pointer = data['m_RD']['texture']
    if pointer['m_FileID'] != 0:
        raise UnsupportedLayout('External predecessor image texture is not implemented',file=relative)
    textures = [o for o in env.objects if o.path_id == pointer['m_PathID'] and o.type.name == 'Texture2D']
    if len(textures) != 1:
        raise UnsupportedLayout('Original predecessor image texture is unavailable',file=relative)
    texture_obj = textures[0]
    texture = texture_obj.read()
    image = texture.image
    if image.size != (texture.m_Width,texture.m_Height):
        raise ValueError('Native miniature dimensions changed')
    out = Path(output_dir)
    out.mkdir(parents=True,exist_ok=True)
    path = out/'evolution_previous.texture.png'
    image.save(path)
    rect = data['m_RD']['textureRect']
    w,h = image.size
    return {'id':'evolution_previous','source':resources.source(sprite,relative),
        'filename_encoding_alias':alias,
        'rect':data['m_Rect'],'border':data['m_Border'],'pivot':data['m_Pivot'],
        'pixels_to_units':data['m_PixelsToUnits'],'texture_rect':rect,
        'texture_rect_offset':data['m_RD']['textureRectOffset'],
        'outer_uv_rect_derived_from_texture_rect':[rect['x']/w,rect['y']/h,(rect['x']+rect['width'])/w,(rect['y']+rect['height'])/h],
        'texture':{'source':resources.source(texture_obj,relative),'png':str(path.resolve()),
            'width':w,'height':h,'format':int(texture.m_TextureFormat),
            'png_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'sampler_settings':texture_obj.read_typetree()['m_TextureSettings'],
            'color_space':texture.m_ColorSpace,'conversion':'Native predecessor texture decode; no resizing, sharpening or synthetic pixels.'}}


def add_evolution_resources(spec, card, language, resources, output_dir):
    """Replace the basic badge and add the original predecessor miniature.

    Call after common resources and bind_layout. On future layout rebinds,
    append spec['evolution_sprite_bindings'] to the resulting sprite bindings.
    The legacy basic_badge binding id is retained for CLI preservation only;
    its source_sprite is 'stage', pointing to the actual stage2/3 asset.
    """
    validate_evolution(card)
    stage = card['pokemon']['EvolutionStage']
    if stage == 1:
        return spec
    badge,badge_rect = stage_badge(resources,stage,language,output_dir)
    image = _miniature(resources,card['pokemon']['PreevolvedCharacterID'],output_dir)
    spec['sprite_assets'] = [s for s in spec['sprite_assets'] if s['id'] not in ('basic','stage','evolution_previous')]+[badge,image]
    badge_binding = {'id':'basic_badge','source_sprite':'stage','render_rect_top_left':badge_rect,
                     'confidence':'original-stage-and-language-condition'}
    previous_go = resources.trees[SOURCE_IMAGE_ID]['m_GameObject']['m_PathID']
    binding = {'id':'evolution_previous','source_sprite':'evolution_previous',
        'render_rect_top_left':_top_rect(resources,previous_go),
        'confidence':'original-source-image-static-rect',
        'provenance':{'original_image_component':SOURCE_IMAGE_ID,'image_switch_key':1,
            'previous_character_id':card['pokemon']['PreevolvedCharacterID'],
            'sprite_source':image['source']}}
    spec['sprite_bindings'] = [b for b in spec['sprite_bindings'] if b['id'] not in ('basic_badge','evolution_previous')]
    spec['sprite_bindings'][0:0] = [badge_binding,binding]
    spec['evolution_sprite_bindings'] = [binding]
    return spec
