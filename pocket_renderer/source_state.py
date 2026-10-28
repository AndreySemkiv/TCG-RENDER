"""Content receipts and data-driven change inventory for imported installations.

An import fingerprint identifies original APK/cache bytes, not a game version
label. A collection download can change while versionName stays the same.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath

from .bluestacks import SourceError, _hash
from .config import CODE_ROOT


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf8')).hexdigest()


def preparation_code_digest():
    """Renderer changes can alter extracted contracts and inventory blockers."""
    paths = list((CODE_ROOT/'pocket_renderer').glob('*.py'))
    paths.extend((CODE_ROOT/'profiles').glob('*.json'))
    # These scripts participate in materialization but are not package modules.
    paths.extend(CODE_ROOT/'scripts'/name for name in
                 ('decode_original_locales.py', 'inspect_original_metadata.py', 'decode_original_master.py'))
    return digest_json({path.relative_to(CODE_ROOT).as_posix():_hash(path) for path in sorted(paths)})


def raw_preparation_code_digest():
    """Keep decoder changes from reusing data produced by an earlier decoder."""
    names = ('pocket_renderer/importer.py', 'pocket_renderer/native_profile.py', 'pocket_renderer/catalog.py', 'pocket_renderer/source_state.py',
             'scripts/inspect_original_metadata.py', 'scripts/decode_original_master.py')
    return digest_json({name:_hash(CODE_ROOT/name) for name in names})


def _input_path(root, relative):
    name = PurePosixPath(relative)
    allowed = ('input/apk/', 'input/downloaded/Default/', 'input/master-cache-original/')
    if (name.is_absolute() or '..' in name.parts or '\\' in relative or ':' in relative
            or not relative.startswith(allowed)):
        raise SourceError('Caminho inválido no inventário da importação: ' + relative)
    path = root.joinpath(*name.parts).resolve()
    if not path.is_relative_to(root):
        raise SourceError('Arquivo importado fora do snapshot: ' + relative)
    return path


def verify_snapshot_inputs(root, progress=None):
    """Verify the complete copied input set, including additions/removals.

    No decoded asset or previous database can satisfy a missing original file.
    This deliberately hashes bytes when opening an imported snapshot.
    """
    root = Path(root).resolve()
    manifest = json.loads((root/'snapshot.json').read_text(encoding='utf8'))
    if not isinstance(manifest, dict):
        raise SourceError('Manifesto de importação inválido.')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not rows or manifest.get('status') in ('copying', 'failed'):
        raise SourceError('A cópia dos arquivos originais está incompleta; importe a instalação novamente.')
    notify = progress or (lambda message: None)
    expected, checked = set(), []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or not isinstance(row.get('file'), str)
                or not isinstance(row.get('bytes'), int) or row['bytes'] < 0
                or not isinstance(row.get('sha256'), str) or len(row['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in row['sha256'])):
            raise SourceError('Registro inválido no inventário da importação.')
        relative = row['file']
        path = _input_path(root, relative)
        if relative in expected:
            raise SourceError('Arquivo duplicado no inventário da importação: ' + relative)
        expected.add(relative)
        if index % 2000 == 0:
            notify('Checking original files: %d/%d' % (index, len(rows)))
        if (not path.is_file() or path.stat().st_size != row['bytes']
                or _hash(path) != row['sha256']):
            raise SourceError('Arquivo original ausente ou alterado: ' + relative + '. Importe novamente a instalação atualizada.')
        checked.append({key: row[key] for key in ('file', 'bytes', 'sha256')})
    notify('Checking original files: %d/%d'%(len(rows),len(rows)))
    actual = set()
    for relative in ('input/apk', 'input/downloaded/Default', 'input/master-cache-original'):
        actual.update(p.relative_to(root).as_posix() for p in (root/relative).rglob('*') if p.is_file())
    if actual != expected:
        raise SourceError('O conjunto de arquivos originais mudou; importe a instalação atualizada em um novo snapshot.')
    return digest_json(sorted(checked, key=lambda row: row['file']))


def source_input_digest(root):
    """Return the receipt used by PNG reuse; legacy workspaces have no receipt."""
    path = Path(root)/'raw-preparation.json'
    if not path.is_file():
        return None
    receipt = json.loads(path.read_text(encoding='utf8'))
    return receipt.get('source_input_sha256') if receipt.get('schema') == 2 else None


def invalidate_readiness(root, raw=False):
    names = ['render-preparation.json', 'prepared-source.json']
    if raw:
        names.append('raw-preparation.json')
    for name in names:
        path = Path(root)/name
        if path.exists():
            path.replace(path.with_name(path.stem+'.previous.json'))


def clear_source_caches():
    """Regenerated paths must not retain JSON, fonts or CAB inventories in RAM."""
    # Inspect only already loaded renderer modules; avoid eager GPU/UI imports.
    import sys
    for name, module in list(sys.modules.items()):
        if not name.startswith('pocket_renderer.'):
            continue
        for value in list(vars(module).values()):
            if callable(getattr(value, 'cache_clear', None)):
                value.cache_clear()


def build_inventory(root, catalog=None):
    """Describe every imported collection/card, with no list of known releases.

    Prefab presence and layout eligibility are preflight facts, not successful
    render counts. Unknown future families stay visible with their blockers.
    """
    from .catalog import Catalog
    from .assets import _prefab_paths
    root = Path(root).resolve()
    catalog = catalog or Catalog(root)
    prefabs = _prefab_paths(str(root))
    cards = {}
    for card in catalog.list_cards():
        cards[card['id']] = {
            'card_id': card['card_id'], 'asset_id': card['asset_id'],
            'collection': card['expansion'],
            'record_sha256': digest_json(card['raw_records']),
            'large_prefab': prefabs.get(card['asset_id']),
            'layout_eligible': card['supported'],
            'blockers': card['unsupported_reasons'],
        }
    collections = []
    for expansion in catalog.table('Expansion'):
        code = expansion['ExpansionID']
        members = [row for row in cards.values() if row['collection'] == code]
        collections.append({'id': code, 'cards': len(members),
                            'large_prefabs_present': sum(bool(row['large_prefab']) for row in members),
                            'layout_eligible': sum(row['layout_eligible'] for row in members)})
    manifest_path = root/'snapshot.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8')) if manifest_path.is_file() else {}
    origin = manifest.get('source') or {key:manifest[key] for key in ('location','serial','package') if key in manifest}
    # Root paths are not source identities: every refresh gets a new snapshot.
    identity = digest_json({key:str(value).casefold() for key,value in origin.items() if key != 'kind'}) if origin.get('location') else None
    return {'schema': 1, 'master_sha256': catalog.master_sha256,
            'source_identity':identity,
            'source_input_sha256': source_input_digest(root),
            'collections': collections, 'cards': cards,
            'limits': ['File presence and layout eligibility do not certify shader preparation or complete rendering.']}


def compare_inventories(current, previous):
    before, after = previous['cards'], current['cards']
    shared = before.keys() & after.keys()
    return {
        'added_collections': sorted({row['id'] for row in current['collections']} -
                                    {row['id'] for row in previous['collections']}),
        'added_cards': sorted(after.keys() - before.keys()),
        'removed_cards': sorted(before.keys() - after.keys()),
        'changed_cards': sorted(key for key in shared
                                if after[key]['record_sha256'] != before[key]['record_sha256']),
        'downloaded_large_prefabs': sorted(key for key in shared
                                          if after[key]['large_prefab'] and not before[key]['large_prefab']),
    }


def previous_inventory(root, current):
    """Find the last complete sibling snapshot from the same selected source."""
    if not current.get('source_identity'):
        return None
    root = Path(root).resolve()
    candidates = sorted(root.parent.glob('*/source-inventory.json'), key=lambda p:p.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        if path.parent.resolve() == root:
            continue
        try:
            receipt = json.loads((path.parent/'render-preparation.json').read_text(encoding='utf8'))
            data = json.loads(path.read_text(encoding='utf8'))
            if receipt.get('complete') and data.get('source_identity') == current['source_identity']:
                return data
        except (OSError, ValueError):
            continue
    return None
