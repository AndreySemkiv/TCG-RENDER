"""Prepare original shader passes for one supported card, without running a GPU.

Only buffer/manifests are written. Stored textures/atlases are referenced at
their original dimensions; no card bitmap is resized or generated here.
"""
from pathlib import Path
import copy
import json
import math
import re
import struct
from .text_tokens import characters, lines as text_lines, space_advance
from .config import CODE_ROOT


class CompilationError(ValueError):
    """The original resources/layout required by this card are unsupported."""


IDENTITY = [[1 if r == c else 0 for c in range(4)] for r in range(4)]
BLEND = {"enabled": True, "src_color": "one", "dst_color": "one_minus_src_alpha",
         "src_alpha": "zero", "dst_alpha": "one_minus_src_alpha"}
LIMITS = [
    "Original shader SPIR-V, native textures and SDF atlases are used without rewriting shader math or resizing source raster files.",
    "TMP geometry/layout preparation is a supported subset, not execution of the complete original Canvas/TMP runtime. Derived layout confidence remains in layout-audit.json.",
    "The High dynamic UI allocation is1468x2048 for the logical734x1024 canvas. Source sprite textures are normally sampled by their original shader; the final card target is700x976.",
    "The frontal projection is an explicit offscreen proof choice. Complete original camera overrides, postprocessing, mip-chain filtering and color management are not reproduced.",
    "Final readback uses original inverse destination alpha. The caller must export conventional PNG alpha explicitly, without a color-key background mask.",
]


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf8")


def _safe(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value))


def _columns(matrix):
    return [float(matrix[r][c]) for c in range(4) for r in range(4)]


def _inverse(matrix):
    work = [[float(v) for v in matrix[r]] + IDENTITY[r][:] for r in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < 1e-12:
            raise CompilationError("Singular original world transform")
        work[pivot], work[col] = work[col], work[pivot]
        scale = work[col][col]
        work[col] = [v / scale for v in work[col]]
        for row in range(4):
            if row == col:
                continue
            scale = work[row][col]
            work[row] = [work[row][i] - scale * work[col][i] for i in range(8)]
    return [row[4:] for row in work]


def _shader_offsets(path, block_name):
    raw = Path(path).read_bytes()
    if len(raw) % 4:
        raise CompilationError("Unaligned original SPIR-V: " + str(path))
    words = struct.unpack("<%dI" % (len(raw) // 4), raw)
    if words[0] != 0x07230203:
        raise CompilationError("Invalid SPIR-V magic: " + str(path))
    names, members, offsets = {}, {}, {}
    cursor = 5
    while cursor < len(words):
        count, opcode = words[cursor] >> 16, words[cursor] & 65535
        if not count or cursor + count > len(words):
            raise CompilationError("Invalid SPIR-V instruction framing")
        args = words[cursor + 1:cursor + count]
        if opcode in (5, 6):
            skip = 1 if opcode == 5 else 2
            text = struct.pack("<%dI" % (len(args) - skip), *args[skip:]).split(b"\0")[0].decode()
            if opcode == 5:
                names[args[0]] = text
            else:
                members[(args[0], args[1])] = text
        if opcode == 72 and args[2] == 35:
            offsets[(args[0], args[1])] = args[3]
        cursor += count
    result = {members[k]: v for k, v in offsets.items() if names.get(k[0]) == block_name}
    if not result:
        raise CompilationError("Original shader uniform block not found: " + block_name)
    return result


def _absolute(path, base):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (Path(base) / path).resolve()


def _stage_pair(root, records, shader_id=None, suffix=None):
    selected = [r for r in records if (shader_id and shader_id in r.get("spirv_file", ""))
                or (suffix and r.get("shader_bundle", "").replace("\\", "/").endswith(suffix))]
    pair = {}
    for record in selected:
        stage = record["entry_points"][0]["execution_model"]
        path = _absolute(record["spirv_file"], root)
        if stage in pair and pair[stage] != path:
            raise CompilationError("Ambiguous original shader variant: " + str(shader_id or suffix))
        pair[stage] = path
    if set(pair) != {0, 4} or not all(p.is_file() for p in pair.values()):
        raise CompilationError("Missing original vertex/fragment stages: " + str(shader_id or suffix))
    return {"vertex": str(pair[0]), "fragment": str(pair[4])}


def _mesh_buffers(out, prefix, mesh, slot=0, with_normals=False):
    values = []
    for i, position in enumerate(mesh["vertices"]):
        values.extend(position[:3] + [1] + mesh["uv0"][i][:2])
        if with_normals:
            if not mesh.get("normals"):
                raise CompilationError("Original rarity mesh normals are missing")
            values.extend(mesh["normals"][i][:3])
    vertices = out / (prefix + ".vertices.bin")
    indices = out / (prefix + ".indices.bin")
    vertices.write_bytes(struct.pack("<%df" % len(values), *values))
    triangles = [i for tri in mesh["submeshes"][slot]["triangles"] for i in tri]
    indices.write_bytes(struct.pack("<%dI" % len(triangles), *triangles))
    attrs = [{"location": 0, "format": "float4", "offset": 0},
             {"location": 1, "format": "float2", "offset": 16}]
    if with_normals:
        attrs.append({"location": 2, "format": "float3", "offset": 24})
    return {"file": str(vertices), "stride": 36 if with_normals else 24, "attributes": attrs}, {"file": str(indices)}


def _projection(scene, meshes):
    front = next((o for o in scene["objects"] if o["name"] == "Card_F" and o.get("renderer")), None)
    if front is None:
        raise CompilationError("Supported regular-card front mesh Card_F is missing")
    mesh = meshes[front["renderer"]["mesh"]]
    xmin = min(p[0] for p in mesh["vertices"])
    xmax = max(p[0] for p in mesh["vertices"])
    ymin = min(p[1] for p in mesh["vertices"])
    ymax = max(p[1] for p in mesh["vertices"])
    if not (xmax > xmin and ymax > ymin):
        raise CompilationError("Empty original card bounds")
    vp = [[2/(xmax-xmin), 0, 0, -(xmax+xmin)/(xmax-xmin)],
          [0, 2/(ymax-ymin), 0, -(ymax+ymin)/(ymax-ymin)],
          [0, 0, 1, .5], [0, 0, 0, 1]]
    return vp, [xmin, xmax, ymin, ymax]


def _font_resources(root, ui):
    source = ui.get("font_metrics", root / "original-render-pipeline/text/font-metrics.json")
    if isinstance(source, (str, Path)):
        source_path = _absolute(source, root)
        return _read(source_path), source_path.parent
    return source, Path(ui.get("font_assets_dir", root / "original-render-pipeline/text"))


def _font_role(text):
    if text.get("font_id"):
        return text["font_id"]
    file = text.get("font_file", "")
    return "bold" if "Bold" in file else "medium" if "Medium" in file else "number"


def _prepare_ui(root, ui, out):
    fonts, font_base = _font_resources(root, ui)
    from .rich_text import expand_font_runs
    expanded=[field for text in ui['texts'] for field in expand_font_runs(text,fonts)]
    inline_fields=[f for f in expanded if f.get('kind')=='inline-sprite']
    ui=dict(ui,texts=[f for f in expanded if f.get('kind')!='inline-sprite'])
    # Fail before writing partial buffers when any required character is absent.
    for text in ui["texts"]:
        role = _font_role(text)
        if role not in fonts:
            raise CompilationError("Missing original font role: " + role)
        keywords = text.get("material_overrides", {}).get("keywords", [])
        if keywords and set(keywords)!={'OUTLINE_ON'}:
            raise CompilationError("Unsupported original text material shader keywords for %s: %s" %
                                   (text["id"], ", ".join(keywords)))
        missing = sorted(set(characters(text)) - set(chr(int(c)) for c in fonts[role]["unicode_lookup"]) - {"\n"})
        if missing:
            raise CompilationError("Missing original stored glyphs in %s (%s): %s" %
                                   (text["id"], role, ", ".join("U+%04X %s" % (ord(c), repr(c)) for c in missing)))

    text_root = root / "original-render-pipeline/text"
    sprite_root = root / "original-render-pipeline/ui-sprites"
    vs = text_root / "decoded-shaders/tmp-sdf.platform18.block0.program12.smolv1.spv"
    fs = text_root / "decoded-shaders/tmp-sdf.platform18.block0.program12.smolv0.spv"
    offsets = _shader_offsets(vs, "VGlobals")
    projection = [2/734, 0, 0, 0, 0, 2/1024, 0, 0, 0, 0, 1, 0, 0, 0, .5, 1]
    identity = _columns(IDENTITY)
    ui_size = list(ui.get("render_size", [1468, 2048]))
    if ui_size not in ([1468, 2048], [734, 1024]):
        raise CompilationError("Unsupported original dynamic UI allocation")
    draws, audit = [], []
    for text in ui["texts"]:
        if not text["text"]:
            continue
        role = _font_role(text)
        font = fonts[role]
        # TMP vertex colour is independent of the selected font material.
        # Only recovered bindings supply this field; other fields retain the
        # existing proof colour until their runtime initialization is known.
        vertex_color = text.get('vertex_color_rgba', [1, 1, 1, 1])
        if (not isinstance(vertex_color, (list, tuple)) or len(vertex_color) != 4
                or any(not isinstance(c, (int, float)) or not math.isfinite(c)
                       or not 0 <= c <= 1 for c in vertex_color)):
            raise CompilationError('Invalid original TMP vertex colour for '+text['id'])
        # Exact original keyword variant; the shipped OUTLINE_ON program uses
        # the same VGlobals layout and vertex channels as the accepted proof.
        if text.get('material_overrides',{}).get('keywords')==['OUTLINE_ON']:
            text_vs=text_root/'decoded-shaders/tmp-sdf.platform18.block0.program13.smolv1.spv'
            text_fs=text_root/'decoded-shaders/tmp-sdf.platform18.block0.program13.smolv0.spv'
            if _shader_offsets(text_vs,'VGlobals')!=offsets:
                raise CompilationError('Original outline font uniform layout differs from recovered UI layout')
        else:text_vs,text_fs=vs,fs
        raw = bytearray(448)
        def pack(field, value):
            value = value if isinstance(value, (tuple, list)) else [value]
            struct.pack_into("<%df" % len(value), raw, offsets[field], *value)
        pack("_WorldSpaceCameraPos", [0, 0, -10])
        pack("_ScreenParams", [ui_size[0], ui_size[1], 1+1/ui_size[0], 1+1/ui_size[1]])
        for field in ("hlslcc_mtx4x4unity_ObjectToWorld", "hlslcc_mtx4x4unity_WorldToObject"):
            pack(field, identity)
        for field in ("hlslcc_mtx4x4glstate_matrix_projection", "hlslcc_mtx4x4unity_MatrixVP"):
            pack(field, projection)
        material = text.get("material_overrides", {})
        material_floats = dict(font["material_floats"])
        material_floats.update(text.get("material_floats", {}))
        material_floats.update(material.get("floats", {}))
        material_colors = dict(font["material_colors"])
        material_colors.update(material.get("colors", {}))
        for field, value in material_floats.items():
            if field in offsets:
                pack(field, value)
        for field, value in material_colors.items():
            if field in offsets:
                pack(field, [value[c] for c in ("r", "g", "b", "a")])
        if text.get("color_rgba"):
            pack("_FaceColor", text["color_rgba"])
        pack("_FaceTex_ST", [1, 1, 0, 0])
        prefix = "text-" + _safe(text["id"])
        globals_file = out / (prefix + ".globals.bin")
        globals_file.write_bytes(raw)
        face = font["face_info"]
        glyphs = {g["m_Index"]: g for g in font["glyphs"]}
        advanced = text.get('layout_engine') == 'tmp-subset-v2'
        measured = None
        if advanced:
            from .text_layout import measure_text
            measured = measure_text(text,font)
        effective_size = measured['font_size'] if measured else text['font_size']
        scale = effective_size / face["m_PointSize"] * face["m_Scale"]
        horizontal_scale=text.get('horizontal_scale',1)
        character_width_scale=text.get('character_width_scale',1)
        padding = font["atlas_padding"]
        spacing = text.get("character_spacing_tmp_units", 0) * effective_size / 100 * character_width_scale
        lines = measured['lines'] if measured else text_lines(text)
        widths = []
        for line in lines:
            width = 0
            count = 0
            for token in line:
                if token[0] != "char":
                    width += space_advance(token, effective_size)
                    continue
                lookup = font["unicode_lookup"][str(ord(token[1]))]
                glyph = glyphs[lookup["glyph_index"]]
                width += glyph["m_Metrics"]["m_HorizontalAdvance"] * scale * lookup["scale"] * glyph["m_Scale"] * horizontal_scale * character_width_scale
                count += 1
            widths.append(width + max(0, count-1) * spacing)
        x, y, w, h = text["render_rect_top_left"]
        line_step = face["m_LineHeight"] * scale + text.get("line_spacing_tmp_units", 0) * effective_size / 100
        baseline = y + h/2 + (face["m_AscentLine"]+face["m_DescentLine"])*scale/2 - (len(lines)-1)*line_step/2 - text.get("language_position_y_offset", 0)
        if advanced:
            vertical=text.get('vertical_alignment','middle')
            if vertical == 'top': baseline=y+measured['ascent']
            elif vertical == 'bottom': baseline=y+h+measured['descent']-(len(lines)-1)*line_step
            baseline-=text.get('language_position_y_offset',0)
        baseline-=text.get('glyph_vertical_offset',0)
        shear = font["font_style_values"]["italicStyle"]/100 if text.get("font_style_bits", 0) & 2 else 0
        groups = {}
        for line_index, line in enumerate(lines):
            align = text["horizontal_alignment"]
            pen = x + (w-widths[line_index] if align == "right" else (w-widths[line_index])/2 if align == "center" else 0)
            justify = 0
            if measured and align == 'justified' and not measured['paragraph_ends'][line_index]:
                spaces=sum(token[0]=='char' and token[1]==' ' for token in line)
                if spaces: justify=max(0,w-widths[line_index])/spaces
            base_down = baseline + line_index * line_step
            for token in line:
                if token[0] != "char":
                    pen += space_advance(token, effective_size)
                    continue
                char = token[1]
                lookup = font["unicode_lookup"][str(ord(char))]
                glyph = glyphs[lookup["glyph_index"]]
                metrics, rect = glyph["m_Metrics"], glyph["m_GlyphRect"]
                gs = scale * lookup["scale"] * glyph["m_Scale"]
                atlas_index = glyph.get("m_AtlasIndex", 0)
                if rect["m_Width"] and rect["m_Height"]:
                    atlas = next((a for a in font["atlases"] if a["index"] == atlas_index), None)
                    if atlas is None:
                        raise CompilationError("Missing original atlas %s for %s" % (atlas_index, char))
                    atlas_path = _absolute(atlas["png"], font.get("assets_dir", font_base))
                    if not atlas_path.is_file():
                        raise CompilationError("Original atlas not found: " + str(atlas_path))
                    vertices, indices, glyph_audit = groups.setdefault(atlas_index, ([], [], []))
                    left = pen + (metrics["m_HorizontalBearingX"]*horizontal_scale-padding)*gs*character_width_scale - 367
                    bottom = 512-base_down + (metrics["m_HorizontalBearingY"]-metrics["m_Height"]-padding)*gs
                    right, top = left+(metrics["m_Width"]*horizontal_scale+padding*2)*gs*character_width_scale, bottom+(metrics["m_Height"]+padding*2)*gs
                    u0, v0 = (rect["m_X"]-padding)/atlas["width"], (rect["m_Y"]-padding)/atlas["height"]
                    u1, v1 = (rect["m_X"]+rect["m_Width"]+padding)/atlas["width"], (rect["m_Y"]+rect["m_Height"]+padding)/atlas["height"]
                    base = len(vertices)
                    sdf_scale = -gs if text.get("font_style_bits", 0) & 1 else gs
                    for px, py, u, v in ((left, bottom, u0, v0), (right, bottom, u1, v0), (right, top, u1, v1), (left, top, u0, v1)):
                        vertices.append([px+shear*(py-(512-base_down)), py, 0, 1, 0, 0, -1, *vertex_color, u, v, 0, sdf_scale, 0, 0])
                    indices.extend([base, base+1, base+2, base, base+2, base+3])
                    glyph_audit.append({"character": char, "glyph_index": glyph["m_Index"], "atlas": atlas_index, "source_rect": rect, "scale": gs})
                pen += metrics["m_HorizontalAdvance"]*gs*horizontal_scale*character_width_scale + spacing + (justify if char==' ' else 0)
        for atlas_index, (vertices, indices, glyph_audit) in groups.items():
            atlas = next(a for a in font["atlases"] if a["index"] == atlas_index)
            atlas_path = _absolute(atlas["png"], font.get("assets_dir", font_base))
            filename = prefix + ".atlas%d" % atlas_index
            vfile, ifile = out/(filename+".vertices.bin"), out/(filename+".indices.bin")
            vfile.write_bytes(b"".join(struct.pack("<17f", *v) for v in vertices))
            ifile.write_bytes(struct.pack("<%dI" % len(indices), *indices))
            text_bindings=[{"set":0,"binding":0,"type":"combined_image_sampler","file":str(atlas_path),"stages":["fragment"],"sampler":"linear"},
                {"set":1,"binding":1 if text_vs!=vs else 0,"type":"uniform_buffer","file":str(globals_file),"stages":["vertex"]}]
            if text_vs!=vs:
                fragment_offsets=_shader_offsets(text_fs,'PGlobals')
                fragment_raw=bytearray(48)
                struct.pack_into('<f',fragment_raw,fragment_offsets['_OutlineWidth'],material_floats['_OutlineWidth'])
                struct.pack_into('<4f',fragment_raw,fragment_offsets['_MainTex_TexelSize'],1/atlas['width'],1/atlas['height'],atlas['width'],atlas['height'])
                struct.pack_into('<i',fragment_raw,fragment_offsets['_FattenEnable'],int(material_floats.get('_FattenEnable',0)))
                struct.pack_into('<f',fragment_raw,fragment_offsets['_FattenFactor'],material_floats.get('_FattenFactor',0))
                fragment_file=out/(filename+'.fragment-globals.bin');fragment_file.write_bytes(fragment_raw)
                text_bindings.append({'set':1,'binding':0,'type':'uniform_buffer','file':str(fragment_file),'stages':['fragment']})
            selected_shader=None
            from .full_tmp import FULL_SHADER,prepare_bindings
            shader_file=material.get('shader_source',{}).get('file')
            if shader_file and shader_file not in (FULL_SHADER,'Common/Shader/Common/CardNew/UI/Card_TextMeshPro_Mobile_DistanceField_ToRT.shader_bundles'):
                raise CompilationError('Unsupported original TMP shader: '+shader_file)
            if shader_file==FULL_SHADER:
                full_stages,text_bindings,selected_shader=prepare_bindings(root,material,out,filename,atlas_path,ui_size,projection,identity)
            else:full_stages={'vertex':str(text_vs),'fragment':str(text_fs)}
            draws.append({"name": filename, "queue": 3100,
                "vertices": {"file": str(vfile), "stride": 68, "attributes": [
                    {"location": 0, "format": "float4", "offset": 0}, {"location": 1, "format": "float3", "offset": 16},
                    {"location": 2, "format": "float4", "offset": 28}, {"location": 3, "format": "float4", "offset": 44},
                    {"location": 4, "format": "float2", "offset": 60}]}, "indices": {"file": str(ifile)},
                "shaders": full_stages,
                "bindings":text_bindings,
                "state": {"cull": "none", "depth_test": False, "depth_write": False, "blend": BLEND},
                "provenance": {"font_source": font.get("source_bundle"), "font_atlas":atlas, "font_limits":font.get("limitations", []), "text": text.get("content_provenance"),
                               "rich_run":text.get('rich_run_source'),"original_full_tmp_shader":selected_shader,
                               "vertex_color_rgba":list(vertex_color),
                               "vertex_color_source":text.get('vertex_color_provenance'),
                               "font_binding": text.get("font_provenance"), "selected_material": material,
                               "layout_source_node": text.get("source_node"), "layout_confidence": text.get("layout_confidence"), "glyphs": glyph_audit}})
        audit.append({"id": text["id"], "text": text["text"], "font": role, "font_size": text["font_size"],
                      "effective_font_size":effective_size,"text_layout_engine":text.get('layout_engine','proof-v1'),
                      "rect": text["render_rect_top_left"], "baseline_down": baseline, "line_step": line_step,
                      "line_advance_widths": widths, "italic_shear": shear,
                      "font_style_bits": text.get("font_style_bits", 0),
                      "material": material, "effective_material_floats": material_floats,
                      "effective_material_colors": material_colors,
                      "vertex_color_rgba":list(vertex_color),
                      "layout_confidence": text.get("layout_confidence")})

    sprite_data = ui.get("sprite_assets")
    if sprite_data is None:
        sprite_data = _read(sprite_root/"manifest.json")["sprites"]
    sprite_by_id = {s["id"]: s for s in sprite_data}
    sprite_vs = sprite_root/"decoded-shaders/ui-default.platform18.block0.program4.smolv1.spv"
    sprite_fs = sprite_root/"decoded-shaders/ui-default.platform18.block0.program4.smolv0.spv"
    vglobals, fglobals = out/"sprite.vertex-globals.bin", out/"sprite.fragment-globals.bin"
    vglobals.write_bytes(struct.pack("<40f", *(identity+projection+[1,1,1,1]+[1,1,0,0])))
    fglobals.write_bytes(bytes(16))
    bindings = list(ui.get("sprite_bindings", []))
    existing = {b["source_sprite"] for b in bindings}
    for sprite in ui.get("sprites", []):
        if sprite.get("render_rect_top_left") and sprite["id"] not in existing:
            bindings.append({"id": sprite["id"], "source_sprite": sprite["id"], "render_rect_top_left": sprite["render_rect_top_left"],
                             "confidence": "original-static-child-rect", "provenance": sprite.get("provenance")})
    for binding in bindings:
        sid = binding["source_sprite"]
        if sid not in sprite_by_id:
            raise CompilationError("Missing original sprite: " + sid)
        sprite = sprite_by_id[sid]
        rect, texture_rect, offset = sprite["rect"], sprite["texture_rect"], sprite["texture_rect_offset"]
        x, y, w, h = binding["render_rect_top_left"]
        gx, gy = x+offset["x"]/rect["width"]*w, y+(rect["height"]-offset["y"]-texture_rect["height"])/rect["height"]*h
        gw, gh = texture_rect["width"]/rect["width"]*w, texture_rect["height"]/rect["height"]*h
        u0, v0, u1, v1 = sprite["outer_uv_rect_derived_from_texture_rect"]
        color=list(binding.get('vertex_color_rgba',[1,1,1,1]))
        if len(color)!=4:
            raise CompilationError('Original Image vertex color must contain four components: '+binding['id'])
        vertices = [[px,py,0,1,*color,u,v] for px,py,u,v in
                    ((gx-367,512-gy-gh,u0,v0),(gx+gw-367,512-gy-gh,u1,v0),(gx+gw-367,512-gy,u1,v1),(gx-367,512-gy,u0,v1))]
        prefix = "sprite-" + _safe(binding["id"])
        vfile, ifile = out/(prefix+".vertices.bin"), out/(prefix+".indices.bin")
        vfile.write_bytes(b"".join(struct.pack("<10f", *v) for v in vertices))
        ifile.write_bytes(struct.pack("<6I", 0,1,2,0,2,3))
        texture = _absolute(sprite["texture"]["png"], sprite.get("assets_dir", sprite_root))
        if not texture.is_file():
            raise CompilationError("Original sprite texture not found: " + str(texture))
        draws.append({"name": prefix, "queue": 3000,
            "vertices": {"file": str(vfile), "stride": 40, "attributes": [{"location":0,"format":"float4","offset":0}, {"location":1,"format":"float4","offset":16}, {"location":2,"format":"float2","offset":32}]},
            "indices": {"file": str(ifile)}, "shaders": {"vertex":str(sprite_vs),"fragment":str(sprite_fs)},
            "bindings": [{"set":0,"binding":0,"type":"combined_image_sampler","file":str(texture),"stages":["fragment"],"sampler":"linear","address_mode":"clamp-to-edge"},
                         {"set":1,"binding":0,"type":"uniform_buffer","file":str(fglobals),"stages":["fragment"]},
                         {"set":1,"binding":1,"type":"uniform_buffer","file":str(vglobals),"stages":["vertex"]}],
            "state": {"cull":"none","depth_test":False,"depth_write":False,"blend":dict(BLEND, src_color="src_alpha")},
            "provenance": {"sprite_source":sprite.get("source"), "layout_binding":binding, "sprite_rect":rect, "texture_rect":texture_rect, "texture_rect_offset":offset}})
        audit.append({"id":prefix,"sprite_rect_top_left":binding["render_rect_top_left"],"trimmed_geometry_top_left":[gx,gy,gw,gh],"confidence":binding.get("confidence")})
    if inline_fields:
        from .inline_ex import prepare_inline_draws
        extra,info=prepare_inline_draws(root,inline_fields,out,ui_size)
        draws.extend(extra);audit.extend(info)
    return draws, audit, ui_size


def _prepare_base(root, scene_dir, scene, meshes, materials, decoded, out, vp):
    textures = {t["id"]:t for t in scene["textures"]}
    shader_names = {s["id"]:s.get("name", "") for s in scene["shaders"]}
    allowed = {"Lettuce/Common/CardNew/Face/Card_Illust":"illust", "Lettuce/Common/CardNew/Face/Frame":"frame"}
    draws = []
    for obj in scene["objects"]:
        renderer = obj.get("renderer")
        if not renderer or not obj.get("active_in_hierarchy", True) or not renderer.get("enabled", True):
            continue
        material = materials[renderer["material_slots"][0]]
        kind = allowed.get(shader_names.get(material["shader_id"]))
        if not kind:
            continue
        mesh = meshes[renderer["mesh"]]
        prefix = "base-" + _safe(obj["id"])
        values = []
        for i, position in enumerate(mesh["vertices"]):
            uv0 = mesh["uv0"][i][:2]
            uv1 = mesh["uv1"][i][:2] if mesh.get("uv1") else uv0
            values.extend(position[:3]+[1]+uv0+uv1)
        vfile, ifile = out/(prefix+".vertices.bin"), out/(prefix+".indices.bin")
        vfile.write_bytes(struct.pack("<%df" % len(values), *values))
        indices = [i for tri in mesh["submeshes"][0]["triangles"] for i in tri]
        ifile.write_bytes(struct.pack("<%dI" % len(indices), *indices))
        vglobals, fglobals = out/(prefix+".vertex-globals.bin"), out/(prefix+".fragment-globals.bin")
        data = struct.pack("<32f", *(_columns(obj["world_matrix"])+_columns(vp)))
        if kind == "illust":
            data += struct.pack("<i", int(material["floats"].get("_UseUv", 0)))+bytes(12)
        vglobals.write_bytes(data)
        source_texture = next((v for v in material["textures"] if v["property"] == ("_MainTex" if kind == "illust" else "_BaseTex")), None)
        if source_texture is None or source_texture["texture_id"] not in textures:
            raise CompilationError("Original base texture is missing: " + material["name"])
        tex = textures[source_texture["texture_id"]]
        texture_path = _absolute(tex["png"], scene_dir)
        stages = _stage_pair(root, decoded, shader_id=material["shader_id"])
        bind = [{"set":0,"binding":0,"type":"combined_image_sampler","file":str(texture_path),"stages":["fragment"],"sampler":"nearest",
                 "address_mode":"clamp-to-edge" if tex.get("unity_settings", {}).get("wrap_u") == 1 else "repeat"}]
        bind.append({"set":1,"binding":0 if kind == "illust" else 1,"type":"uniform_buffer","file":str(vglobals),"stages":["vertex"]})
        if kind == "frame":
            fglobals.write_bytes(struct.pack("<f", material["floats"].get("_EmitMasking", 0))+bytes(12))
            bind.append({"set":1,"binding":0,"type":"uniform_buffer","file":str(fglobals),"stages":["fragment"]})
        attrs = [{"location":0,"format":"float4","offset":0},{"location":1,"format":"float2","offset":16}]
        if kind == "illust":
            attrs.append({"location":2,"format":"float2","offset":24})
        draws.append({"name":obj["name"],"queue":material["custom_render_queue"],"vertices":{"file":str(vfile),"stride":32,"attributes":attrs},"indices":{"file":str(ifile)},
                      "shaders":stages,"bindings":bind,"state":{"cull":"back","front_face":"clockwise","depth_test":bool(material["floats"].get("_ZTest",0)),
                      "depth_write":bool(material["floats"].get("_ZWrite",0)),"depth_compare":"less_or_equal","blend":dict(BLEND,src_color="src_alpha" if kind == "illust" else "one")},
                      "provenance":{"object":obj,"material_id":material["id"],"texture":tex,"submesh_slot":0,"sampler_limit":"Base native mip0 nearest sampling retained from validated proof."}})
    if len(draws) != 4:
        raise CompilationError("Supported regular-window scene requires exactly4 original base draws; found%d" % len(draws))
    return draws


def _prepare_consumer(root, scene_dir, scene, meshes, materials, decoded, out, vp, raw_ui):
    obj = next((o for o in scene["objects"] if o["name"] == "L_UI_Dynamic_Text" and o.get("renderer")), None)
    if obj is None:
        raise CompilationError("Original DynamicUITex mesh is missing")
    renderer = obj["renderer"]
    mesh, material = meshes[renderer["mesh"]], materials[renderer["material_slots"][0]]
    vertices, indices = _mesh_buffers(out, "dynamic", mesh)
    vglobals, fglobals = out/"dynamic.vertex-globals.bin", out/"dynamic.fragment-globals.bin"
    vglobals.write_bytes(struct.pack("<32f", *(_columns(obj["world_matrix"])+_columns(vp))))
    fglobals.write_bytes(struct.pack("<4f", material["floats"]["_AlphaThreshold"], material["floats"]["_EmitMasking"], 0, 0))
    return {"name":"Original DynamicUITex consumer", "queue":2900,"vertices":vertices,"indices":indices,
            "shaders":_stage_pair(root, decoded, shader_id=material["shader_id"]),
            "bindings":[{"set":0,"binding":0,"type":"combined_image_sampler","file":str(raw_ui),"stages":["fragment"],"sampler":"linear","address_mode":"clamp-to-edge"},
                        {"set":1,"binding":0,"type":"uniform_buffer","file":str(fglobals),"stages":["fragment"]},
                        {"set":1,"binding":1,"type":"uniform_buffer","file":str(vglobals),"stages":["vertex"]}],
            "state":{"cull":"back","front_face":"clockwise","depth_test":False,"depth_write":False,"blend":BLEND},
            "provenance":{"source_object":obj,"material_id":material["id"],"generated_ui_layer":str(raw_ui)}}


def _prepare_rarity_object(root, scene_dir, scene, meshes, materials, decoded, out, vp, obj):
    mesh = meshes[obj["renderer"]["mesh"]]
    if len(obj["renderer"]["material_slots"]) != 2:
        raise CompilationError("Unsupported original rarity material count")
    textures = {t["id"]:t for t in scene["textures"]}
    cubes = scene.get("cubemaps", [])
    if not cubes and (scene_dir/"shader-payloads.json").exists():
        cubes = _read(scene_dir/"shader-payloads.json").get("cubemaps", [])
    cube_by_id = {c["id"]:c for c in cubes}
    camera = [0,0,-1.9115060567855835]
    view = [[1,0,0,0],[0,1,0,0],[0,0,-1,camera[2]],[0,0,0,1]]
    builtins = {"unity_ObjectToWorld":obj["world_matrix"],"unity_WorldToObject":_inverse(obj["world_matrix"]),
                "unity_MatrixVP":vp,"unity_MatrixV":view,"_WorldSpaceCameraPos":camera}
    draws = []
    for slot, mid in enumerate(obj["renderer"]["material_slots"]):
        mat = materials[mid]
        shader = next((s for s in scene["shaders"] if s["id"] == mat["shader_id"]), None)
        contract = _read(CODE_ROOT/'profiles/regular-render-contract-v1.json')
        allowed = contract['shaders'].get(mat['shader_id'],{})
        if not shader or '/ShadowBox/UI/' not in allowed.get('bundle','').replace('\\','/'):
            raise CompilationError("Unsupported original rarity shader: " + str(mat["shader_id"]))
        parsed = _read(_absolute(shader["parsed"], scene_dir))
        shader_pass = parsed["m_SubShaders"][0]["m_Passes"][0]
        names = {v:k for k,v in shader_pass["m_NameIndices"]}
        common = shader_pass["progVertex"]["m_CommonParameters"]
        prefix = "rarity-" + _safe(obj["id"]) + "-%d" % slot
        vertices, indices = _mesh_buffers(out, prefix, mesh, slot=slot, with_normals=True)
        if slot == 0:
            vertices["attributes"] = vertices["attributes"][:2]
        bindings, audit = [], []
        for cb in common["m_ConstantBuffers"]:
            name = names[cb["m_NameIndex"]]
            raw = bytearray((cb["m_Size"]+15)//16*16)
            for field in cb["m_MatrixParams"]+cb["m_VectorParams"]:
                fname = names[field["m_NameIndex"]]
                if fname in builtins:
                    value, source = builtins[fname], "original transform/recovered camera; proof frontal VP"
                elif fname in mat["floats"]:
                    value, source = mat["floats"][fname], "original saved material float"
                elif fname in mat["colors"]:
                    value = [mat["colors"][fname][c] for c in ("r","g","b","a")][:field.get("m_Dim",4)]
                    source = "original saved material color/vector"
                else:
                    raise CompilationError("Unresolved original rarity uniform: " + fname)
                if isinstance(value,list) and value and isinstance(value[0],list):
                    value = _columns(value)
                if not isinstance(value,list):
                    value = [value]
                struct.pack_into("<%df" % len(value), raw, field["m_Index"], *value)
                audit.append({"name":fname,"byte_offset":field["m_Index"],"values":value,"source":source})
            binding = next(v for v in common["m_ConstantBufferBindings"] if v["m_NameIndex"] == cb["m_NameIndex"])
            filename = out/(prefix+"."+_safe(name)+".bin")
            filename.write_bytes(raw)
            bindings.append({"set":(binding["m_Index"]>>16)&255,"binding":binding["m_Index"]&65535,"type":"uniform_buffer","file":str(filename),
                             "stages":["fragment" if (binding["m_Index"]>>24) == 8 else "vertex"]})
        for param in common["m_TextureParams"]:
            property_name = names[param["m_NameIndex"]]
            texture = next(t for t in mat["textures"] if t["property"] == property_name)
            tid = texture["texture_id"]
            if property_name == "_CubeMap":
                if tid not in cube_by_id:
                    raise CompilationError("Original rarity cubemap missing: " + str(tid))
                cube = cube_by_id[tid]
                if len(cube["faces"]) != 6:
                    raise CompilationError("Original cubemap requires six native faces")
                file = out/(prefix+".cubemap.json")
                _write_json(file,{"faces":[str(_absolute(f["png"],scene_dir)) for f in sorted(cube["faces"],key=lambda f:f["index"])],"source":cube})
                kind, address = "combined_image_sampler_cube", "clamp-to-edge"
            else:
                if tid not in textures:
                    raise CompilationError("Original rarity texture missing: " + str(tid))
                texture = textures[tid]
                file = _absolute(texture["png"],scene_dir)
                kind = "combined_image_sampler"
                address = "clamp-to-edge" if texture.get("unity_settings",{}).get("wrap_u") == 1 else "repeat"
            bindings.append({"set":(param["m_Index"]>>16)&255,"binding":param["m_Index"]&65535,"type":kind,"file":str(file),"stages":["fragment"],"sampler":"linear","address_mode":address})
        state = shader_pass["m_State"]["rtBlend0"]
        factors = {0:"zero",1:"one",5:"src_alpha",10:"one_minus_src_alpha"}
        try:
            blend = {"enabled":True,"src_color":factors[int(state["srcBlend"]["val"])],"dst_color":factors[int(state["destBlend"]["val"])],
                     "src_alpha":factors[int(state["srcBlendAlpha"]["val"])],"dst_alpha":factors[int(state["destBlendAlpha"]["val"])]}
        except KeyError as exc:
            raise CompilationError("Unsupported original rarity blend factor") from exc
        draws.append({"name":"Original rarity "+mat["name"],"queue":mat["custom_render_queue"],"vertices":vertices,"indices":indices,
                      "shaders":_stage_pair(root,decoded,shader_id=mat["shader_id"]),"bindings":bindings,
                      "state":{"cull":"back","front_face":"clockwise","depth_test":bool(mat["floats"].get("_ZTest",0)),"depth_write":bool(mat["floats"].get("_ZWrite",0)),"blend":blend},
                      "provenance":{"original_object":obj,"material_id":mid,"submesh_slot":slot,"uniform_audit":audit}})
    return draws


def _prepare_rarity(root, scene_dir, scene, meshes, materials, decoded, out, vp):
    # Repeated diamonds deliberately have the same GameObject name. Identity
    # and original world transforms, not names, distinguish their instances.
    draws = []
    for obj in scene["objects"]:
        if "RareMark" not in obj["name"] or not obj.get("renderer") or not obj.get("active_in_hierarchy", True):
            continue
        draws.extend(_prepare_rarity_object(root, scene_dir, scene, meshes, materials, decoded, out, vp, obj))
    return draws


def prepare_passes(root, scene_dir, ui_spec, outdir, fonts=None):
    """Prepare all card/UI passes; no Vulkan calls and no rendered images.

    The returned consumer_manifest includes base, UI consumer AND rarity. Run
    it with renderer --skip-base. The UI raw image is a declared future output.
    """
    root, scene_dir, out = Path(root).resolve(), Path(scene_dir).resolve(), Path(outdir).resolve()
    if isinstance(ui_spec, (str,Path)):
        ui_spec = _read(ui_spec)
    ui = copy.deepcopy(ui_spec)
    if fonts is not None:
        ui["font_metrics"] = fonts
    out.mkdir(parents=True,exist_ok=True)
    scene = _read(scene_dir/"scene.json")
    materials = {m["id"]:m for m in _read(scene_dir/"materials.json")}
    meshes = {m["id"]:_read(_absolute(m["file"],scene_dir)) for m in scene["meshes"]}
    decoded_path = root/"original-render-pipeline/shaders/decode-manifest.json"
    decoded = _read(decoded_path)["decoded_programs"]
    vp,bounds = _projection(scene,meshes)
    from .dynamic_ui import required_types,ui_for_type
    target_types=required_types(scene) if scene.get('profile')=='original-large-scene-v1' else [0]
    # Layer18 category masks must never leak into Text, including scenes
    # whose original materials do not consume a Holo render target.
    ui_draws,audit,ui_size = _prepare_ui(root,ui_for_type(ui,0),out)
    _write_json(out/"layout-audit.json",audit)
    limits = LIMITS + list(ui.get("limitations", []))
    used_fonts, _ = _font_resources(root,ui)
    for font in used_fonts.values():
        limits.extend(v for v in font.get("limitations", []) if v not in limits)
    ui_manifest = out/"ui-manifest.json"
    _write_json(ui_manifest,{"draws":ui_draws,"limits":limits,"provenance":{"card":ui.get("card_id"),"language":ui.get("language"),"source_scene":str(scene_dir)}})
    raw_ui = out/"ui-native-raw.png"
    raw_targets={0:raw_ui};ui_targets=[]
    for target_type in target_types:
        if target_type==0:continue
        target_dir=out/('ui-target-'+str(target_type))
        target_dir.mkdir(parents=True,exist_ok=True)
        target_spec=ui_for_type(ui,target_type)
        target_draws,target_audit,target_size=_prepare_ui(root,target_spec,target_dir)
        target_manifest=target_dir/'ui-manifest.json'
        target_raw=target_dir/'ui-native-raw.png'
        _write_json(target_dir/'layout-audit.json',target_audit)
        _write_json(target_manifest,{'draws':target_draws,'limits':limits,
            # A non-EX immersive face still has the original Holo consumer,
            # while its EX-only UI children contribute no geometry. Execute
            # the target's ordinary clear instead of inventing a dummy draw.
            'clear_only':not target_draws,'clear_color_rgba':[0,0,0,1],
            'provenance':{'card':ui.get('card_id'),'language':ui.get('language'),
                'dynamic_ui_target':target_spec['dynamic_ui_target']}})
        raw_targets[target_type]=target_raw
        ui_targets.append({'type':target_type,'manifest':target_manifest,'raw_path':target_raw,'size':target_size,
            'draw_count':len(target_draws),'clear_only':not target_draws})
    if scene.get('profile')=='original-large-scene-v1':
        from .special_passes import prepare_scene_draws
        complete_draws,shader_audit,records=prepare_scene_draws(root,scene_dir,scene,meshes,materials,out,vp,raw_ui=raw_ui,exclude_dynamic=False,
            raw_ui_targets=raw_targets if len(target_types)>1 else None)
        base,rarity=complete_draws,[]
        decoded_path=out/'general-decode-manifest.json'
        _write_json(decoded_path,{'decoded_programs':records})
        _write_json(out/'original-shader-audit.json',shader_audit)
        limits.append('General L shader path uses original variants, vertex-channel metadata, uniform bindings and stencil states. Frontal static camera and built-in defaults are explicit; runtime overrides/animation are not certified 1:1.')
    else:
        base = _prepare_base(root,scene_dir,scene,meshes,materials,decoded,out,vp)
        consumer = _prepare_consumer(root,scene_dir,scene,meshes,materials,decoded,out,vp,raw_ui)
        rarity = _prepare_rarity(root,scene_dir,scene,meshes,materials,decoded,out,vp)
        complete_draws=base+[consumer]+rarity
    consumer_manifest = out/"consumer-manifest.json"
    _write_json(consumer_manifest,{"draws":complete_draws,"limits":limits,
        "provenance":{"card":ui.get("card_id"),"language":ui.get("language"),"source_scene":str(scene_dir),"front_bounds":bounds,"ui_manifest":str(ui_manifest)},
        "generated_resources":[str(v) for v in raw_targets.values()]})
    return {"ui_manifest":ui_manifest,"consumer_manifest":consumer_manifest,
            "rarity_manifest":consumer_manifest if rarity else None,
            "ui_raw_path":raw_ui,"ui_targets":ui_targets,"ui_size":ui_size,"output_size":[700,976],"limits":limits,
            "assets_dir":scene_dir,"decoded_shaders":decoded_path,
            "counts":{"ui_draws":len(ui_draws),"base_draws":len(base),"rarity_draws":len(rarity)}}
