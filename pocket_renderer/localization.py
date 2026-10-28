"""Original Pocket localization and UI resources for supported card layouts.

No translation service, character substitution or raster resizing is used.
Missing dynamic glyphs are reported to the caller for original-font population.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import warnings
import re
import math
from .text_tokens import characters

LANGUAGES = {"en-US": {"directory": "en_US", "asset_enum": 1},
             "pt-BR": {"directory": "pt_BR", "asset_enum": 8},
             "ja-JP": {"directory": "ja_JP", "asset_enum": 0}}
LANGUAGE_SOURCE = {
    "source": "original-render-pipeline/metadata/il2cpp-dump.cs",
    "type": "Lettuce.Infrastructure.Asset3D.Core.Data.LanguageType",
    "type_definition_index": 42069,
    "note": "This is the Asset3D enum, not the differently ordered Domain.Common enum.",
}
ENERGY_NAMES = {1: "colorless", 2: "grass", 3: "fire", 4: "water", 5: "lightning",
                6: "psychic", 7: "fighting", 8: "darkness", 9: "metal", 10: "dragon"}


class LocalizationError(ValueError):
    code = "localization_error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class UnsupportedLayout(LocalizationError):
    code = "unsupported_layout"


def normalize_language(language):
    from .config import normalize_language as normalize
    try:
        return normalize(str(language))
    except ValueError as error:
        raise LocalizationError("Unsupported language: " + str(language), supported=list(LANGUAGES)) from error


@lru_cache(maxsize=24)
def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


class LocaleRepository:
    """Read verified decoded KVROM tables; always preserve the selected locale."""
    def __init__(self, root, language):
        self.root = Path(root).resolve()
        self.language = normalize_language(language)
        self.directory = LANGUAGES[self.language]["directory"]
        self._indices = {}
        self._categories = {}

    def category(self, table, canonical):
        """Resolve category IDs through original attribute names, not locale order.

        Numeric API aliases name the original audited categories. Resolve their
        stable attribute names in this version and language, including en_US.
        """
        if canonical is None:
            return canonical, None
        key = (table, canonical)
        if key not in self._categories:
            base = self.root/"reports/file-only-analysis/locale-original"
            from .config import CODE_ROOT
            reference = _read_json(str(CODE_ROOT/'profiles/locale-categories-v1.json'))
            name = canonical if isinstance(canonical, str) else reference['tables'].get(table, {}).get(str(canonical))
            data = _read_json(str(base/self.directory/(table+"_Attribute.json")))
            matches = [r for r in data["entries"] if r["key"] == name and len(r["value"]) == 1]
            if not name or len(matches) != 1:
                raise LocalizationError("Original locale attribute mapping is ambiguous", table=table, category=canonical)
            row = matches[0]
            self._categories[key] = (ord(row["value"]), {
                "attribute":row["key"], "source":data["source"], "source_sha256":data["sha256"],
                "entry_offset":row["entry_offset"], "canonical_category":canonical})
        return self._categories[key]

    def resolve_references(self, value):
        """Expand original named Master references used by collection titles."""
        pattern = re.compile(r'\[Mst:([A-Za-z0-9_]+)\s+v="([A-Za-z0-9_]+)"\s*\]')
        for _ in range(8):
            if not pattern.search(value):
                return value
            def replace(match):
                path = self.root/'reports/file-only-analysis/locale-original/en_US/Master_Attribute.json'
                rows = _read_json(str(path))['entries']
                attribute = 'LocalizeMaster'+match[1]
                selected = [r for r in rows if r['key'] == attribute and len(r['value']) == 1]
                if len(selected) != 1:
                    raise LocalizationError('Unknown original Master reference attribute', attribute=attribute)
                return self.lookup('Master',match[2],attribute)[0]
            updated = pattern.sub(replace,value)
            if updated == value:
                break
            value = updated
        raise LocalizationError('Cyclic or excessively nested original Master reference')

    def lookup(self, table, key, category=None, allow_empty=False):
        if not key:
            if allow_empty:
                return "", {"authority": "original-master-empty", "key": key, "language": self.language}
            raise LocalizationError("Missing original localization key", table=table)
        if table not in self._indices:
            path = self.root / "reports/file-only-analysis/locale-original" / self.directory / (table + ".json")
            data = _read_json(str(path))
            index = {}
            for row in data["entries"]:
                if row["value"]:
                    index.setdefault(row["key"], []).append(row)
            self._indices[table] = (data, index)
        data, index = self._indices[table]
        category, category_source = self.category(table, category)
        rows = [r for r in index.get(key, []) if category is None or r["category"] == category]
        if not rows:
            raise LocalizationError("Original localized string is unavailable", language=self.language,
                                    table=table, key=key, category=category)
        values = {r["value"] for r in rows}
        if len(values) != 1:
            raise LocalizationError("Ambiguous original localization key; category required", table=table, key=key)
        row = rows[0]
        return row["value"], {"authority": "original-localization", "language": self.language,
                              "source": data["source"], "source_sha256": data["sha256"],
                              "table": table, "key": key, "category": row["category"],
                              "category_provenance": category_source,
                              "entry_offset": row["entry_offset"]}


def apply_language_parameters(text, language):
    """Apply each CardTextConfig's first matching serialized language row.

    Original Setup0x4810f10 uses Enumerable.FirstOrDefault at0x481104c,
    before testing any override flags. Matching masks are not merged.
    """
    language = normalize_language(language)
    language_enum = LANGUAGES[language]["asset_enum"]
    mask = 1 << language_enum
    raw = text["original_tmp"]
    text["font_size"] = raw["m_fontSize"]
    text["character_spacing_tmp_units"] = raw.get("m_characterSpacing", 0)
    text["line_spacing_tmp_units"] = raw.get("m_lineSpacing", 0)
    text["word_wrapping_ratios"] = raw.get("m_wordWrappingRatios", 0.4)
    text["language_position_y_offset"] = 0
    selected = []
    for component in text.get("companion_components", []):
        for params in component["data"].get("_parameters", []):
            if not params.get("_language", 0) & mask:
                continue
            selected.append(deepcopy(params))
            if params.get("_overrideFontSize"):
                text["font_size"] = params["_fontSize"]
            if params.get("_overrideCharacterSpacing"):
                text["character_spacing_tmp_units"] = params["_characterSpacing"]
            if params.get("_overrideLineSpacing"):
                text["line_spacing_tmp_units"] = params["_lineSpacing"]
            if params.get("_overrideWordWrappingRatios"):
                text["word_wrapping_ratios"] = params["_wordWrappingRatios"]
            text["language_position_y_offset"] = params.get("_positionYOffset", 0)
            break
    text["font_size_source"] = {"original_tmp": raw["m_fontSize"],
                                "selected_language_parameters": selected,
                                "language_enum": language_enum, "language_mask": mask,
                                "enum_provenance": LANGUAGE_SOURCE}
    return text


def glyph_coverage(texts, fonts):
    from .text_tokens import font_characters
    missing = []
    for text in texts:
        for role,character in sorted(set(font_characters(text))):
            if character in ('\n','\r'):continue
            lookup=fonts[role]['unicode_lookup']
            if str(ord(character)) not in lookup:
                missing.append({"text_id": text["id"], "font_id": role, "character": character,
                                "codepoint": "U+%04X" % ord(character),
                                "unicode": ord(character), "original_font_file": fonts[role]["original_font_file"]})
    return {"complete": not missing, "missing": missing,
            "policy": "Populate from the original source font or fail; never substitute or remove characters."}


def _bind_rule_vertex_color(text, key):
    """Preserve the rule TMP vertex tint across original font/material tags.

    Native CardTextView.UpdateText sets font/material, not color. The rule's
    empty LtUIText style key skips the only style initialization that could
    change this serialized tint. This contract is recovered for rule keys
    111/121; other text initialization contexts remain independent.
    """
    if key not in (111,121):
        return
    controllers=[c for c in text['companion_components'] if '_styleKey' in c['data']]
    # Original Mega fields have no LtUIText companion at all: no style
    # initialization can overwrite their white TMP tint. Ordinary EX fields
    # have exactly one companion with an empty style key.
    no_mega_style=key==121 and not controllers
    if not no_mega_style and (len(controllers)!=1 or controllers[0]['data']['_styleKey']):
        raise UnsupportedLayout('Original rule text style color initialization is not recovered',
            text_id=text['id'],key=key)
    raw=text['original_tmp']
    tint=raw.get('m_fontColor',{})
    color=[tint.get(c) for c in ('r','g','b','a')]
    packed=raw.get('m_fontColor32',{}).get('rgba')
    if (any(not isinstance(c,(int,float)) or not math.isfinite(c) or not 0<=c<=1 for c in color)
        or not isinstance(packed,int) or not 0<=packed<=0xffffffff
        or any(round(c*255)!=(packed>>(i*8))&255 for i,c in enumerate(color))):
        raise UnsupportedLayout('Original rule TMP vertex color representations differ',text_id=text['id'])
    text['vertex_color_rgba']=color
    text['vertex_color_provenance']={
        'authority':'original TMP serialized color, unchanged by recovered native initialization',
        'card_text_key':key,'original_color32':packed,
        'style_component_id':controllers[0]['id'] if controllers else None,
        'style_key':'' if controllers else None,'native_methods':{
            'LtUITextLogic.InitializeFontStyle':'0x4B172E4',
            'CardTextView.UpdateText':'0x4811514',
            'LtUICtrlTagCommand.PreProcessForTMP':'0x4A1D780'},
        'shader_operation':'in_COLOR0 * _FaceColor',
        'policy':'Font/material tags preserve the base TMP vertex color; no contrast-based recoloring.'}


class _OriginalUIResources:
    """Resolve native Sprite PPtrs lazily; export only native texture decoding."""
    UI = "Common/CardNew/System/Prefabs/PokemonCardUI.prefab_bundles"

    def __init__(self, root):
        import UnityPy
        from .native_profile import unity_version
        UnityPy.config.FALLBACK_UNITY_VERSION = unity_version(root)
        self.root = Path(root)
        self.src = self.root / "input/aladin-complete/output"
        rows = _read_json(str(self.root / "reports/file-only-analysis/dependencies-index.json"))
        self.index = {r["cab"].lower(): r["file"] for r in rows}
        self.envs = {}
        self.ui = self.load(self.UI)
        self.objects = {obj.path_id: obj for obj in self.ui.objects}
        self.trees = {pid: obj.read_typetree() for pid, obj in self.objects.items()}
        self.gos = {pid: value for pid, value in self.trees.items() if self.objects[pid].type.name == "GameObject"}
        self.transforms = {v["m_GameObject"]["m_PathID"]: k for k, v in self.trees.items()
                           if self.objects[k].type.name == "RectTransform"}
        self.rect_cache = {}

    def load(self, relative):
        import UnityPy
        if relative not in self.envs:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.envs[relative] = UnityPy.load(str(self.src / relative))
        return self.envs[relative]

    def resolve(self, owner, pointer):
        pid = pointer["m_PathID"]
        if not pid:
            raise LocalizationError("Original Sprite resource pointer is empty")
        sf = owner.assets_file
        cab = sf.name.lower() if pointer["m_FileID"] == 0 else sf.externals[pointer["m_FileID"]-1].name.lower()
        relative = self.UI if sf == self.ui.assets[0] and pointer["m_FileID"] == 0 else self.index.get(cab)
        if not relative:
            raise LocalizationError("Original UI resource CAB is not indexed", cab=cab, path_id=pid)
        for obj in self.load(relative).objects:
            if obj.path_id == pid and obj.assets_file.name.lower() == cab:
                return obj, relative
        raise LocalizationError("Original UI object is missing", cab=cab, path_id=pid)

    def source(self, obj, relative):
        return {"file": relative, "cab": obj.assets_file.name.lower(), "path_id": obj.path_id,
                "bundle_sha256": _bundle_hash(str(self.src / relative))}

    def typography(self, spec, card, language, fonts, condition_file=None,output_dir=None):
        """Follow original condition -> keyed settings -> language -> font/material PPtrs."""
        if card.get('pokemon'):
            # Rarity labels and illustration suffixes do not select typography.
            # A non-EX immersive Pokemon also selects Pokemon_OverNumber through
            # its own CardSettings, so using Normal here would lose that source.
            from .card_design import load_card_design,design_activation
            settings,settings_file,design,design_file=load_card_design(self,card)
            data=design.read_typetree()
            ui,ui_file=self.resolve(design,data['_uiBasePrefab'])
            if ui.assets_file.name.lower()!=self.ui.assets[0].name.lower() or ui.path_id not in self.gos:
                raise UnsupportedLayout('Original card design selects an unrecovered UI base prefab',
                    ui_base=self.source(ui,ui_file))
            owner,relative=self.resolve(design,data['_fonts'])
            if condition_file and Path(condition_file).as_posix().replace('\\','/')!=Path(relative).as_posix().replace('\\','/'):
                raise UnsupportedLayout('Original rule and card design font conditions differ',
                    rule_conditions=condition_file,design_conditions=relative)
            spec['original_design_source']={'card_settings':self.source(settings,settings_file),
                'design':self.source(design,design_file),'ui_base':self.source(ui,ui_file),
                'font_conditions':self.source(owner,relative),
                'dynamic_ui_overrides':design_activation(self,design)}
        elif condition_file:
            relative=condition_file
            choices=[obj for obj in self.load(relative).objects if obj.type.name=='MonoBehaviour' and '_default' in obj.read_typetree() and '_conditions' in obj.read_typetree()]
            if len(choices)!=1:raise UnsupportedLayout('Original font condition resource is ambiguous')
            owner=choices[0]
        else:
            raise UnsupportedLayout('Original card font condition source is unavailable')
        conditions = owner.read_typetree()
        # These are different original enums: MasterMemory and Asset3D Card.Core.Data.
        master_to_asset_energy = {1: 11, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8, 10: 10}
        master_energy=card['pokemon']['PokemonTypes'][0] if card.get('pokemon') else 0
        energy = master_to_asset_energy.get(master_energy,0)
        from .font_conditions import select_font_condition
        from .card_conditions import native_condition_contract,pokemon_features
        condition_contract=native_condition_contract(str(self.root))
        dual=pokemon_features(card)[0] if condition_contract['font_dual'] else False
        chosen_condition=select_font_condition(conditions['_conditions'],energy if card.get('pokemon') else None,
            pokemon_dual=dual,dual_predicate=condition_contract['font_dual'])
        selected=[chosen_condition] if chosen_condition is not None else []
        group_obj, group_file = self.resolve(owner, selected[0]["_font"] if selected else conditions["_default"])
        group = group_obj.read_typetree()
        role_ids = {font["font_path_id"]: role for role, font in fonts.items()}
        enum = LANGUAGES[normalize_language(language)]["asset_enum"]
        audit = []
        for text in spec["texts"]:
            keys = [c for c in text["companion_components"] if "_key" in c["data"]]
            if len(keys) != 1:
                raise LocalizationError("Original font key is ambiguous", text_id=text["id"])
            key = keys[0]["data"]["_key"]
            _bind_rule_vertex_color(text,key)
            found = [item for item in group["_fonts"] if item["_key"] == key]
            setting_obj, setting_file = self.resolve(group_obj, found[0]["_font"] if found else group["_default"])
            setting = setting_obj.read_typetree()
            text_type = text.get('original_text_type', 1)
            normal = [item for item in setting["_textTypeFontConditions"] if item["_type"] == text_type]
            # Native GetFontData checks the requested type, then type 1.
            if not normal and text_type != 1:
                text_type = 1
                normal = [item for item in setting["_textTypeFontConditions"] if item["_type"] == 1]
            if len(normal) != 1:
                raise LocalizationError("Original default text type is unavailable", text_id=text["id"])
            choices = normal[0]["_conditions"]
            language_choices = [c for c in choices if not c["_isDefault"] and c["_language"] == enum]
            chosen = language_choices or [c for c in choices if c["_isDefault"]]
            if len(chosen) != 1:
                raise LocalizationError("Original font language condition is ambiguous", text_id=text["id"])
            font_obj, font_file = self.resolve(setting_obj, chosen[0]["_font"]["_font"])
            material_obj, material_file = self.resolve(setting_obj, chosen[0]["_font"]["_material"])
            role = role_ids.get(font_obj.path_id)
            if role is None:
                from .japanese_fonts import load_font_set, FONT_BUNDLE
                if font_file.replace('\\','/') != FONT_BUNDLE:
                    raise UnsupportedLayout("Selected original font has no recovered glyph atlas", font_path_id=font_obj.path_id)
                role = 'source_font_' + str(font_obj.path_id).replace('-', 'n')
                font_out = Path(output_dir or self.root/'render-cache/localized-resources')/'original-fonts'/role
                fonts.update(load_font_set(self.root, self, font_out, {role: font_obj.path_id}))
                role_ids[font_obj.path_id] = role
            material = material_obj.read_typetree()
            properties = material["m_SavedProperties"]
            floats, colors = dict(properties["m_Floats"]), dict(properties["m_Colors"])
            text.update(font_id=role, font_file=fonts[role]["original_font_file"],
                        original_font_file=fonts[role]["original_font_file"],
                        color_rgba=[colors["_FaceColor"][v] for v in ("r", "g", "b", "a")])
            text["material_overrides"] = {"floats": floats, "colors": colors,
                "keywords": material["m_ValidKeywords"], "material_id": material_obj.path_id,
                "name": material["m_Name"], "source": self.source(material_obj, material_file)}
            shader_obj,shader_file=self.resolve(material_obj,material['m_Shader'])
            text['material_overrides']['shader_source']=self.source(shader_obj,shader_file)
            text["font_provenance"] = {"condition_source": self.source(owner, relative),
                "master_energy": master_energy, "asset3d_energy": energy,
                "selected_energy_condition": selected, "group": self.source(group_obj, group_file),
                "condition_selection_native_method": {"name":"FontGroupConditions.GetFontGroup",
                    "virtual_address":"0x480B0B0","policy":"First native match, condition flags and optional Pokemon energy"},
                "group_name": group["m_Name"], "key": key, "key_component_id": keys[0]["id"],
                "setting": self.source(setting_obj, setting_file), "setting_name": setting["m_Name"],
                "text_type": text_type, "language_enum": enum, "language_condition": chosen[0],
                "font": self.source(font_obj, font_file), "material": self.source(material_obj, material_file)}
            if '[Ctrl:Bold' in text['text']:
                bold=[item for item in setting['_textTypeFontConditions'] if item['_type']==2]
                if len(bold)!=1:raise UnsupportedLayout('Original bold font condition is unavailable',text_id=text['id'])
                bc=[c for c in bold[0]['_conditions'] if not c['_isDefault'] and c['_language']==enum]
                bc=bc or [c for c in bold[0]['_conditions'] if c['_isDefault']]
                if len(bc)!=1:raise UnsupportedLayout('Original bold language condition is ambiguous',text_id=text['id'])
                bf,bff=self.resolve(setting_obj,bc[0]['_font']['_font'])
                bm,bmf=self.resolve(setting_obj,bc[0]['_font']['_material'])
                br=role_ids.get(bf.path_id)
                if br is None:raise UnsupportedLayout('Original bold font atlas not recovered',font_path_id=bf.path_id)
                bd=bm.read_typetree();bp=bd['m_SavedProperties'];colors=dict(bp['m_Colors'])
                bold_shader,bold_shader_file=self.resolve(bm,bd['m_Shader'])
                text['rich_styles']={'bold':{'font_id':br,'font_file':fonts[br]['original_font_file'],
                    'original_font_file':fonts[br]['original_font_file'],
                    'color_rgba':[colors['_FaceColor'][v] for v in ('r','g','b','a')],
                    'material_overrides':{'floats':dict(bp['m_Floats']),'colors':colors,'keywords':bd['m_ValidKeywords'],
                        'material_id':bm.path_id,'name':bd['m_Name'],'source':self.source(bm,bmf),
                        'shader_source':self.source(bold_shader,bold_shader_file)},
                    'font_provenance':dict(text['font_provenance'],text_type=2,language_condition=bc[0],
                        font=self.source(bf,bff),material=self.source(bm,bmf))}}
            if '[Ctrl:Italic' in text['text']:
                italic={key:deepcopy(text[key]) for key in ('font_id','font_file','original_font_file',
                    'color_rgba','material_overrides','font_provenance')}
                italic['font_style_bits']=text.get('font_style_bits',0)|2
                italic['original_control_source']={'native_method':'MtUIMessageBase.GetStartCtrlTag / GetEndCtrlTag',
                    'virtual_addresses':['0x5BF9D00','0x5BF9F6C'],'tmp_tags':['<i>','</i>'],
                    'geometry':'Original selected font italicStyle applied to glyph vertices by TMP.'}
                text.setdefault('rich_styles',{})['italic']=italic
                if 'bold' in text['rich_styles']:
                    combined=deepcopy(text['rich_styles']['bold']);combined['font_style_bits']=italic['font_style_bits']
                    text['rich_styles']['bold+italic']=combined
            if '[Img:Element' in text['text']:
                from .inline_elements import bind_inline_elements
                bind_inline_elements(self.root,self,text,fonts,group,self.source(group_obj,group_file),
                    output_dir or self.root/'render-cache/inline-fonts')
            if '[Img:ex' in text['text']:
                from .inline_ex import bind_inline_ex
                bind_inline_ex(self.root,self,text,group,self.source(group_obj,group_file),
                    output_dir or self.root/'render-cache/inline-fonts')
            raw = text["original_tmp"]
            audit.append({"text_id": text["id"], "source_node": text["source_node"],
                "raw_font_pointer": raw["m_fontAsset"], "raw_material_pointer": raw["m_sharedMaterial"],
                "font_weight": raw["m_fontWeight"], "font_style": raw["m_fontStyle"],
                "selected_font_role": role, "original_font_file": text["original_font_file"],
                "font_size": text["font_size"], "character_spacing_tmp_units": text["character_spacing_tmp_units"],
                "line_spacing_tmp_units": text["line_spacing_tmp_units"],
                "font_size_source": text["font_size_source"], "font_provenance": text["font_provenance"],
                "selected_material": text["material_overrides"],
                **({'vertex_color_rgba':text['vertex_color_rgba'],
                    'vertex_color_provenance':text['vertex_color_provenance']}
                    if 'vertex_color_provenance' in text else {}),
                "fatten_components": [c for c in text["companion_components"] if "_fattenEnable" in c["data"]]})
        return audit

    def rect(self, go):
        rid = self.transforms[go]
        if rid in self.rect_cache:
            return self.rect_cache[rid]
        value = self.trees[rid]
        parent = value["m_Father"]["m_PathID"]
        if not parent:
            result = [0, 0, value["m_SizeDelta"]["x"], value["m_SizeDelta"]["y"]]
        else:
            p = self.rect(self.trees[parent]["m_GameObject"]["m_PathID"])
            size = [p[i+2] * (value["m_AnchorMax"][k] - value["m_AnchorMin"][k]) + value["m_SizeDelta"][k]
                    for i, k in enumerate(("x", "y"))]
            pivot = [p[i] + p[i+2] * (value["m_AnchorMin"][k] + (value["m_AnchorMax"][k] - value["m_AnchorMin"][k]) * value["m_Pivot"][k])
                     + value["m_AnchoredPosition"][k] for i, k in enumerate(("x", "y"))]
            result = [pivot[i] - size[i] * value["m_Pivot"][k] for i, k in enumerate(("x", "y"))] + size
        self.rect_cache[rid] = result
        return result

    def export_sprite(self, sid, owner, pointer, output_dir):
        obj, relative = self.resolve(owner, pointer)
        data = obj.read_typetree()
        tex_obj, tex_relative = self.resolve(obj, data["m_RD"]["texture"])
        texture = tex_obj.read()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / (sid + ".texture.png")
        image = texture.image
        assert image.size == (texture.m_Width, texture.m_Height)
        image.save(path)
        rect = data["m_RD"]["textureRect"]
        width, height = image.size
        return {"id": sid, "source": self.source(obj, relative), "rect": data["m_Rect"],
                "border": data["m_Border"], "pivot": data["m_Pivot"], "pixels_to_units": data["m_PixelsToUnits"],
                "texture_rect": rect, "texture_rect_offset": data["m_RD"]["textureRectOffset"],
                "outer_uv_rect_derived_from_texture_rect": [rect["x"]/width, rect["y"]/height,
                    (rect["x"]+rect["width"])/width, (rect["y"]+rect["height"])/height],
                "texture": {"source": self.source(tex_obj, tex_relative), "png": str(path.resolve()),
                    "width": width, "height": height, "format": int(texture.m_TextureFormat),
                    "png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "sampler_settings": tex_obj.read_typetree()["m_TextureSettings"], "color_space": texture.m_ColorSpace,
                    "conversion": "Native channel decode only; no resizing, sharpening, alpha edits or synthetic pixels."}}

    def resources(self, energy_names, language, output_dir):
        result = []
        controller = next((obj_id, data) for obj_id, data in self.trees.items() if "_grass" in data and "_colorless" in data)
        for name in sorted(set(energy_names)):
            result.append(self.export_sprite(name, self.objects[controller[0]], controller[1]["_"+name], output_dir))
        outline_go = controller[1]["_outline"]["m_PathID"]
        outline_obj = next(self.objects[c["component"]["m_PathID"]] for c in self.gos[outline_go]["m_Component"]
                           if "m_Sprite" in self.trees[c["component"]["m_PathID"]])
        result.append(self.export_sprite("energy_outline", outline_obj, outline_obj.read_typetree()["m_Sprite"], output_dir))
        enum = LANGUAGES[normalize_language(language)]["asset_enum"]
        candidates = []
        for go, value in self.gos.items():
            if not value["m_Name"].startswith("phase_txt_img_01_"):
                continue
            components = [c["component"]["m_PathID"] for c in value["m_Component"]]
            conditions = [condition for pid in components for condition in self.trees[pid].get("_conditions", [])]
            if any(c.get("_stage") == 1 and c.get("_language") == enum for c in conditions):
                image_id = next(pid for pid in components if "m_Sprite" in self.trees[pid])
                candidates.append((go, image_id, conditions))
        if len(candidates) != 1:
            raise LocalizationError("Original BASIC sprite locale condition is ambiguous", language=language)
        go, image_id, conditions = candidates[0]
        basic = self.export_sprite("basic", self.objects[image_id], self.trees[image_id]["m_Sprite"], output_dir)
        basic["language_condition"] = {"enum": enum, "conditions": conditions, "source_game_object": go,
                                        "source_name": self.gos[go]["m_Name"]}
        result.append(basic)
        r = self.rect(go)
        return result, [r[0], 1024-r[1]-r[3], r[2], r[3]]


@lru_cache(maxsize=2)
def _resources(root):
    return _OriginalUIResources(root)


@lru_cache(maxsize=64)
def _bundle_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def localize_card(root, card, language, output_dir=None, strict_glyphs=False):
    """Return a prepared source-backed UI specification, without GPU execution.

    Supports the recovered regular-window Pokémon families. Missing atlas
    characters are retained and reported so the original dynamic font module
    can populate them. `strict_glyphs=True` turns that report into a failure.
    """
    from .layout import bind_layout, validate_layout_profile
    root = Path(root).resolve()
    language = normalize_language(language)
    validate_layout_profile(card)
    template = root / "offline-render/bulbasaur-original/ui-layout.json"
    spec = deepcopy(_read_json(str(template)))
    fonts = deepcopy(_read_json(str(root / "original-render-pipeline/text/font-metrics.json")))
    for role, font in fonts.items():
        source_font = {"bold": "TPC_GillSansNovaBold.otf", "medium": "TPC_GillSansNovaMedium.otf",
                       "number": "TPC_FuturaLTProHeavy.otf"}[role]
        font["original_font_file"] = str(root / "offline-render/bulbasaur-original/fonts" / source_font)
        for atlas in font["atlases"]:
            atlas["png"] = str(root / "original-render-pipeline/text" / atlas["png"])
            atlas["raw"]["file"] = str(root / "original-render-pipeline/text" / atlas["raw"]["file"])
    locale = LocaleRepository(root, language)
    if language == "ja-JP":
        from .japanese_fonts import load_japanese_fonts
        font_dir = Path(output_dir)/"original-fonts" if output_dir else root/"cache/localized-resources"/locale.directory/"original-fonts"
        fonts.update(load_japanese_fonts(root, _resources(str(root)), font_dir))
    raw_card = card.get("card") or card.get("raw_records", {}).get("PokemonCard", {})
    pokemon = card["pokemon"]
    attack = card["attacks"][0]
    attack_keys = card.get("attack_name_keys") or [v["NameMSID"] for v in card.get("attack_names", [])]
    bindings = {"name": ("Master", card.get("name_key"), 262),
                "attack": ("Master", attack_keys[0] if attack_keys else None, 50),
                "dex_line": ("Master", card.get("dex_key"), 44),
                "flavor": ("Master", card.get("flavor_key", raw_card.get("FlavorTextMSID")), 60),
                "illustrator": ("Master", card.get("artist_key", (raw_card.get("IllustratorNameMSIDs") or [""])[0]), 162),
                "weakness": ("UI", "weakness", 102), "retreat": ("UI", "retreat", 102),
                "hp_label": ("UI", "hp", 102), "illustrator_prefix": ("UI", "illustrator", 102)}
    provenance = {"authority": "original-local-master", "source": "input/master-cache-decoded/MasterMemory.bytes",
                  "sha256": card["provenance"]["sha256"],
                  "card_id": card["card_id"], "pokemon_id": pokemon["PokemonID"], "attack_id": attack["PokemonAttackID"]}
    localized_rows = []
    for text in spec["texts"]:
        apply_language_parameters(text, language)
        if text["id"] in bindings:
            table, key, category = bindings[text["id"]]
            text["text"], text["content_provenance"] = locale.lookup(table, key, category, allow_empty=text["id"] in ("flavor", "illustrator", "dex_line"))
            localized_rows.append({"table": table, "key": key, "value": text["text"], "provenance": text["content_provenance"]})
        elif text["id"] == "hp_value":
            text["text"], text["content_provenance"] = str(pokemon["HP"]), dict(provenance, field="Pokemon.HP")
        elif text["id"] == "damage":
            text["text"] = "" if attack.get("IsNoDamage") else str(attack["Damage"])
            text["content_provenance"] = dict(provenance, field="PokemonAttack.Damage")
        elif text["id"] == "weak_sign":
            text["text"] = text["original_tmp"]["m_text"]
            text["content_provenance"] = {"authority": "original-UI-component", "field": "m_text", "source_node": text["source_node"]}
        elif text["id"] == "weak_value":
            text["text"] = "20"
            text["content_provenance"] = {"authority": "original-native-method", "method": "PokemonWeaknessValue.get_Operand",
                                          "virtual_address": "0x44ea7cc", "instruction": "MOV w0,#20; RET"}
        if text["id"] in ("weak_sign", "weak_value") and pokemon["WeaknessType"] == 0:
            # UpdateTextView tests Weakness.Type.HasValue for both fields.
            # The translated weakness label remains visible in either case.
            text["text"] = ""
            text["content_provenance"] = {
                "authority": "original-native-method", "method": "PokemonCardUIBehaviour.UpdateTextView",
                "virtual_address": "0x47f6fb0", "master_field": "Pokemon.WeaknessType",
                "master_value": 0, "condition": "Weakness.Type.HasValue == false",
                "branch_address": "0x47f75cc" if text["id"] == "weak_sign" else "0x47f7664"}
        role = "bold" if "Bold" in text["font_file"] else "medium" if "Medium" in text["font_file"] else "number"
        text["font_id"] = role
        text["font_file"] = fonts[role]["original_font_file"]
        text["original_font_file"] = fonts[role]["original_font_file"]
        text["notes"] = ["Original text component and selected language conditions; runtime layout subset is documented separately."]
    spec.update(card_id=card["asset_id"], card_selector=card["id"], language=language, locale=locale.directory,
                font_metrics=fonts, original_locale_bindings=localized_rows, original_card=card,
                status="Source-backed batch UI input; original full TMP runtime is not executed",
                language_provenance=LANGUAGE_SOURCE)
    from .evolution import add_evolution_text,add_evolution_resources
    add_evolution_text(spec,card,language,_resources(str(root)),locale)
    from .skills import add_skill_texts, add_ability_resources
    add_skill_texts(spec,card,locale,_resources(str(root)),fonts,output_dir)
    from .pokemon_rules import add_rule_texts,add_rule_resources
    add_rule_texts(spec,card,locale,_resources(str(root)),fonts)
    from .mega_source import add_mega_source_text, add_mega_source_resources
    add_mega_source_text(spec,card,locale,_resources(str(root)),fonts)
    from .ms_references import expand_additional_names
    for field in spec['texts']:
        if '[Text:AdditionalName' in field['text']:
            field['original_ms_text']=field['text']
            field['text']=expand_additional_names(field['text'],locale,field.setdefault('reference_audit',[]))
    condition_file=spec.get('pokemon_rules',{}).get('design',{}).get('font_conditions_file')
    spec["typography_source_audit"] = _resources(str(root)).typography(spec, card, language, fonts,condition_file=condition_file,output_dir=output_dir)
    bind_layout(spec, card, fonts)
    costs = [v for item in card['attacks'] for v in item['AttackCost']]
    used = [ENERGY_NAMES[v] for v in pokemon["PokemonTypes"] + costs]
    if pokemon["WeaknessType"] != 0:
        used.append(ENERGY_NAMES[pokemon["WeaknessType"]])
    if any(not item['AttackCost'] for item in card['attacks']):
        # CardEnergyView.SetNullEnergy passes Asset3D EnergyType.Empty=12;
        # resources() resolves the original CardEnergyIconView._empty Sprite.
        used.append("empty")
    if pokemon["RetreatAmount"]:
        used.append("colorless")
    resource_dir = Path(output_dir) / "ui-resources" if output_dir else root / "cache/localized-resources" / locale.directory
    sprite_assets, basic_rect = _resources(str(root)).resources(used, language, resource_dir)
    spec["sprite_assets"] = sprite_assets
    spec["sprites"] = [{"id": row["id"], "kind": "sprite", "file": row["texture"]["png"],
                         "original_pixels": [row["texture"]["width"], row["texture"]["height"]],
                         "render_rect_top_left": None, "provenance": row["source"]} for row in sprite_assets]
    spec["sprite_bindings"].insert(0, {"id": "basic_badge", "source_sprite": "basic", "render_rect_top_left": basic_rect,
                                       "confidence": "original-static-rect-and-locale-condition"})
    add_evolution_resources(spec,card,language,_resources(str(root)),resource_dir)
    add_rule_resources(spec,card,locale,_resources(str(root)),resource_dir)
    from .owner_names import add_category_resources
    add_category_resources(spec,card,locale,_resources(str(root)),resource_dir)
    add_mega_source_resources(spec,card,locale,_resources(str(root)),resource_dir)
    if spec.get('skills',{}).get('ability_id'):
        # The ability group starts at the source's UpperCenter edge; its badge
        # can be extracted before missing glyphs are populated and rebound.
        spec['skills'].setdefault('ability_badge_rect_top_left',[53,547,162,36])
        add_ability_resources(spec,card,locale,_resources(str(root)),resource_dir)
        spec['ability_sprite_bindings'] = spec['sprite_bindings'][-2:]
    spec["glyph_coverage"] = glyph_coverage(spec["texts"], fonts)
    spec["limitations"] = [
        "Large regular-window Pokémon use recovered evolution, one/two attack and ability layout groups; unsupported rich-text/runtime shrink cases are rejected.",
        "Glyph metrics, atlas resources, material colors, localization and language conditions are original; complete TMP runtime is not executed.",
        "Per-field layout confidence and source derivations are retained; unsupported layouts are rejected instead of borrowing Bulbasaur values.",
        "Missing atlas characters require population from the original source font; no substitution or accent removal is allowed.",
        "Original per-field fatten component values are retained for audit; their complete runtime effect is not recovered and is not guessed as FaceDilate.",
        "Complete TMP autosizing/rich text is not executed. Recovered space, Ctrl:Bold/Italic and non-nested Img:Element commands use original metrics/font/materials; unknown commands fail.",
    ]
    if strict_glyphs and not spec["glyph_coverage"]["complete"]:
        raise LocalizationError("Original atlas needs dynamic glyph population", glyph_coverage=spec["glyph_coverage"])
    return spec
