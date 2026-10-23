"""Original Mega predecessor tag, independently of the ordinary evolution stage.

The native SetPreMegaEvolvedView (0x47F8A70) binds text keys 119/120,
selects the multiline views from the localized line break, and uses font
text type 4 for that branch. All wording, sprites and rectangles are local.
"""
from copy import deepcopy
import re

from .localization import UnsupportedLayout
from .skills import _field, _path

PREFIX = 'PokemonCardUI/PokemonRainbowCategoryTagView/'


def add_mega_source_text(spec, card, locale, resources, fonts):
    pokemon = card.get('pokemon') or {}
    if not pokemon.get('IsMegaEvolution'):
        return spec
    from .evolution import _characters
    character_id = pokemon.get('MegaEvolutionPreevolvedCharacterID')
    character = _characters(str(resources.root.resolve())).get(character_id)
    if not character or not character.get('DisplayNameMSID'):
        raise UnsupportedLayout('Original Mega predecessor character is unavailable', character_id=character_id)
    name, name_source = locale.lookup('Master', character['DisplayNameMSID'], 262)
    pattern, pattern_source = locale.lookup('UI', 'megaex_pokemon_evolution_from', 102)
    tags = ('[Mst:CardCharacterName ]', '[Mst:CardCharacterName]')
    if sum(pattern.count(tag) for tag in tags) != 1:
        raise UnsupportedLayout('Unsupported original Mega predecessor reference', value=pattern)
    value = pattern
    for tag in tags:
        value = value.replace(tag, name)
    multiline = bool(re.search(r'\r\n|\r|\n', value))
    selected = []
    for pid, data in resources.trees.items():
        if data.get('_key') not in (119, 120) or '_lineType' not in data:
            continue
        go = data['m_GameObject']['m_PathID']
        if not _path(resources, go).startswith(PREFIX) or data['_lineType'] != int(multiline):
            continue
        key = data['_key']
        field = _field(resources, data['_text']['m_PathID'],
            'mega_source' if key == 119 else 'mega_source_outline', value,
            {'authority': 'original-local-master-and-locale', 'name': name_source,
             'format': pattern_source, 'format_template': pattern,
             'previous_character_id': character_id}, locale, fonts, 'medium')
        field.update(source_layer=resources.gos[go]['m_Layer'], wrap=False,
            original_text_type=4 if multiline else 1,
            layout_confidence='original-Mega-predecessor-line-type-and-static-rect')
        selected.append(field)
    if sorted(f['id'] for f in selected) != ['mega_source', 'mega_source_outline']:
        raise UnsupportedLayout('Original Mega predecessor text views are ambiguous')
    spec['texts'].extend(selected)
    spec['mega_source'] = {'previous_character_id': character_id, 'multiline': multiline,
        'native_method': 'PokemonCardUIBehaviour.SetPreMegaEvolvedView',
        'virtual_address': '0x47F8A70', 'text_keys': [119, 120]}
    return spec


def add_mega_source_resources(spec, card, locale, resources, output_dir):
    if not spec.get('mega_source'):
        return spec
    from .pokemon_rules import card_design, _active_function
    from .dynamic_ui import original_image_binding
    design = card_design(resources.root, card, resources)
    active = _active_function(resources, card, locale.language, design['dynamic_ui_overrides'])
    order = []
    def visit(go):
        order.append(go)
        for child in resources.trees[resources.transforms[go]]['m_Children']:
            visit(resources.trees[child['m_PathID']]['m_GameObject']['m_PathID'])
    for go in resources.gos:
        if not resources.trees[resources.transforms[go]]['m_Father']['m_PathID']:
            visit(go)
    bindings = []
    for go in order:
        if not _path(resources, go).startswith(PREFIX) or not active(go):
            continue
        for component in resources.gos[go]['m_Component']:
            pid = component['component']['m_PathID']
            data = resources.trees[pid]
            if not data.get('m_Sprite', {}).get('m_PathID'):
                continue
            sid = 'mega-source-' + str(pid)
            spec['sprite_assets'].append(resources.export_sprite(sid, resources.objects[pid], data['m_Sprite'], output_dir))
            bindings.append(original_image_binding(resources, go, pid, sid))
    if not bindings:
        raise UnsupportedLayout('Original Mega predecessor tag Images are unavailable')
    spec['mega_source_sprite_bindings'] = bindings
    spec['sprite_bindings'].extend(deepcopy(bindings))
    bind_mega_source_layout(spec, spec['font_metrics'])
    return spec


def bind_mega_source_layout(spec, fonts):
    """Apply the source's AutoCondensed single-line contract after glyph loading."""
    if not spec.get('mega_source') or spec['mega_source']['multiline']:
        return spec
    from .font_fit import fit_single_line
    for field in spec['texts']:
        if field['id'] not in ('mega_source', 'mega_source_outline'):
            continue
        try:
            fit_single_line(field, fonts[field['font_id']], field['render_rect_top_left'][2], fonts)
        except ValueError as error:
            if 'Missing original glyph' not in str(error):
                raise
            # The standard font population stage rebinds the same layout.
    return spec
