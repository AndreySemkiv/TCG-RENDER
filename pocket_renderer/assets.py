"""Export a bounded original regular Pokemon scene for batch preparation.

This module decodes original Texture2D/Cubemap pixels at native dimensions; it
does not render cards or resize images. Unsupported layouts/shaders are rejected
explicitly instead of reusing another card's art or materials.
"""
from collections import deque
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os
import re
import warnings

from .catalog import project_root
from .config import CODE_ROOT


PROFILE = "pokemon-window-v1"
EXPORT_SCHEMA = 4
TERMINAL_TYPES = {"Shader", "MonoScript", "Font", "Texture2D", "Cubemap", "Mesh", "TextAsset", "AnimationClip"}
REQUIRED_RENDERERS = {"Card_F", "L_Pokemon_Window_Image", "L_Pokemon_Full_EnergyType_BG", "L_UI_Panel_Pokemon", "L_UI_Dynamic_Text"}


class AssetError(ValueError):
    pass


def _read(path):
    return json.loads(path.read_text(encoding="utf8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(value):
    return str(value).replace("\\", "/")


@lru_cache(maxsize=4)
def _shared_index(root):
    path = Path(root) / "reports/file-only-analysis/dependencies-index.json"
    return {v["cab"].lower(): _relative(v["file"]) for v in _read(path)}


@lru_cache(maxsize=4)
def _prefab_paths(root):
    source = Path(root) / "input/aladin-complete/output"
    result = {}
    for parent, directories, files in os.walk(source / "Common/CardNew/Face"):
        if Path(parent).name != "Prefabs" or Path(parent).parent.name != "L":
            continue
        for name in files:
            if name.endswith("_L.prefab_bundles"):
                asset = name[:-len('_L.prefab_bundles')]
                relative = str((Path(parent)/name).relative_to(source))
                result[asset] = relative
                try:
                    canonical = asset.encode('cp1252').decode('utf8')
                    if canonical.encode('utf8').decode('cp1252') == asset:
                        result.setdefault(canonical,relative)
                except (UnicodeEncodeError,UnicodeDecodeError):
                    pass
    return result


def _refs(value):
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            yield value
        else:
            for item in value.values():
                yield from _refs(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _refs(item)


def _oid(key):
    return key[0] + "__" + str(key[1])


def _vector(value, components="xyz"):
    return [value[k] for k in components]


def _multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _trs(value):
    x, y, z, w = _vector(value["m_LocalRotation"], "xyzw")
    sx, sy, sz = _vector(value["m_LocalScale"])
    tx, ty, tz = _vector(value["m_LocalPosition"])
    return [[(1-2*(y*y+z*z))*sx, 2*(x*y-z*w)*sy, 2*(x*z+y*w)*sz, tx],
            [2*(x*y+z*w)*sx, (1-2*(x*x+z*z))*sy, 2*(y*z-x*w)*sz, ty],
            [2*(x*z-y*w)*sx, 2*(y*z+x*w)*sy, (1-2*(x*x+y*y))*sz, tz], [0,0,0,1]]


class _Store:
    def __init__(self, root, prefab):
        import UnityPy
        from .native_profile import unity_version
        UnityPy.config.FALLBACK_UNITY_VERSION = unity_version(root)
        self.root, self.source = root, root / "input/aladin-complete/output"
        self.cab_files = dict(_shared_index(str(root)))
        self.objects, self.serialized, self.environments, self.provenance, self.trees = {}, {}, {}, {}, {}
        # The global index intentionally excludes the other thousands of card
        # folders. Add only this selected card's L resources before traversal.
        directory = (self.source / prefab).parent.parent
        for file in directory.rglob("*"):
            if file.is_file():
                self.load(str(file.relative_to(self.source)))

    def load(self, relative):
        import UnityPy
        relative = _relative(relative)
        if relative in self.environments:
            return
        path = self.source / relative
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            env = UnityPy.load(str(path))
        self.environments[relative] = env
        digest = _sha(path)
        for sf in env.assets:
            cab = sf.name.lower()
            self.serialized[cab] = sf
            self.cab_files[cab] = relative
            for path_id, obj in sf.objects.items():
                key = (cab, path_id)
                self.objects[key] = obj
                self.provenance[key] = {"cab":cab, "path_id":path_id, "bundle":relative, "bundle_sha256":digest}

    def tree(self, key):
        if key not in self.trees:
            self.trees[key] = self.objects[key].read_typetree()
        return self.trees[key]

    def resolve(self, key, pointer):
        path_id, file_id = pointer["m_PathID"], pointer["m_FileID"]
        if not path_id:
            return None
        sf = self.serialized[key[0]]
        if file_id == 0:
            cab = key[0]
        elif 0 < file_id <= len(sf.externals):
            cab = sf.externals[file_id-1].name.lower()
        else:
            raise AssetError("Invalid original PPtr file index")
        target = (cab, path_id)
        if target not in self.objects:
            relative = self.cab_files.get(cab)
            if not relative and cab in ('unity default resources','unity_builtin_extra'):
                builtin = self.root/'input/unity-data/Resources'/cab
                if builtin.is_file():
                    relative=os.path.relpath(builtin,self.source).replace('\\','/')
            if not relative:
                from .bundle_index import find_cab
                try:
                    relative = find_cab(self.source, cab)
                    self.cab_files[cab] = relative
                except ValueError as error:
                    raise AssetError(str(error)) from error
            self.load(relative)
        if target not in self.objects:
            raise AssetError("Original dependency path ID not found: " + _oid(target))
        return target

    def graph(self, prefab):
        roots = [key for key, source in self.provenance.items() if _relative(source["bundle"]) == _relative(prefab)]
        queue, seen = deque(roots), set()
        while queue:
            key = queue.popleft()
            if key in seen:
                continue
            seen.add(key)
            kind = self.objects[key].type.name
            if kind in TERMINAL_TYPES or kind == "AssetBundle":
                continue
            for pointer in _refs(self.tree(key)):
                target = self.resolve(key, pointer)
                if target:
                    queue.append(target)
        return sorted(seen)


def _eligibility(card):
    reasons = list(card.get("unsupported_reasons", []))
    mon = card.get("pokemon")
    if not mon:
        reasons.append("pokemon-window-profile-requires-pokemon-record")
    else:
        if len(mon.get("PokemonTypes", [])) != 1:
            reasons.append("multiple-energy-types-not-supported")
    return list(dict.fromkeys(reasons))


def prepare_assets(root, card, outdir, generalized=False):
    """Return exported original scene/material paths, or an explicit rejection.

    Root is the pocket-extraction directory (workspace root also accepted).
    Card is a Catalog.resolve result. Existing compatible exports are reused
    only when the selected card and source prefab hash still match.
    """
    root, outdir = project_root(root), Path(outdir).resolve()
    result = {"supported":False, "card_id":card["card_id"], "asset_id":card["asset_id"],
              "profile":PROFILE, "scene_dir":outdir, "scene":outdir/"scene.json", "materials":outdir/"materials.json"}
    profile = 'original-large-scene-v1' if generalized else PROFILE
    result['profile'] = profile
    reasons = [] if generalized else _eligibility(card)
    if reasons:
        return dict(result, reasons=reasons)
    prefab = _prefab_paths(str(root)).get(card["asset_id"])
    if not prefab:
        return dict(result, reasons=["original-large-prefab-not-downloaded"])
    prefab_path = root/"input/aladin-complete/output"/prefab
    prefab_sha = _sha(prefab_path)
    completed = outdir/"prepared-assets.json"
    if completed.exists():
        cached = _read(completed)
        sources = cached.get("source_bundle_hashes", {})
        outputs = cached.get("output_hashes", {})
        sources_match = bool(sources) and all((root/"input/aladin-complete/output"/name).is_file() and _sha(root/"input/aladin-complete/output"/name) == digest for name,digest in sources.items())
        outputs_match = bool(outputs) and all((outdir/name).is_file() and _sha(outdir/name) == digest for name,digest in outputs.items())
        if cached.get("schema") == EXPORT_SCHEMA and cached.get('profile') == profile and cached.get("asset_id") == card["asset_id"] and cached.get("prefab_sha256") == prefab_sha and sources_match and outputs_match:
            return dict(result, supported=True, cached=True, prefab=prefab, prefab_sha256=prefab_sha)
    try:
        store = _Store(root, prefab)
        keys = store.graph(prefab)
        trees = {key:store.tree(key) for key in keys if store.objects[key].type.name not in TERMINAL_TYPES}
        transforms = {key:value for key,value in trees.items() if store.objects[key].type.name == "Transform"}
        world = {}
        def get_world(key):
            if key not in world:
                tree = transforms[key]
                parent = store.resolve(key, tree["m_Father"])
                local = _trs(tree)
                world[key] = _multiply(get_world(parent), local) if parent else local
            return world[key]
        objects = []
        for key, tree in trees.items():
            if store.objects[key].type.name != "GameObject":
                continue
            components = [store.resolve(key, v["component"]) for v in tree["m_Component"]]
            transform = next(k for k in components if store.objects[k].type.name == "Transform")
            td = trees[transform]
            parent = store.resolve(transform, td["m_Father"])
            renderer = next((k for k in components if store.objects[k].type.name == "MeshRenderer"), None)
            meshfilter = next((k for k in components if store.objects[k].type.name == "MeshFilter"), None)
            node = {"id":_oid(key), "name":tree["m_Name"], "active_self":bool(tree["m_IsActive"]),
                    "transform_id":_oid(transform), "parent_transform_id":_oid(parent) if parent else None,
                    "local_position":_vector(td["m_LocalPosition"]), "local_rotation_xyzw":_vector(td["m_LocalRotation"],"xyzw"),
                    "local_scale":_vector(td["m_LocalScale"]), "world_matrix":get_world(transform), "source":store.provenance[key],
                    "components":[{"id":_oid(k),"type":store.objects[k].type.name} for k in components]}
            if generalized:
                node['serialized_components'] = [{'id':_oid(k),'type':store.objects[k].type.name,'data':trees[k]}
                    for k in components if store.objects[k].type.name == 'MonoBehaviour']
            if renderer:
                rd = trees[renderer]
                material_keys = [store.resolve(renderer, v) for v in rd["m_Materials"]]
                mk = store.resolve(meshfilter, trees[meshfilter]["m_Mesh"]) if meshfilter else None
                node["renderer"] = {"id":_oid(renderer), "enabled":bool(rd["m_Enabled"]),
                                    "material_slots":[_oid(k) for k in material_keys], "mesh":_oid(mk) if mk else None,
                                    "sorting_order":rd["m_SortingOrder"], "sorting_layer_id":rd["m_SortingLayerID"]}
            objects.append(node)
        bytransform = {v["transform_id"]:v for v in objects}
        def active(node):
            parent = node["parent_transform_id"]
            return node["active_self"] and (active(bytransform[parent]) if parent else True)
        for node in objects:
            node["active_in_hierarchy"] = active(node)
        renderer_names = {v["name"] for v in objects if "renderer" in v}
        if not generalized and not REQUIRED_RENDERERS.issubset(renderer_names):
            return dict(result, reasons=["original-prefab-layout-is-not-regular-pokemon-window"], renderer_names=sorted(renderer_names))
        # Only the already decoded original shader assets are eligible; new
        # variants are not guessed or silently replaced by a similar shader.
        reference = _read(CODE_ROOT/"profiles/regular-render-contract-v1.json")
        known_renderers = set(reference["renderer_names"])
        extra_renderers = sorted({v["name"] for v in objects if "renderer" in v and v["active_in_hierarchy"] and v["renderer"]["enabled"] and v["name"] not in known_renderers})
        if not generalized and extra_renderers:
            return dict(result, reasons=["additional-original-renderer-not-supported"], extra_renderers=extra_renderers)
        allowed_shaders = reference["shaders"]
        shader_keys = [k for k in keys if store.objects[k].type.name == "Shader"]
        unknown = [_oid(k) for k in shader_keys if _oid(k) not in allowed_shaders]
        if not generalized and unknown:
            return dict(result, reasons=["original-shader-variant-not-supported"], unknown_shaders=unknown)
        changed = [_oid(k) for k in shader_keys if _oid(k) in allowed_shaders and store.provenance[k]["bundle_sha256"] != allowed_shaders[_oid(k)]["bundle_sha256"]]
        if not generalized and changed:
            return dict(result, reasons=["original-shader-source-hash-does-not-match-decoded-program"], changed_shaders=changed)
        keyword_materials = [store.tree(k)["m_Name"] for k in keys if store.objects[k].type.name == "Material" and store.tree(k).get("m_ValidKeywords", store.tree(k).get("m_ShaderKeywords", []))]
        if not generalized and keyword_materials:
            return dict(result, reasons=["material-keyword-variant-not-supported"], keyword_materials=keyword_materials)
        outdir.mkdir(parents=True, exist_ok=True)
        for name in ("meshes","textures","shaders"):
            (outdir/name).mkdir(exist_ok=True)
        meshes, textures, materials, shaders, cubemaps = [], [], [], [], []
        from UnityPy.helpers.MeshHelper import MeshHandler
        from UnityPy.export.Texture2DConverter import parse_image_data
        from PIL import Image
        for key in keys:
            obj, oid = store.objects[key], _oid(key)
            kind = obj.type.name
            if kind == "Mesh":
                mesh = obj.read()
                handler = MeshHandler(mesh)
                handler.process()
                submeshes = []
                for slot,(sm, triangles) in enumerate(zip(mesh.m_SubMeshes,handler.get_triangles())):
                    stride = 2 if handler.m_Use16BitIndices else 4
                    start, base = sm.firstByte//stride, sm.baseVertex or 0
                    raw = handler.m_IndexBuffer[start:start+sm.indexCount]
                    submeshes.append({"slot":slot,"topology":int(sm.topology),"base_vertex":base,"first_vertex":sm.firstVertex,
                                      "vertex_count":sm.vertexCount,"first_byte":sm.firstByte,"index_count":sm.indexCount,
                                      "raw_indices":raw,"triangles":[[int(x+base) for x in t] for t in triangles]})
                item = {"id":oid,"name":mesh.m_Name,"source":store.provenance[key],"vertices":handler.m_Vertices,
                        "normals":handler.m_Normals,"tangents":handler.m_Tangents,"colors":handler.m_Colors,"submeshes":submeshes,
                        "coordinate_convention":"Original Unity local vertices and UVs; no coordinate/UV conversion"}
                for channel in range(8):
                    item["uv"+str(channel)] = getattr(handler,"m_UV"+str(channel),None)
                item["bounds"] = {"min":[min(v[i] for v in item["vertices"]) for i in range(3)],"max":[max(v[i] for v in item["vertices"]) for i in range(3)]}
                file = "meshes/"+oid+".json"
                _write(outdir/file,item)
                meshes.append({"id":oid,"name":mesh.m_Name,"file":file,"vertex_count":len(item["vertices"]),"submesh_count":len(submeshes)})
            elif kind == "Texture2D":
                texture = obj.read()
                raw = bytes(texture.get_image_data())
                safe = re.sub(r"[^a-zA-Z0-9_.-]","_",texture.m_Name)+"__"+str(key[1])
                rawfile, pngfile = "textures/"+safe+".data.bin", "textures/"+safe+".png"
                (outdir/rawfile).write_bytes(raw)
                image = Image.frombytes("I;16",(texture.m_Width,texture.m_Height),raw).transpose(Image.Transpose.FLIP_TOP_BOTTOM) if int(texture.m_TextureFormat) == 9 else texture.image
                image.save(outdir/pngfile)
                settings = texture.m_TextureSettings
                textures.append({"id":oid,"name":texture.m_Name,"source":store.provenance[key],"width":texture.m_Width,"height":texture.m_Height,
                                 "format":int(texture.m_TextureFormat),"png":pngfile,"png_sha256":_sha(outdir/pngfile),"raw_data":rawfile,"raw_sha256":hashlib.sha256(raw).hexdigest(),
                                 "png_conversion":"Native original Texture2D decode only; no resize, alpha inversion, sharpening or correction",
                                 "unity_settings":{"filter_mode":settings.m_FilterMode,"aniso":settings.m_Aniso,"mip_bias":settings.m_MipBias,"wrap_u":settings.m_WrapU,"wrap_v":settings.m_WrapV,"color_space":texture.m_ColorSpace,"mip_count":texture.m_MipCount}})
            elif kind == "Cubemap":
                cube = obj.read()
                raw = bytes(cube.get_image_data())
                if cube.m_MipCount != 1 or len(raw) != cube.m_ImageCount*cube.m_CompleteImageSize:
                    raise AssetError("Cubemap mip layout is not supported by the current original-face exporter")
                rawfile = "textures/"+oid+".cubemap.bin"
                (outdir/rawfile).write_bytes(raw)
                faces = []
                for index in range(cube.m_ImageCount):
                    face = raw[index*cube.m_CompleteImageSize:(index+1)*cube.m_CompleteImageSize]
                    image = parse_image_data(face,cube.m_Width,cube.m_Height,cube.m_TextureFormat,obj.version,obj.platform,cube.m_PlatformBlob,True)
                    file = "textures/"+oid+".face"+str(index)+".png"
                    image.save(outdir/file)
                    faces.append({"index":index,"png":file,"sha256":_sha(outdir/file)})
                cubemaps.append({"id":oid,"source":store.provenance[key],"name":cube.m_Name,"width":cube.m_Width,"height":cube.m_Height,
                                 "format":int(cube.m_TextureFormat),"face_count":cube.m_ImageCount,"mip_count":cube.m_MipCount,
                                 "raw_data":{"file":rawfile,"sha256":hashlib.sha256(raw).hexdigest()},"faces":faces})
            elif kind == "Material":
                md = trees[key]
                props, bindings = md["m_SavedProperties"], []
                for property_name,value in props["m_TexEnvs"]:
                    target = store.resolve(key,value["m_Texture"])
                    bindings.append({"property":property_name,"texture_id":_oid(target) if target else None,
                                     "scale":_vector(value["m_Scale"],"xy"),"offset":_vector(value["m_Offset"],"xy")})
                shader = store.resolve(key,md["m_Shader"])
                materials.append({"id":oid,"name":md["m_Name"],"source":store.provenance[key],"shader_id":_oid(shader),"textures":bindings,
                                  "floats":dict(props.get("m_Floats",[])),"ints":dict(props.get("m_Ints",[])),"colors":dict(props.get("m_Colors",[])),
                                  "shader_keywords":md.get("m_ValidKeywords",md.get("m_ShaderKeywords")),"custom_render_queue":md.get("m_CustomRenderQueue"),"original_typetree":md})
            elif kind == "Shader":
                data = obj.read_typetree()
                parsed = data["m_ParsedForm"]
                file = "shaders/"+oid+".parsed.json"
                _write(outdir/file,parsed)
                shaders.append({"id":oid,"name":parsed["m_Name"],"source":store.provenance[key],"parsed":file,"platforms":data.get("platforms"),
                                "decoded_program_manifest":str(root/"original-render-pipeline/shaders/decode-manifest.json")})
        manifest = {"card":card["asset_id"],"card_id":card["card_id"],"profile":profile,"source":"Original copied game files only",
                    "prefab":prefab,"prefab_sha256":prefab_sha,"coordinate_convention":"Original Unity coordinates, UVs and TRS; row-major world matrices for column vectors",
                    "unity_version_fallback":"6000.0.78f1","objects":objects,"meshes":meshes,"textures":textures,"materials":materials,"shaders":shaders,"cubemaps":cubemaps,
                    "errors":[],"limits":["Scene export only; no GPU rendering, image resize or runtime state observation", "Original dynamic UI placeholder remains unchanged; localized text layer must be prepared separately", "Current supported profile is ordinary regular-window Pokemon, not EX/mega/abilities/mirror/special-art"]}
        _write(outdir/"scene.json",manifest)
        _write(outdir/"materials.json",materials)
        _write(outdir/"shader-payloads.json",{"source":"Original native cubemap face export; shader bytecode is reused only by matching original shader IDs","cubemaps":cubemaps})
        source_bundle_hashes = {store.provenance[k]["bundle"]:store.provenance[k]["bundle_sha256"] for k in keys}
        output_hashes = {str(p.relative_to(outdir)).replace("\\","/"):_sha(p) for p in outdir.rglob("*") if p.is_file() and p != completed}
        _write(completed,{"schema":EXPORT_SCHEMA,"asset_id":card["asset_id"],"card_id":card["card_id"],"profile":profile,"prefab":prefab,"prefab_sha256":prefab_sha,
                          "scene_sha256":_sha(outdir/"scene.json"),"materials_sha256":_sha(outdir/"materials.json"),
                          "source_bundle_hashes":source_bundle_hashes,"output_hashes":output_hashes})
        return dict(result,supported=True,cached=False,prefab=prefab,prefab_sha256=prefab_sha,objects=len(objects),textures=len(textures),meshes=len(meshes),cubemaps=len(cubemaps))
    except (AssetError,KeyError,ValueError,StopIteration,NotImplementedError,OSError) as exc:
        return dict(result,reasons=["original-resource-preparation-failed"],error=str(exc))
