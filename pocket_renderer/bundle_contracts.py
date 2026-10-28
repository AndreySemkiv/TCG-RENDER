"""Source resource contracts that survive unrelated Unity bundle additions.

Only local source bytes are read. Shader bytecode remains checked separately;
this module verifies the concrete UI nodes/fonts the existing renderer uses.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import warnings

from .config import CODE_ROOT


PROFILE = CODE_ROOT/'profiles/shared-resource-contract-v1.json'
UI = 'Common/CardNew/System/Prefabs/PokemonCardUI.prefab_bundles'
FONTS = 'Common/Font_bundles'
CONDITIONS = 'Common/CardNew/Template/L/Settings/FontGroupConditions/Pokemon_Normal.asset_bundles'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def canonical(value, asset_file):
    """Replace an external PPtr index with its actual serialized-file identity."""
    if isinstance(value, dict):
        if set(value) == {'m_FileID', 'm_PathID'}:
            index, path = value['m_FileID'], value['m_PathID']
            if not path:
                return {'pointer': None}
            if index < 0 or index > len(asset_file.externals):
                raise ValueError('A source resource pointer has an invalid file index.')
            owner = asset_file.name if index == 0 else asset_file.externals[index-1].name
            return {'pointer': [owner.lower(), path]}
        return {key: canonical(item, asset_file) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item, asset_file) for item in value]
    return value


def object_contract(obj, mode):
    if mode == 'raw':
        raw = obj.get_raw_data()
        # Streamed atlas payloads live outside the Texture2D object's own bytes.
        # Include those bytes too, otherwise a changed resS could evade a match.
        if obj.type.name == 'Texture2D':
            texture = obj.read()
            if texture.image_data or texture.m_StreamData.size:
                raw += texture.get_image_data()
        return hashlib.sha256(raw).hexdigest()
    data = obj.read_typetree()
    if mode == 'controller':
        # The native owner-name controller may replace the additional TMP node
        # while keeping its text/layout contract. Bind and validate that node by
        # its content and parent geometry, rather than its arbitrary path ID.
        pointer = data['_additionalText']
        if pointer['m_FileID'] != 0 or not pointer['m_PathID']:
            raise ValueError('The additional name field is not local to its UI prefab.')
        target = obj.assets_file.objects[pointer['m_PathID']]
        text = target.read_typetree()
        if target.type.name != 'MonoBehaviour' or 'm_text' not in text:
            raise ValueError('The additional name field is not a TMP component.')
        go = obj.assets_file.objects[text['m_GameObject']['m_PathID']].read_typetree()
        transforms = [obj.assets_file.objects[c['component']['m_PathID']]
                      for c in go['m_Component']
                      if obj.assets_file.objects[c['component']['m_PathID']].type.name == 'RectTransform']
        if len(transforms) != 1:
            raise ValueError('The additional name field transform is ambiguous.')
        rect = transforms[0].read_typetree()
        data['_additionalText'] = {'text': {k: v for k, v in text.items() if k != 'm_GameObject'},
            'rect': {k: v for k, v in rect.items() if k != 'm_GameObject'},
            'gameobject': {k: go[k] for k in ('m_Name', 'm_IsActive', 'm_Layer')}}
        # This new setting only affects the separate, unsupported dual-name
        # branch; all existing single-name width fields remain checked.
        data.pop('_textMaxWidthForDual', None)
    elif mode == 'transform':
        # Current hierarchy traversal uses the live children list. Adding an
        # unrelated child does not change this node's coordinate transform.
        data = {key: value for key, value in data.items() if key != 'm_Children'}
    elif mode == 'gameobject':
        # Companion identities are checked as separate consumed objects.
        data = {key: data[key] for key in ('m_Name', 'm_IsActive', 'm_Layer')}
    return digest(canonical(data, obj.assets_file))


def condition_contract(obj):
    data = obj.read_typetree()
    for condition in data['_conditions']:
        flags = condition['_conditionType']
        # For the consumed non-Dual source rows, old all-bits/energy-only
        # conditions select the same row. New true-Dual rows need a new audit.
        condition['_conditionType'] = 0 if flags == 0 else 1 if flags & 1 else 2
        if condition.get('_isDual', 0):
            raise ValueError('Dual-energy font conditions are not recovered.')
        condition.pop('_isDual', None)
    return digest(canonical(data, obj.assets_file))


@lru_cache(maxsize=12)
def _verify(path, expected_hash, profile_digest):
    import UnityPy
    contract = json.loads(PROFILE.read_text(encoding='utf-8'))
    if digest(contract) != profile_digest:
        raise ValueError('Shared resource contract changed while being verified.')
    source = next((name for name in contract['bundles'] if Path(path).as_posix().endswith('/'+name)), None)
    if source is None:
        raise ValueError('No semantic contract exists for this source resource.')
    from .native_profile import unity_version
    # path points below <snapshot>/input/aladin-complete/output/<relative>.
    snapshot = Path(path).parents[len(Path(source).parts)+2]
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version(snapshot)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        environment = UnityPy.load(path)
    objects = {obj.path_id: obj for obj in environment.objects}
    bundle = contract['bundles'][source]
    if bundle.get('external_files'):
        actual = [external.name.lower() for external in environment.assets[0].externals]
        if actual[:len(bundle['external_files'])] != bundle['external_files']:
            raise ValueError('The font source external pointer identities changed.')
    for path_id, row in bundle['objects'].items():
        obj = objects.get(int(path_id))
        if obj is None or obj.type.name != row['type']:
            raise ValueError('A consumed %s source object is missing: %s' % (row['type'], path_id))
        actual = condition_contract(obj) if row['mode'] == 'condition' else object_contract(obj, row['mode'])
        if actual != row['sha256']:
            raise ValueError('A consumed source object changed: %s (%s)' % (path_id, row['type']))
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
        raise ValueError('The source resource changed during contract validation.')
    return {'contract': contract['id'], 'checked_objects': len(bundle['objects']),
            'source_sha256': expected_hash, 'additional_objects': len(objects)-len(bundle['objects'])}


def verify_resource(path, actual_hash):
    contract = json.loads(PROFILE.read_text(encoding='utf-8'))
    return _verify(str(Path(path).resolve()), actual_hash, digest(contract))
