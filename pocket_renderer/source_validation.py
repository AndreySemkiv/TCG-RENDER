"""Gate imported rendering dependencies against source-backed known contracts.

New collection/master contents are deliberately not pinned. Shared shaders,
fonts and the UI/configuration consumed by the current renderer must match a
profile that was actually inspected. This prevents an imported release from
silently receiving shaders or font atlases decoded from a different release.
Byte-identical source dependencies do not certify complete Unity runtime 1:1.
"""
from pathlib import Path
import hashlib
import json

from .catalog import project_root
from .config import CODE_ROOT

DEFAULT_PROFILE = CODE_ROOT / 'profiles/known-source-v1.json'


class SourceCompatibilityError(ValueError):
    def __init__(self, report):
        self.report = report
        problems = report['problems']
        message = '; '.join('%s: %s' % (p['reason'], p['file']) for p in problems[:5])
        if len(problems) > 5:
            message += '; +%d problemas' % (len(problems)-5)
        super().__init__('A fonte não é compatível com o perfil de renderização: ' + message)


def load_profile(profile_path=None):
    path = Path(profile_path or DEFAULT_PROFILE).resolve()
    profile = json.loads(path.read_text(encoding='utf8'))
    if profile.get('schema') != 1 or not profile.get('bundles'):
        raise ValueError('Perfil de origem inválido: ' + str(path))
    for section in ('bundles', 'derived', 'reference_artifacts'):
        for name, digest in profile.get(section, {}).items():
            path_parts = Path(name.replace('\\', '/'))
            if path_parts.is_absolute() or '..' in path_parts.parts or ':' in name:
                raise ValueError('Caminho inválido no perfil: ' + name)
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('SHA-256 inválido no perfil: ' + name)
    return profile


def _check(base, values, problems, category):
    for name, expected in values.items():
        path = base/name
        if not path.is_file():
            problems.append({'category':category, 'file':name, 'reason':'arquivo ausente'})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append({'category':category, 'file':name,
                             'reason':'hash diferente do recurso validado',
                             'expected_sha256':expected, 'actual_sha256':actual})


def _finish(report, strict):
    report['supported'] = not report['problems']
    if strict and not report['supported']:
        raise SourceCompatibilityError(report)
    return report


def validate_profile_source(root, profile_path=None, strict=True):
    """Check original decoded bundles before preparing/copying derived data."""
    root = project_root(root)
    profile = load_profile(profile_path)
    problems = []
    _check(root/'input/aladin-complete/output', profile['bundles'], problems, 'original-bundle')
    report = {'profile':profile['profile'], 'profile_path':str(Path(profile_path or DEFAULT_PROFILE).resolve()),
              'root':str(root), 'checked_bundles':len(profile['bundles']), 'checked_derived':0,
              'master_hash_pinned':False, 'problems':problems, 'limits':profile.get('limitations', [])}
    return _finish(report, strict)


def validate_prepared_source(root, profile_path=None, strict=True):
    """Also check lossless shader stages and native font bytes before GPU use.

    JSON descriptions may be regenerated from identical source bundles; their
    reference hashes are retained for audit rather than forced to match text
    formatting, path separators or the old card-specific template contents.
    """
    root = project_root(root)
    profile = load_profile(profile_path)
    report = validate_profile_source(root, profile_path, strict=False)
    _check(root, profile.get('derived', {}), report['problems'], 'derived-original-bytes')
    report['checked_derived'] = len(profile.get('derived', {}))
    return _finish(report, strict)
