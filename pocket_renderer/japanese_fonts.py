"""Decode the original Japanese TMP fonts and native atlas pages.

The IDs below were followed from the explicit Japanese (Asset3D enum 0)
conditions in CardName/AttackName and CardDescription/LibraryData/Rule. They
are not replacement system fonts. Font outlines and populated atlas bytes are
copied/decoded without any text rasterization or texture resizing here.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


FONT_BUNDLE = "Common/Font_bundles"
FONT_IDS = {"japanese_bold": 7997069833075654224,
            "japanese_medium": 8163795205098350844}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_font_set(root, resources, output_dir, font_ids, manifest_name="fonts.json"):
    """Return compiler/font_engine-compatible native font resources.

    ``resources`` is the existing original UI resource/PPtr resolver. The
    cache is accepted only when its source bundle and every copied/decoded
    artifact still have the recorded SHA-256. Missing glyphs remain missing;
    the independent original-outline population step handles them explicitly.
    """
    root = Path(root).resolve()
    out = Path(output_dir)
    out = out.resolve()
    source_hash = _sha(resources.src / FONT_BUNDLE)
    manifest_path = out / manifest_name
    if manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf8"))
            if (cached["source_bundle_sha256"] == source_hash and
                    all(Path(entry["file"]).is_file() and _sha(entry["file"]) == entry["sha256"]
                        for entry in cached["artifacts"])):
                return deepcopy(cached["fonts"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    out.mkdir(parents=True, exist_ok=True)
    objects = {obj.path_id: obj for obj in resources.load(FONT_BUNDLE).objects}
    fonts, artifacts = {}, []

    def record(path):
        entry = {"file": str(path), "bytes": path.stat().st_size, "sha256": _sha(path)}
        artifacts.append(entry)
        return entry

    def blob(path, raw):
        path.write_bytes(raw)
        return record(path)

    for role, path_id in font_ids.items():
        obj = objects[path_id]
        data = obj.read_typetree()
        font_path=original_font=source_record=None
        # Static symbol fonts retain their complete atlas/metrics but do not
        # ship source outlines. They require no dynamic font generation.
        if data['m_SourceFontFile']['m_PathID']:
            source_obj, source_file = resources.resolve(obj, data["m_SourceFontFile"])
            source = source_obj.read_typetree()
            font_bytes = bytes(source["m_FontData"])
            if font_bytes[:4] not in (b"OTTO",b'\x00\x01\x00\x00',b'true'):
                raise ValueError("Expected original OpenType/TrueType outlines: " + data["m_Name"])
            font_path = out / (role + ('.otf' if font_bytes[:4]==b'OTTO' else '.ttf'))
            original_font = blob(font_path, font_bytes)
            source_record=resources.source(source_obj, source_file)
        material_obj, material_file = resources.resolve(obj, data["m_Material"])
        material = material_obj.read_typetree()
        atlases = []
        for index, pointer in enumerate(data["m_AtlasTextures"]):
            atlas_obj, atlas_file = resources.resolve(obj, pointer)
            atlas = atlas_obj.read()
            image = atlas.image
            if image.size != (atlas.m_Width, atlas.m_Height):
                raise ValueError("Native atlas dimensions changed during decoding")
            name = role + ".atlas%d" % index
            png_path = out / (name + ".png")
            image.save(png_path)
            record(png_path)
            raw = blob(out / (name + ".data.bin"), bytes(atlas.get_image_data()))
            atlases.append({"index": index, "id": atlas_obj.path_id, "name": atlas.m_Name,
                "width": atlas.m_Width, "height": atlas.m_Height,
                "format": int(atlas.m_TextureFormat), "mip_count": atlas.m_MipCount,
                "raw": raw, "png": str(png_path), "png_sha256": _sha(png_path),
                "decoded_mode": image.mode, "alpha_extrema": list(image.getchannel("A").getextrema()),
                "settings": atlas_obj.read_typetree()["m_TextureSettings"],
                "source": resources.source(atlas_obj, atlas_file),
                "conversion": "Native stored atlas channel decode only; no resizing, sharpening or glyph generation."})
        fonts[role] = {
            "source_bundle": FONT_BUNDLE, "source_bundle_sha256": source_hash,
            "cab": obj.assets_file.name.lower(), "font_path_id": path_id, "name": data["m_Name"],
            "face_info": data["m_FaceInfo"], "glyphs": data["m_GlyphTable"],
            "characters": data["m_CharacterTable"],
            "unicode_lookup": {str(c["m_Unicode"]): {"glyph_index": c["m_GlyphIndex"], "scale": c["m_Scale"]}
                               for c in data["m_CharacterTable"]},
            "atlas_padding": data["m_AtlasPadding"], "atlas_width": data["m_AtlasWidth"],
            "atlas_height": data["m_AtlasHeight"], "atlas_render_mode": data["m_AtlasRenderMode"],
            "atlas_population_mode": data["m_AtlasPopulationMode"], "atlases": atlases,
            "font_style_values": {k: data[k] for k in ("normalStyle", "normalSpacingOffset", "boldStyle", "boldSpacing", "italicStyle", "tabSize")},
            "font_feature_table": data["m_FontFeatureTable"],
            "material_id": material_obj.path_id,
            "material_floats": dict(material["m_SavedProperties"]["m_Floats"]),
            "material_ints": dict(material["m_SavedProperties"]["m_Ints"]),
            "material_colors": dict(material["m_SavedProperties"]["m_Colors"]),
            "material_keywords": material.get("m_ValidKeywords", []),
            "material_source": resources.source(material_obj, material_file),
            "original_font_file": str(font_path) if font_path else None, "original_font_sha256": original_font["sha256"] if original_font else None,
            "original_font_source": source_record,
            "limitations": ["Stored TMP font metrics and atlas pages are original. Complete Unity TMP layout/ruby behavior is not executed by this font decoder."],
        }
    manifest_path.write_text(json.dumps({"source_bundle": FONT_BUNDLE,
        "source_bundle_sha256": source_hash, "fonts": fonts, "artifacts": artifacts,
        "new_card_rendered": False, "source_textures_resized": False}, ensure_ascii=False, indent=2), encoding="utf8")
    return fonts


def load_japanese_fonts(root, resources, output_dir=None):
    return load_font_set(root, resources,
        output_dir or Path(root)/"render-cache/original-fonts/ja-JP",
        FONT_IDS, manifest_name="japanese-fonts.json")
