"""Source-bound layout subset for regular Pokémon cards."""
from .localization import ENERGY_NAMES, UnsupportedLayout


def validate_layout_profile(card):
    pokemon = card.get("pokemon") or {}
    attacks = card.get("attacks") or []
    from .evolution import validate_evolution
    validate_evolution(card)
    from .pokemon_rules import validate_rule_profile
    validate_rule_profile(card)
    from .skills import validate_skills
    validate_skills(card)
    attack = attacks[0]
    if len(pokemon.get("PokemonTypes", [])) != 1:
        raise UnsupportedLayout("Only one displayed Pokémon type is implemented")
    # Original PokemonRepository.ToEntity converts WeaknessType=0 to a
    # nullable without a value; it is not an energy icon enum.
    weakness = pokemon.get("WeaknessType")
    energies = pokemon["PokemonTypes"] + attack.get("AttackCost", [])
    if weakness != 0:
        energies.append(weakness)
    if any(v not in ENERGY_NAMES for v in energies):
        raise UnsupportedLayout("An original energy/type resource is unavailable", energy_values=energies)
    if len(attack.get("AttackCost", [])) > 5:
        raise UnsupportedLayout("Attack energy row exceeds implemented original slots")
    if not 0 <= pokemon.get("RetreatAmount", -1) <= 5:
        raise UnsupportedLayout("Retreat energy row exceeds original available slots")
    raw = card.get("card") or card.get("raw_records", {}).get("PokemonCard", {})
    if set(pokemon.get("AdditionalCategories") or [])-{1,2,3}:
        raise UnsupportedLayout("Original Pokémon category is not recovered")
    # Promotion/event logos are original meshes/materials instantiated in the
    # card's L prefab. The scene compiler validates those resources and keeps
    # their serialized transforms; no fabricated 2D promotion mark is needed.
    if len(raw.get("IllustratorNameMSIDs", [])) > 1:
        raise UnsupportedLayout("Multiple-illustrator layout is not implemented")


def preferred_advance(text, font, size, spacing=0):
    glyphs = {v["m_Index"]: v for v in font["glyphs"]}
    result = 0
    for character in text:
        if character in "\r\n":
            continue
        match = font["unicode_lookup"].get(str(ord(character)))
        if match is None:
            return None
        glyph = glyphs[match["glyph_index"]]
        result += glyph["m_Metrics"]["m_HorizontalAdvance"] * size / font["face_info"]["m_PointSize"] * font["face_info"]["m_Scale"] * match["scale"] * glyph["m_Scale"]
    return result + max(0, len(text)-1) * spacing * size / 100


def bind_layout(spec, card, fonts):
    """Evaluate the source-backed subset, including the zero-width retreat group."""
    pokemon, attack = card["pokemon"], card["attacks"][0]
    texts = {v["id"]: v for v in spec["texts"]}
    cost = attack["AttackCost"]
    padding = 174 if len(cost) <= 4 else 218
    texts["attack"]["render_rect_top_left"] = [52+padding, 677, 378, 34.01]
    texts["damage"]["render_rect_top_left"] = [581, 673, 100, 40]
    for key in ("attack", "damage"):
        texts[key]["layout_confidence"] = "source-derived-single-attack-no-description"
    hp = texts["hp_value"]
    hp_width = preferred_advance(hp["text"], fonts["number"], hp["font_size"], hp["character_spacing_tmp_units"])
    if hp_width is not None:
        hp["render_rect_top_left"] = [636-hp_width, 40, hp_width, 54]
        texts["hp_label"]["render_rect_top_left"] = [636-hp_width-36, 60, 40, 30]
    prefix = texts["illustrator_prefix"]
    prefix_width = preferred_advance(prefix["text"], fonts["number"], prefix["font_size"], prefix["character_spacing_tmp_units"])
    if prefix_width is not None:
        prefix_width = max(15.600000381469727, prefix_width)
        prefix["render_rect_top_left"] = [53, 913, prefix_width, 24]
        texts["illustrator"]["render_rect_top_left"] = [53+prefix_width, 913, 170-prefix_width, 24]
    bindings = []
    # Empty AttackCost is displayed as one original EnergyType.Empty slot.
    # It is distinct from a null weakness and never enters ENERGY_NAMES.
    for index, value in enumerate(cost or [None]):
        left = 47 + index*42.5
        bindings.extend([
            {"id": "cost_%d_outline" % index, "source_sprite": "energy_outline",
             "render_rect_top_left": [left, 671, 41.5, 41.5], "confidence": "original-icon-size-and-layout-spacing"},
            {"id": "cost_%d" % index, "source_sprite": "empty" if value is None else ENERGY_NAMES[value],
             "render_rect_top_left": [left+1.8, 672.8, 37.9, 37.9], "confidence": "original-inner-icon-inset"}])
    # CardEnergyIconView.SetEnergy(null) clears and disables imgEnergy.
    if pokemon["WeaknessType"] != 0:
        bindings.append({"id": "weakness_type", "source_sprite": ENERGY_NAMES[pokemon["WeaknessType"]],
                         "render_rect_top_left": [209.14000952243805,881.2999997138977,27.40000033378601,27.40000057220459],
                         "confidence": "original-static-child-rect"})
    retreat_source = {
        "source": "Common/CardNew/System/Prefabs/PokemonCardUI.prefab_bundles",
        "attributes_rect_transform_id": -8594024216352516931,
        "horizontal_layout_component_id": -6216037333364501315,
        "container_left": 504.0, "container_width": 0.0, "alignment": "MiddleLeft",
        "child_width": 31.0, "spacing": 1.0, "inner_inset": 1.8,
        "derivation": "SetLayoutHorizontal places the first31-unit child at zero-width container left504; inner image begins at505.8. Saved child center positions must not be reused as final layout.",
    }
    for index in range(pokemon["RetreatAmount"]):
        bindings.append({"id": "retreat_%d" % index, "source_sprite": "colorless",
                         "render_rect_top_left": [505.8 + index*32, 881.3, 27.4, 27.4],
                         "confidence": "original-horizontal-layout-group-evaluation",
                         "provenance": retreat_source})
    bindings.extend([
        {"id": "type_outline", "source_sprite": "energy_outline", "render_rect_top_left": [644,36,54,54],
         "confidence": "original-static-child-rect"},
        {"id": "type", "source_sprite": ENERGY_NAMES[pokemon["PokemonTypes"][0]],
         "render_rect_top_left": [645.8,37.8,50.4,50.4], "confidence": "original-static-child-rect"}])
    spec["sprite_bindings"] = bindings
    spec["sprite_bindings"].extend(spec.get('evolution_sprite_bindings',[]))
    from .skills import bind_skill_layout
    skills = bind_skill_layout(spec, card, fonts)
    if not skills['legacy'] and not skills.get('pending_original_glyphs'):
        spec['sprite_bindings'] = [b for b in spec['sprite_bindings'] if not b['id'].startswith('cost_')]
        spec['sprite_bindings'].extend(skills['energy_bindings'])
    for binding in spec.get('ability_sprite_bindings',[]):
        binding['render_rect_top_left'] = spec['skills']['ability_badge_rect_top_left']
        spec['sprite_bindings'].append(binding)
    spec["layout_profile"] = "pokemon-basic-one-attack-window-v1"
    if pokemon.get('EvolutionStage') != 1 or not spec.get('skills',{}).get('legacy',True):
        spec['layout_profile']='pokemon-regular-skills-evolution-v2'
    spec["layout_adjustments"] = [{"id": "retreat-spacing", "old_first_inner_x": 490.3,
                                    "new_first_inner_x": 505.8, "source": retreat_source}]
    if spec.get('pokemon_rules'):
        from .pokemon_rules import bind_rule_layout
        bind_rule_layout(spec,card,fonts)
    from .owner_names import bind_owner_layout
    bind_owner_layout(spec,card,fonts)
    if not pokemon.get('IsEX') and not pokemon.get('OwnerNameID'):
        # Common titles also run CardTextConfig AutoCondensed in the game.
        # Sized AdditionalName runs otherwise bypass the legacy geometry path
        # and can extend past the serialized title rectangle into the HP field.
        from .text_layout import measure_text
        name=texts['name']
        source=name.get('auto_condense_source_input',{})
        probe=dict(name,wrap=False,fit_font=False,character_width_scale=1)
        for key in ('font_size','horizontal_scale','character_spacing_tmp_units'):
            if key in source:probe[key]=source[key]
        try:
            width=measure_text(probe,fonts[name['font_id']],fonts=fonts)['width']
        except ValueError as error:
            if 'Missing original glyph' not in str(error):raise
            # The same layout is evaluated again after original font population.
        else:
            maximum=name['render_rect_top_left'][2]
            if width>maximum:
                from .font_fit import fit_single_line
                fit_single_line(name,fonts[name['font_id']],maximum,fonts)
    spec['sprite_bindings'].extend(spec.get('category_sprite_bindings',[]))
    spec['sprite_bindings'].extend(spec.get('mega_source_sprite_bindings',[]))
    from .mega_source import bind_mega_source_layout
    bind_mega_source_layout(spec, fonts)
    return spec
