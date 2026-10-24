"""Release updates for ZIP installations, using only the Python standard library."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile


REPOSITORY = 'AndreySemkiv/TCG-RENDER'
API_ROOT = 'https://api.github.com/repos/' + REPOSITORY
RELEASES_URL = 'https://github.com/' + REPOSITORY + '/releases'
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_EXTRACTED = 512 * 1024 * 1024
APP_DIRECTORIES = {'pocket_renderer', 'scripts', 'profiles', 'native', 'tools', 'docs'}
APP_FILES = {'launch.py', 'render_cards.py', 'requirements.txt', 'start.cmd', 'setup.cmd',
             'run.ps1', 'README.md', 'LICENSE', '.gitignore', '.gitattributes'}
PRESERVED = {'.git', '.venv', '.updates', '.update-state.json', '.update.lock', '__pycache__', 'tcg-data', 'pocket-extraction',
             'reports', 'renders', 'renders-metadata', 'render-cache', 'input', 'workspaces'}
REQUIRED_FILES = {'launch.py', 'render_cards.py', 'requirements.txt',
                  'pocket_renderer/__init__.py', 'pocket_renderer/menu.py',
                  'pocket_renderer/cli.py', 'pocket_renderer/updater.py',
                  'scripts/render_original_gpu.py', 'tools/aladump/aladump.exe',
                  'native/original-shader-decoder/shaderdecode.exe'}


class UpdateError(RuntimeError):
    pass


class RecoveryError(UpdateError):
    """Recovery was incomplete; do not start the app in an uncertain state."""


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple


def version_tuple(value):
    """Compare stable release versions numerically, never lexicographically."""
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', str(value))
    return tuple(map(int, match.groups())) if match else None


def latest_release(current_version, opener=urlopen):
    """Unreachable GitHub, no release, and current versions are all silent."""
    current = version_tuple(current_version)
    if current is None:
        return None
    try:
        request = Request(API_ROOT + '/releases/latest', headers={
            'Accept': 'application/vnd.github+json', 'User-Agent': 'TCG-Render/' + current_version})
        with opener(request, timeout=3) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
            return None
        tag = data.get('tag_name', '')
        version = version_tuple(tag)
        if version is not None and version > current:
            return Release(tag, version)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _download(release, destination, output, opener=urlopen):
    request = Request(API_ROOT + '/zipball/' + release.tag,
                      headers={'User-Agent': 'TCG-Render-Updater'})
    output('Downloading %s...' % release.tag)
    with opener(request, timeout=30) as response, destination.open('wb') as stream:
        total = int(response.headers.get('Content-Length') or 0)
        if total > MAX_DOWNLOAD:
            raise UpdateError('The release archive is too large.')
        received, last_percent = 0, -1
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_DOWNLOAD:
                raise UpdateError('The release archive is too large.')
            stream.write(chunk)
            if total:
                percent = min(100, received * 100 // total)
                if percent // 10 != last_percent // 10:
                    output('[%3d%%] Downloading update' % percent)
                    last_percent = percent
            elif received // (1024 * 1024) != (received-len(chunk)) // (1024 * 1024):
                output('Downloading update: %.1f MB received' % (received / (1024 * 1024)))
        if total and total != received:
            raise UpdateError('The release download is incomplete.')


def _safe_relative(name):
    parts = name.split('/')
    if (not name or '\\' in name or any(p in ('', '.', '..') for p in parts)
            or any(':' in p or '\x00' in p or p.endswith((' ', '.')) for p in parts)
            or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I) for p in parts)):
        raise UpdateError('The release contains an invalid path.')
    return PurePosixPath(name)


def _managed(relative):
    path = _safe_relative(relative)
    if any(part.casefold() in PRESERVED for part in path.parts):
        return False
    return (len(path.parts) == 1 and path.name in APP_FILES
            or len(path.parts) > 1 and path.parts[0] in APP_DIRECTORIES)


def _inside(root, relative):
    path = root.joinpath(*_safe_relative(relative).parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise UpdateError('An update path leaves the application folder.')
    # Do not follow a local symlink/junction, even if its target is in the app.
    for item in (path, *path.parents):
        if item == root:
            break
        attributes = getattr(item.lstat(), 'st_file_attributes', 0) if item.exists() else 0
        if item.is_symlink() or attributes & 0x400:
            raise UpdateError('An update path contains a link or junction.')
    return path


def _source_version(path):
    try:
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
                return version_tuple(ast.literal_eval(node.value))
    except (SyntaxError, ValueError, TypeError) as error:
        raise UpdateError('The release has an invalid application version file.') from error
    return None


def extract_release(archive, destination, release):
    """Read a GitHub source ZIP without trusting extractall or executing it."""
    files, seen, size, archive_root = [], set(), 0, None
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if len(members) > 20000:
            raise UpdateError('The release archive has too many files.')
        for member in members:
            # ZipInfo.filename may already normalize backslashes or truncate NULs.
            name = member.orig_filename.rstrip('/') if member.is_dir() else member.orig_filename
            path = _safe_relative(name)
            if archive_root is None:
                archive_root = path.parts[0]
            if path.parts[0] != archive_root:
                raise UpdateError('The release must have a single root folder.')
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise UpdateError('Links and special files are not allowed in releases.')
            if member.is_dir():
                continue
            if len(path.parts) < 2:
                raise UpdateError('Invalid source archive structure.')
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            normalized = relative.casefold()
            if normalized in seen:
                raise UpdateError('The release contains duplicate file paths.')
            seen.add(normalized)
            size += member.file_size
            if size > MAX_EXTRACTED:
                raise UpdateError('The extracted release is too large.')
            if not _managed(relative):
                continue
            target = _inside(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            files.append(relative)
    if not REQUIRED_FILES.issubset(files):
        raise UpdateError('The release is missing required application files.')
    if _source_version(destination/'pocket_renderer/__init__.py') != release.version:
        raise UpdateError('The release tag and application version do not match.')
    for relative in ('tools/aladump/aladump.exe', 'native/original-shader-decoder/shaderdecode.exe'):
        with (destination/relative).open('rb') as binary:
            if binary.read(2) != b'MZ':
                raise UpdateError('A bundled decoder is missing its executable data (possibly a Git LFS pointer).')
    return sorted(files)


@contextmanager
def _update_lock(directory):
    # Keep the lock outside .updates so cleanup stays protected by the same lock.
    stream = (directory/'.update.lock').open('a+b')
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise UpdateError('Another update is already running.') from error
        yield
    finally:
        stream.close()  # The OS releases the lock, including after a crash.


def _dependencies(root, output, log):
    python = root/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        import sys
        subprocess.run([sys.executable, '-m', 'venv', str(root/'.venv')], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    output('Updating Python dependencies; this may take a few minutes...')
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(root/'requirements.txt')], check=True, stdout=log, stderr=subprocess.STDOUT)


def install_release(root, release, output=print, downloader=_download, dependencies=_dependencies):
    """Stage, back up and replace application files; restore code on failure."""
    root = Path(root).resolve()
    if (root/'.git').exists():
        raise UpdateError('Use Git to update a Git checkout.')
    if version_tuple(release.tag) != release.version:
        raise UpdateError('Invalid release version.')
    updates = _inside(root, '.updates')
    with _update_lock(root):
        updates.mkdir(exist_ok=True)
        return _install_locked(root, updates, release, output, downloader, dependencies)


def _cleanup_updates(root):
    """Delete only this app's verified .updates directory, never its data folder."""
    updates = _inside(root, '.updates')
    if updates.exists():
        shutil.rmtree(updates)


def _install_locked(root, updates, release, output, downloader, dependencies):
    with tempfile.TemporaryDirectory(prefix='stage-', dir=updates) as temporary:
        installed = _source_version(root/'pocket_renderer/__init__.py')
        if installed is None or release.version <= installed:
            raise UpdateError('The release is not newer than the installed application.')
        staging = Path(temporary)
        archive = staging/'release.zip'
        downloader(release, archive, output)
        prepared = staging/'files'
        prepared.mkdir()
        files = extract_release(archive, prepared, release)
        # A tiny persistent manifest is needed to remove obsolete files next time.
        # Also accept manifests written by the earlier backup-retaining updater.
        state_file = _inside(root, '.update-state.json')
        previous_state_file = state_file if state_file.is_file() else _inside(root, '.updates/installed-files.json')
        old_state = previous_state_file.read_bytes() if previous_state_file.is_file() else None
        previous = json.loads(old_state)['files'] if old_state else []
        if not isinstance(previous, list) or any(not isinstance(p, str) or not _managed(p) for p in previous):
            raise UpdateError('The previous update file manifest is invalid.')
        removed = sorted(set(previous) - set(files))
        targets = {name: _inside(root, name) for name in sorted(set(files) | set(removed))}
        for target in targets.values():
            if target.exists() and not target.is_file():
                raise UpdateError('An application file conflicts with an existing folder.')
        backup = updates/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir()
        existed = []
        for name, target in targets.items():
            if target.is_file():
                saved = _inside(backup, name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                existed.append(name)
        (backup/'restore.json').write_text(json.dumps({'files': existed, 'added': sorted(set(files)-set(existed)),
            'previous_state': json.loads(old_state) if old_state else None}, indent=2), encoding='utf-8')
        old_requirements = (root/'requirements.txt').read_bytes()
        changed, dependency_attempted = [], False
        try:
            output('Installing %s...' % release.tag)
            for name in files:
                target = targets[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                # Record before replace so rollback also covers partial failures.
                changed.append(name)
                os.replace(prepared/name, target)
            for name in removed:
                if targets[name].exists():
                    changed.append(name)
                    targets[name].unlink()
            if old_requirements != (root/'requirements.txt').read_bytes():
                dependency_attempted = True
                with (backup/'dependencies.log').open('w', encoding='utf-8') as log:
                    dependencies(root, output, log)
            state = {'version': release.tag, 'files': files}
            pending = updates/'installed-files.partial'
            pending.write_text(json.dumps(state, indent=2), encoding='utf-8')
            os.replace(pending, state_file)
        except (Exception, KeyboardInterrupt) as error:
            failures = []
            for name in reversed(changed):
                try:
                    if name in existed:
                        shutil.copy2(backup/name, targets[name])
                    elif targets[name].exists():
                        targets[name].unlink()
                except OSError:
                    failures.append(name)
            if dependency_attempted and not failures:
                try:
                    with (backup/'dependency-recovery.log').open('w', encoding='utf-8') as log:
                        dependencies(root, output, log)
                except (Exception, KeyboardInterrupt):
                    failures.append('Python dependencies; run setup.cmd before starting again')
            if failures:
                raise RecoveryError('Update failed. Recovery needs attention: %s. Backup: %s' %
                                  (', '.join(failures), backup)) from error
            raise UpdateError('Update failed; previous application files restored. Backup: %s' % backup) from error
    # Commit and temporary-directory cleanup must finish before removing backups.
    # A cleanup problem must not roll back an otherwise successful installation.
    try:
        _cleanup_updates(root)
    except (OSError, UpdateError) as error:
        output('Updated to %s, but update files could not be removed: %s' % (release.tag, error))
        output('You can remove the .updates folder after closing the app.')
    else:
        output('Updated to %s. Update backups and temporary files removed.' % release.tag)
    return True


def offer_update(root, current_version, input_fn=input, output=print):
    release = latest_release(current_version)
    if release is None:
        return False
    output('\nUpdate available: %s (installed: %s).' % (release.tag, current_version))
    if (Path(root)/'.git').exists():
        output('This copy is managed by Git. Update with Git, or use a release ZIP for automatic updates.')
        output(RELEASES_URL)
        return False
    try:
        if input_fn('Update now? [y/N]: ').strip().casefold() not in ('y', 'yes'):
            return False
        return install_release(root, release, output)
    except RecoveryError as error:
        output('Could not recover the update: %s' % error)
        raise
    except (OSError, ValueError, UpdateError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        output('Could not update: %s' % error)
        output('Release downloads: ' + RELEASES_URL)
        return False
    except (KeyboardInterrupt, EOFError):
        output('\nUpdate cancelled.')
        return False
