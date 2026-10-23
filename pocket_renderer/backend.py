"""Two original-shader passes and inverse-alpha PNG export.

Source textures remain unchanged. Only the new render attachment is converted
from the game's inverse coverage/premultiplied RGB to conventional straight RGBA.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from PIL import Image
from .config import CODE_ROOT

def export_inverse_alpha(image: Image.Image) -> Image.Image:
    if image.mode != 'RGBA':
        raise ValueError('O render bruto deve conter RGBA e o alfa inverso original.')
    source = image.tobytes()
    pixels = bytearray(len(source))
    for index in range(0, len(source), 4):
        opacity = 255 - source[index + 3]
        pixels[index + 3] = opacity
        if opacity == 255:
            pixels[index:index+3] = source[index:index+3]
        elif opacity:
            for channel in range(3):
                pixels[index+channel] = min(255, (source[index+channel] * 255 + opacity//2)//opacity)
    return Image.frombytes('RGBA', image.size, bytes(pixels))

def write_transparent_png(raw: Path, destination: Path) -> dict:
    with Image.open(raw) as source:
        image = export_inverse_alpha(source)
    if image.getchannel('A').getextrema()[1] == 0:
        raise ValueError('A renderização ficou totalmente transparente; a saída não será aceita.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.partial')
    try:
        image.save(temporary, format='PNG')
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()
    return {'mode': 'RGBA', 'pixels': list(image.size),
            'alpha_extrema': list(image.getchannel('A').getextrema()),
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'background': 'transparent',
            'alpha_conversion': 'opacity=255-original_attachment_alpha; RGB unpremultiplied, zero RGB at opacity0',
            'source_textures_resized': False}

def render_code_digest():
    """Bind PNG reuse to preparation code, profiles and the actual GPU runner."""
    from .source_state import preparation_code_digest, digest_json
    return digest_json({'preparation_code_sha256':preparation_code_digest(),
        'gpu_runner_sha256':hashlib.sha256((CODE_ROOT/'scripts/render_original_gpu.py').read_bytes()).hexdigest(),
        'shader_decoder_sha256':hashlib.sha256((CODE_ROOT/'native/original-shader-decoder/shaderdecode.exe').read_bytes()).hexdigest()})


def verify_existing_png(path, metadata, card, language, quality, master_sha256, source_input_sha256=None,
                        render_code_sha256=None):
    """Never count an unrelated/stale existing image as a verified batch result."""
    if (metadata.get('status') != 'rendered' or metadata.get('card') != card
            or metadata.get('language') != language
            or metadata.get('source_master_sha256') != master_sha256):
        return False
    if source_input_sha256 is not None and metadata.get('source_input_sha256') != source_input_sha256:
        return False
    if render_code_sha256 is not None and metadata.get('render_code_sha256') != render_code_sha256:
        return False
    previous_quality = metadata.get('quality')
    if previous_quality not in ('large','native') or (quality != 'auto' and quality != previous_quality):
        return False
    result = metadata.get('result') or {}
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != result.get('sha256'):
        return False
    try:
        with Image.open(path) as image:
            return image.mode == 'RGBA' and list(image.size) == result.get('pixels')
    except (OSError,ValueError):
        return False


class VulkanBackend:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.renderer = CODE_ROOT / 'scripts/render_original_gpu.py'

    def render(self, scene_dir: Path, passes: dict, destination: Path,
               width: int = 700, height: int = 976) -> dict:
        work = Path(passes['consumer_manifest']).parent
        ui_size = passes.get('ui_size', [1468, 2048])
        native = bool(passes.get('native'))
        raw_ui = None if native else Path(passes.get('ui_raw_path', passes.get('ui_raw', work / 'ui-raw.png')))
        raw_card = work / 'card-raw-inverse-alpha.png'
        shader_manifest = passes.get('decoded_shaders') or self.root / 'original-render-pipeline/shaders/decode-manifest.json'
        common = [sys.executable, str(self.renderer), '--assets-dir', str(scene_dir),
                  '--decoded-shaders', str(shader_manifest), '--skip-base', '--skip-wgpu',
                  '--clear', '0', '0', '0', '1', '--rgba']
        if native:
            width,height=passes['output_size']
        jobs=[]
        if not native:
            jobs.append(('ui',passes['ui_manifest'],ui_size,raw_ui))
            for target in passes.get('ui_targets',[]):
                jobs.append(('ui-'+str(target['type']),target['manifest'],target['size'],target['raw_path']))
        jobs.append(('card',passes['consumer_manifest'],[width,height],raw_card))
        commands=[common+['--additional-draws',str(manifest),'--width',str(size[0]),
            '--height',str(size[1]),'--output',str(output)] for _,manifest,size,output in jobs]
        execution = []
        for index, command in enumerate(commands):
            start = time.monotonic()
            process = subprocess.run(command, capture_output=True, text=True,
                                     encoding='utf8', errors='replace', cwd=self.root)
            record = {'stage': jobs[index][0], 'arguments': command,
                      'seconds': round(time.monotonic()-start, 3), 'returncode': process.returncode}
            execution.append(record)
            (work / ('gpu-' + record['stage'] + '.log')).write_text(
                process.stdout + '\n' + process.stderr, encoding='utf8')
            if process.returncode:
                raise RuntimeError('Falha no passe ' + record['stage'] + '. Consulte ' + str(work / ('gpu-' + record['stage'] + '.log')))
        result = write_transparent_png(raw_card, destination)
        result['execution'] = execution
        result['profile'] = passes.get('profile','original-large-dynamic')
        result['quality'] = 'native' if native else 'large'
        result['limits'] = passes.get('limits',[])
        return result
