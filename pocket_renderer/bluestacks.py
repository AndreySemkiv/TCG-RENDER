"""Read-only BlueStacks discovery and original-file snapshots.

No game launch, settings writes, root, screen capture, or Android file writes.
Only this package's APKs and public Sharin.Resources cache are copied.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .config import CODE_ROOT

PACKAGE = 'jp.pokemon.pokemontcgp'
ANDROID_RESOURCES = '/sdcard/Android/data/' + PACKAGE + '/files/Sharin.Resources'
DEFAULT_LOCATIONS = (r'C:\Program Files\BlueStacks_nxt', r'C:\ProgramData\BlueStacks_nxt')


class SourceError(ValueError):
    pass


def source_requirements():
    return ('BlueStacks aberto na instância que contém o Pokémon TCG Pocket. '
            'Em Configurações > Avançado, ative Android Debug Bridge (ADB). '
            'Conclua o download adicional e feche o jogo antes de importar, mantendo o BlueStacks aberto. Não é necessário root. '
            'O programa lê APKs e o cache público do Pocket; não captura a tela nem altera o jogo. '
            'A pasta padrão do programa é C:\\Program Files\\BlueStacks_nxt; '
            'as instâncias costumam estar em C:\\ProgramData\\BlueStacks_nxt.')


def _run(arguments, timeout=30):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        return subprocess.run([str(a) for a in arguments], capture_output=True, text=True,
                              encoding='utf8', errors='replace', timeout=timeout,
                              creationflags=flags)
    except subprocess.TimeoutExpired as error:
        raise SourceError('O ADB não respondeu no prazo. Verifique se a instância do BlueStacks está aberta.') from error
    except OSError as error:
        raise SourceError('Não foi possível executar o ADB: '+str(error)) from error


def _adb(adb, serial, *args, timeout=30, required=True):
    result = _run([adb, '-s', serial] + list(args), timeout)
    if required and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SourceError('ADB não conseguiu ler o Pocket: ' + detail[-1000:])
    return result.stdout.strip()


def _version(adb, serial):
    result = _adb(adb, serial, 'shell', 'dumpsys', 'package', PACKAGE)
    version = re.search(r'\bversionName=([^\s]+)', result)
    code = re.search(r'\bversionCode=(\d+)', result)
    if not version:
        raise SourceError('Pocket não encontrado nesta instância do BlueStacks.')
    return {'version':version[1], 'version_code':code[1] if code else None}


def _config(path):
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        match = re.fullmatch(r'\s*([\w.]+)="([^"]*)"\s*', line)
        if match:
            result[match[1]] = match[2]
    return result


def _prepared(path):
    return (path/'input/master-cache-decoded/MasterMemory.bytes').is_file()


def _registered_locations():
    """Read installation/data paths advertised by the local Windows installer."""
    try:
        import winreg
    except ImportError:
        return []
    result = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\BlueStacks_nxt', 0, winreg.KEY_READ | view) as key:
                    for name in ('InstallDir', 'UserDefinedDir', 'DataDir'):
                        try:
                            value, kind = winreg.QueryValueEx(key, name)
                            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and value:
                                path = Path(os.path.expandvars(value))
                                if path not in result:
                                    result.append(path)
                        except OSError:
                            pass
            except OSError:
                pass
    return result


def discover_sources(location=None):
    """Return detected source dictionaries; never alter emulator configuration.

    Explicit paths may be an HD-Adb installation, BlueStacks data directory,
    an exported raw Pocket directory, or an already prepared renderer workspace.
    ADB discovery connects only to loopback ports declared in local settings.
    """
    requested = Path(str(location).strip().strip('"')).expanduser().resolve() if location else None
    if requested:
        if not requested.exists():
            raise SourceError('A pasta informada não existe: ' + str(requested))
        if requested.is_file() and requested.suffix.lower() == '.apk':
            requested = requested.parent
        if requested.name == 'Sharin.Resources':
            requested = requested.parent
        elif requested.name in ('Default', 'aladin') and requested.parent.name == 'Sharin.Resources':
            requested = requested.parent.parent
        if ((requested/'snapshot.json').is_file() and (requested/'input/apk').is_dir()
                and requested.is_relative_to(CODE_ROOT.parent.resolve())):
            data = json.loads((requested/'snapshot.json').read_text(encoding='utf8'))
            return [dict(data,kind='snapshot',root=str(requested),
                         prepared=False)]
        if _prepared(requested):
            manifest = requested/'snapshot.json'
            data = json.loads(manifest.read_text(encoding='utf8')) if manifest.is_file() else {}
            return [{'kind':'prepared', 'root':str(requested), 'location':str(requested),
                     'version':data.get('version', 'unknown'), 'package':PACKAGE}]
        if ((requested/'input/apk').is_dir() or list(requested.glob('*.apk')) or
                (requested/'Sharin.Resources').is_dir()):
            return [{'kind':'folder', 'location':str(requested), 'package':PACKAGE,
                     'version':'unknown'}]
    defaults = [Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'BlueStacks_nxt',
                Path(os.environ.get('ProgramData', r'C:\ProgramData'))/'BlueStacks_nxt']
    roots = ([requested] if requested else []) + _registered_locations() + defaults
    # A supplied Engine/instance subfolder can locate its own configuration.
    # This is a bounded parent check, not a recursive scan of the user's drive.
    for root in list(roots):
        for parent in list(root.parents)[:2]:
            if (parent/'bluestacks.conf').is_file() and parent not in roots:
                roots.append(parent)
    adb_files = []
    configurations = []
    for root in roots:
        if root is None:
            continue
        if root.name.lower() == 'hd-adb.exe' and root.is_file():
            adb_files.append(root)
        candidate = root/'HD-Adb.exe'
        if candidate.is_file() and candidate not in adb_files:
            adb_files.append(candidate)
        conf = root/'bluestacks.conf'
        if conf.is_file() and conf not in configurations:
            configurations.append(conf)
    if not adb_files:
        fallback = shutil.which('adb')
        if fallback:
            adb_files.append(Path(fallback))
    if not adb_files:
        raise SourceError('HD-Adb.exe não encontrado. Informe a pasta do BlueStacks. ' + source_requirements())
    adb = adb_files[0].resolve()
    instances = {}
    disabled = False
    for conf in configurations:
        values = _config(conf)
        disabled |= values.get('bst.enable_adb_access') == '0'
        for key, value in values.items():
            match = re.fullmatch(r'bst\.instance\.([\w-]+)\.(?:status\.)?adb_port', key)
            if match and value.isdecimal() and 1 <= int(value) <= 65535:
                serial = '127.0.0.1:' + str(int(value))
                instances[serial] = {'instance':match[1],
                    'display_name':values.get('bst.instance.'+match[1]+'.display_name', match[1])}
    # 5555 is the conventional first instance; no remote hosts are contacted.
    instances.setdefault('127.0.0.1:5555', {'instance':'default', 'display_name':'BlueStacks padrão'})
    for serial in instances:
        _run([adb, 'connect', serial], timeout=10)
    devices = _run([adb, 'devices'], timeout=20)
    if devices.returncode:
        raise SourceError('Não foi possível consultar ADB: ' + devices.stderr.strip())
    result = []
    for line in devices.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != 'device':
            continue
        serial = parts[0]
        # Only local emulator sockets discovered from BlueStacks config/defaults.
        if serial not in instances:
            continue
        try:
            version = _version(adb, serial)
        except SourceError:
            continue
        result.append(dict(instances[serial], kind='adb', location=str(adb.parent),
                           adb=str(adb), serial=serial, package=PACKAGE, **version))
    if not result:
        reason = 'O ADB está desativado nas configurações. ' if disabled else ''
        raise SourceError(reason+'Nenhuma instância acessível com Pocket foi encontrada. '+source_requirements())
    return result


def _destination(destination):
    target = Path(destination).expanduser().resolve()
    workspace = CODE_ROOT.parent.resolve()
    if not target.is_relative_to(workspace) or target == workspace:
        raise SourceError('O novo snapshot deve ficar dentro da pasta de trabalho: '+str(workspace))
    if target.exists():
        raise SourceError('O destino já existe. Use uma nova pasta para preservar a importação anterior.')
    return target


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _manifest(root, data):
    path = root/'snapshot.json'
    temporary = path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')
    temporary.replace(path)


def _raw_folder(source, target, notify):
    original = Path(source['location']).resolve()
    if target.is_relative_to(original):
        raise SourceError('O snapshot não pode ficar dentro da pasta de origem.')
    apk_dir = original/'input/apk' if (original/'input/apk').is_dir() else original
    apks = list(apk_dir.glob('*.apk'))
    if not any(p.name == 'base.apk' for p in apks):
        raise SourceError('A exportação precisa conter base.apk e os APKs split do Pocket.')
    apk_before = {path.name:_hash(path) for path in apks}
    for index,path in enumerate(apks):
        notify('Copying APKs: %d/%d - %s'%(index,len(apks),path.name))
        shutil.copy2(path, target/'input/apk'/path.name)
    notify('Copying APKs: %d/%d'%(len(apks),len(apks)))
    choices = [original/'input/downloaded-after-update/Default', original/'input/downloaded/Default',
               original/'Sharin.Resources/Default', original/'Default']
    available = [p for p in choices if (p/'index').is_dir()]
    if len(available) > 1 and len({_local_index_digest(p/'index') for p in available}) > 1:
        raise SourceError('A exportação contém caches Default diferentes: ' + ', '.join(str(p) for p in available)
                          + '. Organize uma exportação com os APKs e somente o cache da mesma instalação.')
    cache = next(iter(available), None)
    if cache is None:
        raise SourceError('Não encontrei o cache Sharin.Resources/Default com index e blob na exportação.')
    cache_before = _local_index_digest(cache/'index')
    for name in ('blob','index'):
        if not (cache/name).is_dir():
            raise SourceError('Cache de recursos incomplete: '+name)
        notify('Copying cache Default/'+name)
        from .progress import copy_tree
        copy_tree(cache/name, target/'input/downloaded/Default'/name,notify)
    masters = [original/'input/master-cache-original', original/'Sharin.Resources/aladin', original/'aladin']
    available_masters = [p for p in masters if (p/'index').is_dir()]
    if len(available_masters) > 1 and len({_local_index_digest(p/'index') for p in available_masters}) > 1:
        raise SourceError('A exportação contém caches de banco aladin diferentes: ' + ', '.join(str(p) for p in available_masters)
                          + '. Mantenha somente o banco da instalação selecionada.')
    master = next(iter(available_masters), None)
    if master is None:
        raise SourceError('Não encontrei o cache de banco Sharin.Resources/aladin na exportação.')
    master_before = _local_index_digest(master/'index')
    for name in ('blob','index'):
        notify('Copying original database: '+name)
        from .progress import copy_tree
        copy_tree(master/name, target/'input/master-cache-original'/name,notify)
    if (_local_index_digest(cache/'index') != cache_before
            or _local_index_digest(master/'index') != master_before
            or _local_index_digest(target/'input/downloaded/Default/index') != cache_before
            or _local_index_digest(target/'input/master-cache-original/index') != master_before):
        raise SourceError('Os índices da exportação mudaram durante a cópia. Conclua o download e importe novamente.')
    if ({path.name:_hash(path) for path in apk_dir.glob('*.apk')} != apk_before
            or {path.name:_hash(path) for path in (target/'input/apk').glob('*.apk')} != apk_before):
        raise SourceError('Os APKs da exportação mudaram durante a cópia. Conclua a atualização e importe novamente.')
    return {'version':source.get('version', 'unknown')}


def _local_index_digest(path):
    rows = [(p.relative_to(path).as_posix(), _hash(p)) for p in sorted(path.rglob('*')) if p.is_file()]
    if not rows:
        raise SourceError('O índice de recursos está vazio: ' + str(path))
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode('utf8')).hexdigest()


def _android_index_digest(adb, serial):
    """Hash only public Pocket index files before/after the read-only copy.

    Content downloads do not necessarily change the APK's versionName.
    Filenames are sorted on the host; no Android file is written.
    """
    records = []
    for cache in ('Default', 'aladin'):
        parent = ANDROID_RESOURCES + '/' + cache + '/index'
        names = _adb(adb, serial, 'shell', 'find', parent, '-type', 'f').splitlines()
        if not names:
            raise SourceError('Índice do jogo ausente ou vazio: ' + cache + '. Conclua os downloads no Pocket.')
        for name in sorted(names):
            if not name.startswith(parent + '/') or not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or '..' in name.split('/'):
                raise SourceError('Nome inesperado no índice de recursos do Pocket.')
            result = _adb(adb, serial, 'shell', 'sha256sum', name)
            match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?' + re.escape(name), result)
            if not match:
                raise SourceError('O Android não confirmou o hash do índice: ' + name)
            records.append((name, match[1].lower()))
    return records


def _pull_with_progress(adb,serial,remote,destination,label,notify):
    from .progress import copy_bytes
    notify('Measuring transfer size: '+label)
    try:
        sizes=_adb(adb,serial,'shell','find',remote,'-type','f','-exec','stat','-c','%s','{}','+',
            required=False,timeout=60).splitlines()
    except SourceError:
        sizes=[]  # Size measurement is optional; it must never prevent import.
    total=sum(int(value) for value in sizes) if sizes and all(value.isdigit() for value in sizes) else None
    with copy_bytes(destination,total,label,notify):
        _adb(adb,serial,'pull',remote,str(destination),timeout=3600)


def import_source(source, destination, progress=None):
    """Copy immutable input files to a new snapshot; return its manifest.

    Prepared workspaces are returned unchanged. Raw import still requires
    importer.prepare_snapshot and materialization before card rendering.
    Failures preserve a diagnostic partial snapshot instead of deleting data.
    """
    notify = progress or (lambda message:None)
    if source.get('kind') == 'snapshot':
        root = Path(source['root']).resolve()
        if not root.is_relative_to(CODE_ROOT.parent.resolve()) or not (root/'snapshot.json').is_file():
            raise SourceError('Snapshot de trabalho inválido.')
        notify('Reusing previously copied snapshot files...')
        # A marker's existence cannot prove that its APKs, database and derived
        # assets still agree. prepare_import verifies receipts before reuse.
        return dict(source,root=str(root),prepared=False)
    if source.get('kind') == 'prepared':
        root = Path(source.get('root', source['location'])).resolve()
        if not _prepared(root):
            raise SourceError('O diretório preparado não contém MasterMemory.bytes.')
        return dict(source, root=str(root), prepared=True)
    root = _destination(destination)
    root.mkdir(parents=True)
    (root/'input/apk').mkdir(parents=True)
    data = dict(source, root=str(root), kind='snapshot', prepared=False,
                source={key:source[key] for key in ('kind','location','serial','package') if key in source},
                snapshot_id=uuid.uuid4().hex, status='copying',
                created_utc=datetime.now(timezone.utc).isoformat(),
                android_writes=False, game_executed=False, screen_capture=False)
    _manifest(root, data)
    try:
        if source.get('kind') == 'folder':
            data.update(_raw_folder(source, root, notify))
        elif source.get('kind') == 'adb':
            adb, serial = source['adb'], source['serial']
            initial = _version(adb, serial)
            notify('Checking download indexes before copying...')
            initial_indices = _android_index_digest(adb, serial)
            data.update(initial)
            packages = _adb(adb, serial, 'shell', 'pm', 'path', PACKAGE).splitlines()
            remote_apks = [line[len('package:'):] for line in packages if line.startswith('package:')]
            if not remote_apks:
                raise SourceError('Não foi possível localizar os APKs instalados do Pocket.')
            for apk_index,path in enumerate(remote_apks):
                notify('Copying APKs: %d/%d'%(apk_index,len(remote_apks)))
                name = path.rsplit('/', 1)[-1]
                if not path.startswith('/data/app/') or not re.fullmatch(r'[A-Za-z0-9_./=+@~-]+\.apk', path):
                    raise SourceError('Caminho APK inesperado retornado pelo Android.')
                if not re.fullmatch(r'[A-Za-z0-9_.-]+\.apk', name):
                    raise SourceError('Nome APK inesperado.')
                notify('Copying APKs: %d/%d - %s'%(apk_index,len(remote_apks),name))
                _pull_with_progress(adb,serial,path,root/'input/apk'/name,'Copying APK '+name,notify)
            notify('Copying APKs: %d/%d'%(len(remote_apks),len(remote_apks)))
            for folder, relative in (('Default','input/downloaded/Default'), ('aladin','input/master-cache-original')):
                notify('Copying game cache: '+folder)
                parent = root/relative
                parent.mkdir(parents=True, exist_ok=True)
                for name in ('blob','index'):
                    _pull_with_progress(adb,serial,ANDROID_RESOURCES+'/'+folder+'/'+name,
                        parent/name,'Copying game cache '+folder+'/'+name,notify)
            if _version(adb, serial) != initial:
                raise SourceError('O jogo foi atualizado durante a cópia. Importe novamente após concluir a atualização.')
            if _android_index_digest(adb, serial) != initial_indices:
                raise SourceError('Os recursos do jogo mudaram durante a cópia, mesmo sem mudança da versão APK. Conclua o download, feche o Pocket e importe novamente.')
            data['android_index_files'] = initial_indices
        else:
            raise SourceError('Tipo de origem desconhecido.')
        notify('Verifying copied files...')
        files = []
        paths=sorted(path for path in (root/'input').rglob('*') if path.is_file())
        for index,path in enumerate(paths):
            if index%200==0:notify('Verifying copied files: %d/%d'%(index,len(paths)))
            files.append({'file':path.relative_to(root).as_posix(), 'bytes':path.stat().st_size, 'sha256':_hash(path)})
        notify('Verifying copied files: %d/%d'%(len(paths),len(paths)))
        data.update(status='copied', files=files)
        _manifest(root, data)
        return data
    except Exception as error:
        data.update(status='failed', error=str(error))
        _manifest(root, data)
        raise
