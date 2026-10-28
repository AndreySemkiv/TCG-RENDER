"""Losslessly decode Vulkan stages from the selected original Unity bundles."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import warnings

from .config import CODE_ROOT


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spirv_entries(raw):
    if len(raw) < 20 or len(raw) % 4:
        raise ValueError("Invalid original SPIR-V length")
    words = struct.unpack('<%dI' % (len(raw)//4), raw)
    if words[0] != 0x07230203:
        raise ValueError("Invalid original SPIR-V magic")
    cursor, entries = 5, []
    while cursor < len(words):
        count, opcode = words[cursor] >> 16, words[cursor] & 65535
        if not count or cursor+count > len(words):
            raise ValueError("Invalid original SPIR-V framing")
        if opcode == 15:
            entries.append({'execution_model':words[cursor+1], 'entry_id':words[cursor+2]})
        cursor += count
    if len(entries) != 1 or entries[0]['execution_model'] not in (0,4):
        raise ValueError("Unsupported original shader execution model")
    return entries


def extract_shader(root, relative, raw_dir, decoded_dir, prefix=None, unity_version=None):
    import UnityPy
    if unity_version is None:
        from .native_profile import unity_version as source_unity_version
        unity_version = source_unity_version(root)
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    from UnityPy.helpers import CompressionHelper
    from UnityPy.streams import EndianBinaryReader
    from UnityPy.export.ShaderConverter import ShaderSubProgram
    root, raw_dir, decoded_dir = Path(root), Path(raw_dir), Path(decoded_dir)
    executable = CODE_ROOT/'native/original-shader-decoder/shaderdecode.exe'
    if not executable.is_file():
        raise ValueError('Decodificador SMOL-V ausente: '+str(executable))
    path = root/'input/aladin-complete/output'/relative
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        env = UnityPy.load(str(path))
    obj = next(o for o in env.objects if o.type.name == 'Shader')
    shader = obj.read()
    parsed = obj.read_typetree()['m_ParsedForm']
    parameter_ids, program_ids = set(), set()
    for sub in parsed['m_SubShaders']:
        for shader_pass in sub['m_Passes']:
            for stage in ('progVertex', 'progFragment'):
                data = shader_pass[stage]
                for group in data['m_ParameterBlobIndices']:
                    parameter_ids.update(group if isinstance(group,list) else [group])
                for group in data['m_PlayerSubPrograms']:
                    program_ids.update(program['m_BlobIndex'] for program in group)
    name = prefix or obj.assets_file.name.lower()+'__'+str(obj.path_id)
    records = []
    raw_dir.mkdir(parents=True,exist_ok=True)
    decoded_dir.mkdir(parents=True,exist_ok=True)
    for platform, offsets, lengths, sizes in zip(shader.platforms, shader.offsets, shader.compressedLengths, shader.decompressedLengths):
        if int(platform) != 18:
            continue
        if isinstance(offsets,int):
            offsets, lengths, sizes = [offsets], [lengths], [sizes]
        for block_index, (offset,length,size) in enumerate(zip(offsets,lengths,sizes)):
            block = CompressionHelper.decompress_lz4(bytes(shader.compressedBlob)[offset:offset+length],size)
            count = struct.unpack_from('<I',block)[0]
            if count > (len(block)-4)//12:
                raise ValueError('Shader program index exceeds original container')
            for index in sorted(program_ids-parameter_ids):
                if index >= count:
                    raise ValueError('Shader program entry is missing')
                start, length, _ = struct.unpack_from('<3I',block,4+index*12)
                if start+length > len(block):
                    raise ValueError('Shader program exceeds original container')
                program = ShaderSubProgram(EndianBinaryReader(block[start:start+length],endian='<'))
                code = bytes(program.m_ProgramCode)
                starts = [i for i in range(len(code)) if code.startswith(b'LOMS',i)]
                for stage_index,(left,right) in enumerate(zip(starts,starts[1:]+[len(code)])):
                    stem = '%s.platform18.block%d.program%d.smolv%d' % (name,block_index,index,stage_index)
                    raw = raw_dir/(stem+'.bin')
                    raw.write_bytes(code[left:right])
                    target = decoded_dir/stem
                    process = subprocess.run([str(executable),str(raw),str(target)],capture_output=True,
                        text=True,encoding='utf8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if process.returncode:
                        raise ValueError('Falha ao decodificar shader original: '+stem+' '+process.stderr)
                    spv = target.with_suffix(target.suffix+'.spv')
                    entries = spirv_entries(spv.read_bytes())
                    records.append({'shader_bundle':relative, 'source_bundle_sha256':digest(path),
                        'source_smolv':raw.relative_to(root).as_posix(),'source_sha256':digest(raw),
                        'spirv_file':spv.relative_to(root).as_posix(),'spirv_sha256':digest(spv),
                        'spirv_bytes':spv.stat().st_size,'entry_points':entries,'returncode':0,
                        'decoder_output':process.stdout.strip(),'error':'',
                        'validation':'Original SMOL-V lossless decode; original SPIR-V framing checked, shader math unchanged.'})
    if not records:
        raise ValueError('No supported original Vulkan stages: '+relative)
    return records


def write_manifest(path, records):
    Path(path).write_text(json.dumps({'source':'Selected installation original SMOL-V decoded losslessly to SPIR-V',
        'decoded_programs':records},indent=2),encoding='utf8')
