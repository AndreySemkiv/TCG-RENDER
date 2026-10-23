"""Prepare newly copied Pocket resources without borrowing an older database.

Aladin assets use the key asset extracted from the selected APK. Protected
metadata currently has one audited native-loader profile. A different native
binary is rejected rather than applying unverified offsets to another build.
"""
import ctypes
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import warnings
import zipfile

from .bluestacks import SourceError, _hash
from .config import CODE_ROOT

NATIVE_PROFILES = {
    'eefba3938aa7bc1751135abb56b82f12d46c290c0a598b59e459d6f1a15c4d5c': {
        'id':'pocket-1.7.5-arm64-metadata31', 'unity_version':'6000.0.78f1',
        'metadata_master_key_offset':0x1211358,
        'key_va':0x81846f5, 'prefix_size_va':0x818268c, 'prefix_va':0x8182690,
    }
}


@contextmanager
def _snapshot_lock(root):
    """OS-held lock: another import is refused; process exit releases the lock."""
    path = root/'.import.lock'
    with path.open('a+b') as lock:
        lock.seek(0,2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise SourceError('Já existe uma importação em andamento neste snapshot. Aguarde a conclusão.') from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def _load_script(name):
    directory = CODE_ROOT/'scripts'
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('_pocket_import_'+name, directory/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_zip_target(root, name):
    normalized = PurePosixPath(name)
    if normalized.is_absolute() or '..' in normalized.parts or '\\' in name:
        raise SourceError('Caminho inválido dentro do APK.')
    destination = root.joinpath(*normalized.parts).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise SourceError('Caminho APK fora do diretório de importação.')
    return destination


def _extract_apks(root, notify):
    unity = root/'input/unity-data'
    assets = root/'input/aladin-complete'
    native = root/'input/native-code'
    for folder in (unity, assets, native):
        folder.mkdir(parents=True, exist_ok=True)
    apks = sorted((root/'input/apk').glob('*.apk'))
    if not any(path.name == 'base.apk' for path in apks):
        raise SourceError('base.apk não encontrado no snapshot.')
    for path in apks:
        notify('Preparing contents of '+path.name)
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                name = entry.filename
                target = None
                if name.startswith('assets/bin/Data/'):
                    target = _safe_zip_target(unity, name[len('assets/bin/Data/'):])
                elif name.startswith(('assets/assetpack/blob/', 'assets/assetpack/index/')):
                    target = _safe_zip_target(assets, name[len('assets/assetpack/'):])
                elif name in ('lib/arm64-v8a/libil2cpp.so', 'lib/arm64-v8a/libunity.so'):
                    target = native/name.rsplit('/', 1)[-1]
                if target is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open('wb') as destination:
                        shutil.copyfileobj(source, destination)
    downloaded = root/'input/downloaded/Default'
    for name in ('blob', 'index'):
        if not (downloaded/name).is_dir():
            raise SourceError('Download adicional incomplete: falta Default/'+name)
        shutil.copytree(downloaded/name, assets/name, dirs_exist_ok=True)
    return assets


def _aes_ecb_windows(key, data):
    """Windows BCrypt native AES, used only on original loader counter blocks."""
    library = ctypes.WinDLL('bcrypt')
    handle = ctypes.c_void_p()
    secret = ctypes.c_void_p()
    def check(status):
        if status:
            raise SourceError('Falha BCrypt AES: 0x%08X' % (status & 0xffffffff))
    check(library.BCryptOpenAlgorithmProvider(ctypes.byref(handle), ctypes.c_wchar_p('AES'), None, 0))
    try:
        mode = ctypes.create_unicode_buffer('ChainingModeECB')
        check(library.BCryptSetProperty(handle, ctypes.c_wchar_p('ChainingMode'), mode,
                                        ctypes.sizeof(mode), 0))
        length, written = ctypes.c_ulong(), ctypes.c_ulong()
        check(library.BCryptGetProperty(handle, ctypes.c_wchar_p('ObjectLength'), ctypes.byref(length),
                                        ctypes.sizeof(length), ctypes.byref(written), 0))
        object_buffer = ctypes.create_string_buffer(length.value)
        key_buffer = ctypes.create_string_buffer(key)
        check(library.BCryptGenerateSymmetricKey(handle, ctypes.byref(secret), object_buffer,
                length.value, key_buffer, len(key), 0))
        source = ctypes.create_string_buffer(data)
        output = ctypes.create_string_buffer(len(data))
        check(library.BCryptEncrypt(secret, source, len(data), None, None, 0,
                                    output, len(data), ctypes.byref(written), 0))
        if written.value != len(data):
            raise SourceError('Tamanho inesperado retornado pelo AES nativo.')
        return output.raw[:written.value]
    finally:
        if secret.value:
            library.BCryptDestroyKey(secret)
        library.BCryptCloseAlgorithmProvider(handle, 0)


def _recover_metadata(root, profile, notify):
    inspector = _load_script('inspect_original_metadata')
    elf = inspector.ELF(root/'input/native-code/libil2cpp.so')
    source = root/'input/unity-data/Managed/Metadata/global-metadata.dat'
    protected = source.read_bytes()
    if os.name == 'nt':
        if _aes_ecb_windows(bytes(range(16)), bytes.fromhex('00112233445566778899aabbccddeeff')).hex() != '69c4e0d86a7b0430d8cdb78070b4c55a':
            raise SourceError('Teste de referência AES não passou.')
        n = struct.unpack_from('<I', elf.data, elf.off(profile['prefix_size_va']))[0]
        pos = elf.off(profile['prefix_va'])
        encrypted = elf.data[pos+16:pos+16+n]+protected[4:]
        pos = elf.off(profile['key_va'])
        seed = struct.pack('<Q', 0x2d79bbfe57c87ce8)
        key = bytes(value ^ seed[index%8] for index,value in enumerate(elf.data[pos:pos+16]))
        counters = b''.join(b'\0'*8 + index.to_bytes(8, 'big') for index in range(1, (len(encrypted)+15)//16+1))
        stream = _aes_ecb_windows(key, counters)[:len(encrypted)]
        recovered = (int.from_bytes(encrypted, 'little') ^ int.from_bytes(stream, 'little')).to_bytes(len(encrypted), 'little')
    else:
        notify('Decoding metadata with the verified native profile...')
        recovered, _ = inspector.recover_prefix(elf, protected, len(protected)-4+8192)
    if struct.unpack_from('<II', recovered) != (0xfab11baf,31):
        raise SourceError('Os metadados não correspondem ao perfil IL2CPP auditado.')
    values = struct.unpack_from('<64I', recovered)
    if not all(offset+size <= len(recovered) for offset,size in zip(values[2::2], values[3::2])):
        raise SourceError('Limites inválidos nas tabelas dos metadados.')
    out = root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(recovered)
    return {'source_sha256':_hash(source), 'decoded_sha256':_hash(out), 'bytes':len(recovered)}


def _decode_master(root, profile):
    decoder = _load_script('decode_original_master')
    metadata = (root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat').read_bytes()
    offset = profile['metadata_master_key_offset']
    key = metadata[offset:offset+32]
    if len(key) != 32:
        raise SourceError('Chave do banco fora dos metadados do perfil.')
    src = root/'input/master-cache-original'
    output = root/'input/master-cache-decoded'
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index in sorted((src/'index').rglob('*.aladin')):
        for entry in decoder.index_entries(index.read_bytes()):
            if entry['key_id'] != '075b2dfa1a1e4302':
                raise SourceError('A chave do banco mudou; este perfil precisa de atualização.')
            blob = src/'blob'/entry['blob_hash'][:2]/(entry['blob_hash']+'.aladin')
            raw = blob.read_bytes()
            if len(raw) != entry['blob_size'] or decoder.xxh64(raw) != int(entry['blob_hash'],16):
                raise SourceError('Hash do cache do banco não confere. Aguarde o download e importe novamente.')
            decoded, settings = decoder.chacha_original(raw,key,int(entry['content_hash'],16)) if entry['crypted'] else (raw,{})
            if len(decoded) != entry['content_size'] or decoder.xxh64(decoded) != int(entry['content_hash'],16):
                raise SourceError('A decodificação do banco não passou na verificação de integridade.')
            target = _safe_zip_target(output, entry['address'])
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(decoded)
            records.append(dict(entry, **settings, source_blob_sha256=_hash(blob),
                    output=str(target.relative_to(root)), sha256=_hash(target)))
    if not any(record['address'] == 'MasterMemory.bytes' for record in records):
        raise SourceError('MasterMemory.bytes não foi encontrado no banco instalado.')
    (output/'PROVENANCE.json').write_text(json.dumps(records,indent=2),encoding='utf8')
    # Parse the selected source's database now; never substitute a previous copy.
    from .catalog import Catalog
    catalog = Catalog(root)
    return {'sha256':catalog.master_sha256, 'collections':len(catalog.table('Expansion'))}


def _decode_aladin(root, assets, executable, notify):
    """Use a short relative input and reject logged failures even at exit0.

    The shipped decoder concatenates forward slashes to its input path, so a
    Windows extended-path prefix is not accepted. Running in the source folder
    with input '.' avoids an excessively long absolute input prefix.
    """
    notify('Decoding original resource packages...')
    log_path = root/'aladin-import.log'
    from .progress import decoder_progress
    with log_path.open('w',encoding='utf8') as log, decoder_progress(log_path,notify):
        process = subprocess.run([str(executable.resolve()),'.'], cwd=assets.resolve(), stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    contents = log_path.read_text(encoding='utf8',errors='replace')
    errors = re.findall(r'(?im)(?:file_write:\s*cannot\s+create|file_open:\s*cannot\s+open|error:|failed to (?:write|create|read|decrypt))',contents)
    if process.returncode or errors:
        hint = ' No Windows, tente uma pasta de projeto mais curta se o log mostrar file_write: cannot create.' if 'file_write:' in contents else ''
        raise SourceError('A extração Aladin falhou (%d erros no log); consulte %s. Os arquivos originais foram preservados.%s' % (len(errors),log_path,hint))
    progress = [(int(done),int(total)) for done,total in re.findall(r'\[(\d+)/(\d+)\]',contents)]
    if not progress or progress[-1][0] != progress[-1][1]:
        raise SourceError('O log não confirma a extração Aladin completa. Consulte '+str(log_path))
    output = assets/'output'
    notify('Checking extraction completion and resource files...')
    count = sum(len(files) for _,_,files in os.walk(output))
    if count == 0 or not (output/'Common/Font_bundles').is_file():
        raise SourceError('A extração não produziu os recursos necessários para renderizar.')
    return {'index_entries_processed':progress[-1][1], 'files_written':count,
            'logged_failures':len(errors), 'log_sha256':_hash(log_path), 'relative_decoder_workdir':True}


def _prepare_snapshot_unlocked(root, tools_root, progress=None, input_digest=None):
    """Decode selected APK/assets/master; caller materializes render resources.

    This does not certify complete rendering compatibility or call a GPU.
    ``tools_root`` is the existing dependency directory containing aladump/.
    """
    notify = progress or (lambda message:None)
    root = Path(root).resolve()
    if not root.is_relative_to(CODE_ROOT.parent.resolve()):
        raise SourceError('Snapshot fora da pasta de trabalho.')
    if not (root/'snapshot.json').is_file():
        raise SourceError('Snapshot sem manifesto de importação.')
    from .source_state import verify_snapshot_inputs, raw_preparation_code_digest
    input_digest = input_digest or verify_snapshot_inputs(root, notify)
    assets = _extract_apks(root, notify)
    native = root/'input/native-code/libil2cpp.so'
    if not native.is_file():
        raise SourceError('Esta importação precisa do split ARM64 do Pocket (libil2cpp.so).')
    if not (native.parent/'libunity.so').is_file():
        raise SourceError('O split ARM64 está incomplete: libunity.so não foi encontrado nos APKs selecionados.')
    profile = NATIVE_PROFILES.get(_hash(native))
    if not profile:
        raise SourceError('Versão nativa ainda não reconhecida. Os arquivos foram preservados, mas a preparação precisa de um perfil compatível; os dados antigos não serão usados como substituição.')
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = profile['unity_version']
    notify('Reading the selected APK resource key...')
    found = False
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        env = UnityPy.load(str(root/'input/unity-data'))
    for obj in env.objects:
        if obj.type.name == 'TextAsset':
            value = obj.read()
            if value.m_Name == 'src_cph_1001':
                data = value.m_Script.encode('utf8','surrogateescape') if isinstance(value.m_Script,str) else bytes(value.m_Script)
                (assets/'src_cph_1001').write_bytes(data)
                found = True
    if not found:
        raise SourceError('Chave src_cph_1001 não encontrada no APK importado.')
    executable = Path(tools_root)/'aladump/aladump.exe'
    if not executable.is_file():
        raise SourceError('Dependência aladump.exe não encontrada em '+str(executable))
    asset_report = _decode_aladin(root,assets,executable,notify)
    notify('Decoding metadata and the selected installation card database...')
    metadata = _recover_metadata(root,profile,notify)
    master = _decode_master(root,profile)
    result = {'schema':2, 'complete':True, 'source_input_sha256':input_digest,
              'raw_preparation_code_sha256':raw_preparation_code_digest(),
              'root':str(root),'profile':profile['id'],'unity_version':profile['unity_version'],
              'raw_prepared':True,'prepared':False,'metadata':metadata,'master':master,
              'assets':asset_report,
              'native_files':{path.name:_hash(path) for path in (root/'input/native-code').glob('*.so')},
              'limits':['Audited native loader profile only; changed native binaries need an updated profile.',
                        'Render-resource materialization and per-layout compatibility checks are still required.']}
    marker = root/'raw-preparation.json'
    temporary = marker.with_suffix('.json.partial')
    temporary.write_text(json.dumps(result,indent=2),encoding='utf8')
    temporary.replace(marker)
    return result


def prepare_snapshot(root,tools_root,progress=None):
    """Prepare one snapshot exclusively, invalidating stale readiness first."""
    root = Path(root).resolve()
    if not root.is_relative_to(CODE_ROOT.parent.resolve()) or not (root/'snapshot.json').is_file():
        raise SourceError('Snapshot inválido ou fora da pasta de trabalho.')
    with _snapshot_lock(root):
        from .source_state import clear_source_caches, invalidate_readiness
        invalidate_readiness(root, raw=True)
        clear_source_caches()
        return _prepare_snapshot_unlocked(root,tools_root,progress)
