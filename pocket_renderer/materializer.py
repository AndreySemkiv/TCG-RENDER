"""Materialize a selected original installation into a renderable workspace.

The original master and locales come from that source. Shader stages and font
atlases are decoded again from its bundles. Only inspected layout contracts
are versioned in the source project; no old card database is substituted.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import warnings

from .config import CODE_ROOT, PROJECT_ROOT
from .source_validation import load_profile, validate_profile_source, validate_prepared_source


def tools_directory():
    import os
    candidates = [Path(os.environ['TCG_RENDER_TOOLS'])] if os.environ.get('TCG_RENDER_TOOLS') else []
    candidates += [CODE_ROOT/'tools', PROJECT_ROOT/'tools']
    for path in candidates:
        if (path/'aladump/aladump.exe').is_file():
            return path
    raise ValueError('Dependência aladump.exe ausente. Consulte IMPORT-SOURCES.md.')


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str),encoding='utf8')
    temporary.replace(path)


def _module(name):
    path = CODE_ROOT/'scripts'/(name+'.py')
    spec = importlib.util.spec_from_file_location('_tcg_'+name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_dependency_index(root, progress=None):
    """Index shared serialized files; per-card dependencies extend lazily."""
    import UnityPy
    root = Path(root)
    source = root/'input/aladin-complete/output'
    notify = progress or (lambda message:None)
    candidates = set()
    for relative in ('Common/CardNew/Common','Common/CardNew/System','Common/CardNew/Template','Common/Shader'):
        candidates.update(p for p in (source/relative).rglob('*') if p.is_file())
    candidates.update(p for p in source.iterdir() if p.is_file() and p.name != 'catalog.bytes' and not p.name.startswith('src_cph'))
    candidates.add(source/'Common/Font_bundles')
    rows = []
    for index, path in enumerate(sorted(candidates)):
        if index % 200 == 0:
            notify('Indexing shared resources: %d/%d' % (index,len(candidates)))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            env = UnityPy.load(str(path))
        relative = path.relative_to(source).as_posix()
        for asset in env.assets:
            if not getattr(asset,'name','').lower().startswith('cab-'):
                continue
            rows.append({'cab':asset.name.lower(),'file':relative,'objects':len(asset.objects),
                         'externals':[external.name.lower() for external in asset.externals]})
    if not rows:
        raise ValueError('Nenhum recurso Unity compartilhado foi indexado.')
    notify('Indexing shared resources: %d/%d'%(len(candidates),len(candidates)))
    _write(root/'reports/file-only-analysis/dependencies-index.json',rows)
    return rows


def extract_locales(root):
    import UnityPy
    root = Path(root)
    parser = _module('decode_original_locales')
    parser.ROOT = root
    results = []
    for language in ('en_US','pt_BR','ja_JP'):
        source = root/'input/aladin-complete/output/Common/Locale'/(language+'_bundles')
        if not source.is_file():
            raise ValueError('Idioma original não baixado: '+language)
        out = root/'reports/file-only-analysis/locale-original'/language
        out.mkdir(parents=True,exist_ok=True)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            env = UnityPy.load(str(source))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        count = 0
        required = {'Master','UI','System','Master_Attribute','UI_Attribute','System_Attribute'}
        seen, ignored = set(), []
        for obj in env.objects:
            if obj.type.name != 'TextAsset':
                continue
            data = obj.read()
            if data.m_Name not in required:
                ignored.append(data.m_Name)
                continue
            if data.m_Name in seen:
                raise ValueError('Tabela de idioma original duplicada: '+data.m_Name)
            seen.add(data.m_Name)
            path = out/(data.m_Name+'.kvrom')
            raw = data.m_Script.encode('utf8','surrogateescape') if isinstance(data.m_Script,str) else bytes(data.m_Script)
            path.write_bytes(raw)
            decoded = parser.parse(path)
            decoded['source_bundle'] = {'file':source.relative_to(root).as_posix(),'sha256':source_hash,'path_id':obj.path_id}
            _write(path.with_suffix('.json'),decoded)
            count += 1
        if seen != required:
            raise ValueError('Tabelas de idioma ausentes em '+language+': '+', '.join(sorted(required-seen)))
        results.append({'language':language,'tables':count,'source_sha256':source_hash,'unused_text_assets':ignored})
    return results


def _materialize_unlocked(root, progress=None):
    import UnityPy
    from .localization import _OriginalUIResources
    from .japanese_fonts import load_font_set
    from .shader_import import extract_shader, write_manifest
    from .catalog import Catalog
    root = Path(root).resolve()
    catalog = Catalog(root)  # Verify this source's own master provenance before preparing.
    notify = progress or (lambda message:None)
    UnityPy.config.FALLBACK_UNITY_VERSION = '6000.0.78f1'
    profile = load_profile()
    notify('Checking shaders, fonts and source layout...')
    source_check = validate_profile_source(root)
    index = build_dependency_index(root, notify)
    notify('Decoding original text in all three languages...')
    locales = extract_locales(root)
    resources = _OriginalUIResources(root)
    notify('Extracting original fonts and atlases...')
    font_out = root/'original-render-pipeline/text'
    fonts = load_font_set(root,resources,font_out,{
        'bold':-396364928729778045,'medium':8884153184787638352,'number':-757749988448016049})
    standard_names = {'bold':'TPC_GillSansNovaBold.otf','medium':'TPC_GillSansNovaMedium.otf','number':'TPC_FuturaLTProHeavy.otf'}
    for role,font in fonts.items():
        target = root/'offline-render/bulbasaur-original/fonts'/standard_names[role]
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(font['original_font_file'],target)
        font['original_font_file'] = str(target)
    _write(font_out/'font-metrics.json',fonts)
    template = json.loads((CODE_ROOT/'profiles/ui-layout-v1.json').read_text(encoding='utf8'))
    if template['source_ui_sha256'] != profile['bundles']['Common/CardNew/System/Prefabs/PokemonCardUI.prefab_bundles']:
        raise ValueError('O contrato de layout e o perfil de origem não correspondem.')
    _write(root/'offline-render/bulbasaur-original/ui-layout.json',template)
    shader_records, shader_count = [], 0
    shader_paths=[relative for relative in profile['bundles'] if relative.endswith('.shader_bundles')]
    for shader_index,relative in enumerate(shader_paths):
        notify('Decoding original shaders: %d/%d - %s'%(shader_index,len(shader_paths),Path(relative).name))
        if relative.endswith('Card_TextMeshPro_Mobile_DistanceField_ToRT.shader_bundles'):
            raw, decoded, prefix = font_out, font_out/'decoded-shaders', 'tmp-sdf'
        elif relative.endswith('Card_UI_Default_ToRT.shader_bundles'):
            raw = root/'original-render-pipeline/ui-sprites'
            decoded, prefix = raw/'decoded-shaders', 'ui-default'
        else:
            raw = root/'offline-render/bulbasaur-original/shaders'
            decoded, prefix = root/'original-render-pipeline/shaders', None
        records = extract_shader(root,relative,raw,decoded,prefix)
        if prefix:
            write_manifest(decoded/'decode-manifest.json',records)
        else:
            shader_records.extend(records)
        shader_count += len(records)
    notify('Decoding original shaders: %d/%d'%(len(shader_paths),len(shader_paths)))
    write_manifest(root/'original-render-pipeline/shaders/decode-manifest.json',shader_records)
    check = validate_prepared_source(root)
    from .source_state import build_inventory, compare_inventories, previous_inventory, source_input_digest, preparation_code_digest
    notify('Discovering collections, cards and large models...')
    inventory = build_inventory(root, catalog)
    previous = previous_inventory(root, inventory)
    if previous:
        inventory['changes'] = compare_inventories(inventory, previous)
        inventory['previous_master_sha256'] = previous['master_sha256']
    _write(root/'source-inventory.json', inventory)
    artifacts = [root/'reports/file-only-analysis/dependencies-index.json',
                 font_out/'font-metrics.json', root/'offline-render/bulbasaur-original/ui-layout.json',
                 root/'source-inventory.json']
    artifacts.extend((root/'reports/file-only-analysis/locale-original').rglob('*.*'))
    result = {'schema':2, 'complete':True, 'source_input_sha256':source_input_digest(root),
        'preparation_code_sha256':preparation_code_digest(),
        'source_master_sha256':catalog.master_sha256,
        'root':str(root),'prepared':True,'profile':profile['profile'],
        'shared_bundles':len(index),'locales':locales,'original_shader_stages':shader_count,
        'source_validation':source_check,'prepared_validation':check,
        'artifacts':{path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in artifacts if path.is_file()},
        'inventory_summary':{'collections':len(inventory['collections']), 'cards':len(inventory['cards']),
                             'large_prefabs_present':sum(bool(row['large_prefab']) for row in inventory['cards'].values()),
                             'changes':inventory.get('changes')},
        'database_substituted':False,'source_rasters_resized':False,'game_executed':False}
    _write(root/'render-preparation.json',result)
    return result


def materialize(root, progress=None):
    """Publish readiness only after an exclusive, successful materialization."""
    from .source_state import clear_source_caches, invalidate_readiness
    from .importer import _snapshot_lock
    root = Path(root).resolve()
    with _snapshot_lock(root):
        invalidate_readiness(root)
        clear_source_caches()
        return _materialize_unlocked(root, progress)


def _raw_receipt_valid(root, input_digest):
    from .bluestacks import _hash
    from .importer import NATIVE_PROFILES
    from .source_state import raw_preparation_code_digest
    try:
        receipt = json.loads((root/'raw-preparation.json').read_text(encoding='utf8'))
        if (receipt.get('schema') != 2 or not receipt.get('complete')
                or receipt.get('source_input_sha256') != input_digest
                or receipt.get('raw_preparation_code_sha256') != raw_preparation_code_digest()):
            return False
        native = receipt['native_files']
        if not {'libil2cpp.so','libunity.so'} <= native.keys() or native['libil2cpp.so'] not in NATIVE_PROFILES:
            return False
        for name, digest in native.items():
            if name not in ('libil2cpp.so','libunity.so') or _hash(root/'input/native-code'/name) != digest:
                return False
        return (_hash(root/'input/master-cache-decoded/MasterMemory.bytes') == receipt['master']['sha256']
                and _hash(root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat') == receipt['metadata']['decoded_sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _prepared_receipt(root, input_digest):
    from .bluestacks import _hash
    from .catalog import Catalog
    from .source_state import preparation_code_digest
    try:
        receipt = json.loads((root/'render-preparation.json').read_text(encoding='utf8'))
        if (receipt.get('schema') != 2 or not receipt.get('complete') or not receipt.get('prepared')
                or receipt.get('source_input_sha256') != input_digest or not receipt.get('artifacts')
                or receipt.get('preparation_code_sha256') != preparation_code_digest()
                or receipt.get('profile') != load_profile()['profile']
                or receipt.get('source_master_sha256') != Catalog(root).master_sha256):
            return None
        for name, digest in receipt['artifacts'].items():
            path = (root/name).resolve()
            if not path.is_relative_to(root) or _hash(path) != digest:
                return None
        validate_prepared_source(root)
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare_import(snapshot, progress=None, tools_root=None):
    """Menu integration: raw copy -> source-specific decode -> render resources."""
    from .importer import _snapshot_lock, _prepare_snapshot_unlocked
    from .source_state import clear_source_caches, invalidate_readiness, verify_snapshot_inputs
    root = Path(snapshot['root'] if isinstance(snapshot,dict) else snapshot).resolve()
    if not root.is_relative_to(CODE_ROOT.parent.resolve()) or not (root/'snapshot.json').is_file():
        raise ValueError('Snapshot inválido ou fora da pasta de trabalho.')
    result = dict(snapshot) if isinstance(snapshot,dict) else {'root':str(root)}
    notify = progress or (lambda message:None)
    with _snapshot_lock(root):
        # Never leave an earlier ready marker after an incomplete/changed copy.
        try:
            input_digest = verify_snapshot_inputs(root, notify)
        except Exception:
            invalidate_readiness(root, raw=True)
            clear_source_caches()
            raise
        clear_source_caches()
        raw_valid = _raw_receipt_valid(root, input_digest)
        cached = _prepared_receipt(root, input_digest) if raw_valid else None
        if cached:
            notify('Original files and previous preparation verified; reusing this snapshot.')
            result.update(cached)
        else:
            invalidate_readiness(root, raw=not raw_valid)
            if not raw_valid:
                tools = Path(tools_root) if tools_root else tools_directory()
                result.update(_prepare_snapshot_unlocked(root,tools,progress=progress,input_digest=input_digest))
            result.update(_materialize_unlocked(root,progress))
        result['description'] = 'Files imported from the selected installation'
        result['warnings'] = ['Collections reflect the selected local download; update the game and import again to discover new cards.',
                              '']
        _write(root/'prepared-source.json',result)
    return result
