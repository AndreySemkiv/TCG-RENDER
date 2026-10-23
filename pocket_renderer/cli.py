"""User-facing batch CLI. Preparation/dry-run never opens a Vulkan device."""
from __future__ import annotations
import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import uuid

from . import __version__
from .config import PROJECT_ROOT, LANGUAGES, RenderConfig, normalize_language

class UnsupportedJob(ValueError):
    pass

def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(data,indent=2,ensure_ascii=False,default=str),encoding='utf8')
    temporary.replace(path)

def _key(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',value):
        raise ValueError('Identificador de carta inválido para um arquivo: '+value)
    return value

def _prepare_native_job(config,card,language,reason=None):
    from .native_card import prepare_native_card
    work=config.cache/'native-jobs'/normalize_language(language)/_key(card['id'])
    work.mkdir(parents=True,exist_ok=True)
    passes=prepare_native_card(config.root,card,language,work)
    validate_manifests(passes)
    prepared={'assets_dir':passes['assets_dir'],'passes':passes,'work':work,'font_generation':[],
              'quality':'native','fallback_reason':reason,'native_size':passes['output_size']}
    _write_json(work/'prepared-job.json',dict(prepared,card=card['id'],language=language,new_card_rendered=False))
    return prepared


def prepare_job(config,card,language):
    if config.quality == 'native':
        return _prepare_native_job(config,card,language)
    try:
        return _prepare_large_job(config,card,language)
    except (UnsupportedJob,ValueError) as error:
        from .source_validation import SourceCompatibilityError
        if config.quality != 'auto' or isinstance(error,SourceCompatibilityError):
            raise
        prepared=_prepare_native_job(config,card,language,str(error))
        prepared['fallback_details']=getattr(error,'details',None)
        return prepared


def _prepare_large_job(config, card, language):
    from .assets import prepare_assets
    from .localization import localize_card, glyph_coverage
    from .layout import bind_layout
    from .font_engine import ensure_glyphs
    from .compiler import prepare_passes
    from .source_validation import validate_prepared_source
    if not card.get('supported',False):
        raise UnsupportedJob('; '.join(card.get('unsupported_reasons',[])))
    validate_prepared_source(config.root)
    language = normalize_language(language)
    asset_key = card['asset_id'] if re.fullmatch(r'[A-Za-z0-9_.-]+',card['asset_id']) else 'asset-'+hashlib.sha256(card['asset_id'].encode('utf8')).hexdigest()[:24]
    assets_dir = config.cache/'assets'/asset_key
    prepared = prepare_assets(config.root,card,assets_dir,generalized=bool(card.get('trainer')))
    if not prepared['supported'] and not prepared.get('error'):
        prepared=prepare_assets(config.root,card,assets_dir,generalized=True)
    if not prepared['supported']:
        message='; '.join(prepared.get('reasons',[]))
        if prepared.get('error'): raise RuntimeError(message+': '+prepared['error'])
        raise UnsupportedJob(message)
    work = config.cache/'jobs'/language/_key(card['id'])
    work.mkdir(parents=True,exist_ok=True)
    if card.get('trainer'):
        from .trainer_ui import localize_trainer,bind_trainer_layout
        ui=localize_trainer(config.root,card,language,output_dir=work)
    else:ui = localize_card(config.root,card,language,output_dir=work)
    fonts = ensure_glyphs(config.root,ui['font_metrics'],ui,work,strict=config.stored_glyphs_only)
    # A dynamically populated character can affect preferred-width calculations.
    basic = [v for v in ui['sprite_bindings'] if v['id']=='basic_badge']
    if card.get('trainer'):bind_trainer_layout(ui,card,fonts)
    else:bind_layout(ui,card,fonts)
    ui['sprite_bindings'][0:0] = basic
    ui['font_metrics'] = fonts
    ui['glyph_coverage'] = glyph_coverage(ui['texts'],fonts)
    generated = [font['dynamic_generation'] for font in fonts.values() if font.get('dynamic_generation')]
    if generated:
        ui['limitations'].append('Only missing glyphs are generated from original font outlines by FreeType; the Unity dynamic FontEngine SDF algorithm is not executed byte-for-byte.')
    _write_json(work/'ui-spec.json',ui)
    passes = prepare_passes(config.root,assets_dir,ui,work/'passes',fonts=fonts)
    # GPU-ready manifests must refer to existing immutable inputs, except for the
    # declared intermediate render target that the first pass will produce.
    validate_manifests(passes)
    _write_json(work/'prepared-job.json',{'card':card['id'],'language':language,'assets':prepared,
        'passes':passes,'font_generation':generated,'background':'transparent','new_card_rendered':False})
    return {'assets_dir':assets_dir,'passes':passes,'work':work,'font_generation':generated,'quality':'large'}

def validate_manifests(passes):
    allowed_future = {Path(passes['ui_raw_path']).resolve()} if passes.get('ui_raw_path') else set()
    target_manifests=[]
    for target in passes.get('ui_targets',[]):
        allowed_future.add(Path(target['raw_path']).resolve())
        target_manifests.append(target['manifest'])
    manifests=[passes[key] for key in ('ui_manifest','consumer_manifest') if passes.get(key)]+target_manifests
    for manifest in manifests:
        path=Path(manifest)
        data=json.loads(path.read_text(encoding='utf8'))
        if not data.get('draws'):
            # Only an explicitly declared secondary UI target can be empty.
            # Its original layer can legitimately have no active children
            # (e.g. non-EX IM's Holo), and its consumer still needs the clear.
            targets=[t for t in passes.get('ui_targets',[]) if Path(t['manifest']).resolve()==path.resolve()]
            target_info=data.get('provenance',{}).get('dynamic_ui_target',{})
            clear_only=(len(targets)==1 and targets[0].get('clear_only') is True
                and data.get('clear_only') is True and data.get('draws')==[]
                and data.get('clear_color_rgba')==[0,0,0,1]
                and target_info.get('type')==targets[0]['type'] and target_info.get('layer')==18)
            if not clear_only:raise ValueError('Manifest sem passes: '+str(path))
        for draw in data['draws']:
            values=[draw['vertices']['file'],draw['indices']['file']]+list(draw['shaders'].values())
            for binding in draw['bindings']:
                if 'file' in binding: values.append(binding['file'])
                values.extend(binding.get('faces',[]))
                if binding.get('type')=='combined_image_sampler_cube' and binding.get('file'):
                    cube_path=Path(binding['file'])
                    if not cube_path.is_absolute(): cube_path=path.parent/cube_path
                    cube=json.loads(cube_path.read_text(encoding='utf8'))
                    values.extend(str(cube_path.parent/v) if not Path(v).is_absolute() else v for v in cube['faces'])
            for value in values:
                candidate=Path(value)
                if not candidate.is_absolute(): candidate=path.parent/candidate
                if candidate.resolve() not in allowed_future and not candidate.is_file():
                    raise ValueError('Recurso ausente no manifest: '+str(candidate))

def _parser():
    parser=argparse.ArgumentParser(description='Batch renderer for Pokemon TCG Pocket using original local resources.')
    parser.add_argument('--root',type=Path,default=PROJECT_ROOT,help=argparse.SUPPRESS)
    parser.add_argument('--version',action='version',version=__version__)
    commands=parser.add_subparsers(dest='command',required=True)
    listing=commands.add_parser('list',help='List cards from the original local database.')
    listing.add_argument('--expansion')
    listing.add_argument('--language','--lang',default='en-US')
    listing.add_argument('--supported-only',action='store_true',help='List eligible large models only.')
    listing.add_argument('--limit',type=int,default=30,help='Maximum rows; 0 shows all.')
    listing.add_argument('--json',type=Path,help='Save the catalog as JSON.')
    for command in ('prepare','render'):
        sub=commands.add_parser(command,help='Internal preparation without GPU.' if command=='prepare' else 'Export transparent PNGs.')
        selection=sub.add_mutually_exclusive_group(required=True)
        selection.add_argument('--cards',nargs='+',help='IDs such as A1-001 A1-008.')
        selection.add_argument('--expansion',help='Process a whole collection.')
        sub.add_argument('--language','--lang',default='en-US',help='en-US, pt-BR, ja-JP; all for all three; both for English and Portuges(Brazil).')
        sub.add_argument('--output',type=Path)
        sub.add_argument('--cache',type=Path)
        sub.add_argument('--supported-only',action='store_true',help='Select eligible large layouts only (may omit cards).')
        sub.add_argument('--limit',type=int,default=0)
        sub.add_argument('--dry-run',action='store_true',help='Internal validation without rendering.')
        sub.add_argument('--overwrite',action='store_true',help='Overwrite existing PNGs.')
        sub.add_argument('--stored-glyphs-only',action='store_true',help='Reject characters requiring dynamic font population.')
        sub.add_argument('--quality',choices=('large',),default='large',help='Export assembled large cards at 700 x 976.')
    return parser

def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf8',errors='replace')
    parser=_parser()
    args=parser.parse_args(argv)
    try:
        from .catalog import Catalog, project_root
        root=project_root(args.root)
        catalog=Catalog(root)
        if args.limit<0: raise ValueError('--limit deve ser maior ou igual a zero.')
        if args.command=='list':
            from .localization import LocaleRepository
            language=normalize_language(args.language)
            locale=LocaleRepository(root,language)
            rows=catalog.list_cards(args.expansion)
            if args.supported_only: rows=[v for v in rows if v['supported']]
            items=[]
            for row in rows:
                name,_=locale.lookup('Master',row['name_key'],262,allow_empty=True) if row['name_key'] else ('',{})
                items.append({'id':row['id'],'name':name,'supported':row['supported'],
                              'large_eligible':row['supported'],'native_available':row.get('native_available',False),
                              'exportable':row.get('exportable',row['supported']),
                              'reasons':row.get('unsupported_reasons',[])})
            if args.json: _write_json(args.json,{'language':language,'cards':items,'total':len(items)})
            shown=items[:args.limit] if args.limit else items
            for item in shown:
                label='eligible large model + native resources' if item['supported'] and item['native_available'] else 'eligible large model' if item['supported'] else 'native resources available' if item['native_available'] else 'resources pending'
                print('%-12s %-24s %s'%(item['id'],item['name'],label))
            print('%d cards; %d eligible large models; %d with native resources (file check; shaders validated during preparation).'%(len(items),sum(v['supported'] for v in items),sum(v['native_available'] for v in items)))
            return 0
        cards=[catalog.resolve(v) for v in args.cards] if args.cards else catalog.list_cards(args.expansion)
        cards=list({v['id']:v for v in cards}.values())
        if args.supported_only: cards=[v for v in cards if v['supported']]
        if args.limit: cards=cards[:args.limit]
        if not cards: raise ValueError('Nenhuma carta encontrada para essa seleção.')
        selection = args.language.lower()
        languages = list(LANGUAGES) if selection == 'all' else ['en-US','pt-BR'] if selection == 'both' else [normalize_language(args.language)]
        config=RenderConfig(root=root,output=(args.output or root/'renders').resolve(),
                            cache=(args.cache or root/'render-cache').resolve(),overwrite=args.overwrite,
                            stored_glyphs_only=args.stored_glyphs_only,quality=args.quality).validate()
        dry=args.command=='prepare' or args.dry_run
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        from .export_paths import metadata_root,metadata_path,existing_metadata_path
        report_file=metadata_root(config.output)/('batch-'+run_id+'.json')
        report={'version':__version__,'mode':'prepare' if dry else 'render','background':'transparent',
                'languages':languages,'source_master_sha256':catalog.master_sha256,'jobs':[],
                'requested_quality':config.quality,
                'scope':('Source-bound assembled large card only. Unsupported layouts/materials are reported; no low-resolution fallback.' if config.quality=='large' else 'Explicit legacy medium/automatic mode; native 367x512 is not HD and is never upscaled.'),
                'no_upscaling':True}
        from .source_state import source_input_digest
        input_digest = source_input_digest(root)
        report['source_input_sha256'] = input_digest
        from .backend import render_code_digest
        renderer_digest = render_code_digest()
        report['render_code_sha256'] = renderer_digest
        from .export_paths import ExportLayout,resolve_destination,copy_verified_legacy
        export_layout=ExportLayout(catalog,root)
        report['output_layout']='<language>/<collection-code>-<original localized name>/<card-id>.png'
        from .localization import UnsupportedLayout
        from .compiler import CompilationError
        if config.quality == 'auto':
            print('Modo automático: render grande quando compatível; demais cartas em 367 × 512 nativos. A apresentação média não é HD; nenhuma imagem será ampliada.')
        for card in cards:
            for language in languages:
                destination=resolve_destination(config.output,card,language,export_layout)
                job={'card':card['id'],'language':language,'output':str(destination),
                     'source_input_sha256':input_digest,
                     'render_code_sha256':renderer_digest,
                     'collection':export_layout.collection(card['expansion'],language)}
                try:
                    if not dry and not config.overwrite:
                        copied=copy_verified_legacy(config.output,destination,card,language,config.quality,catalog.master_sha256,input_digest)
                        if copied:job['organization']=copied['organization']
                    if not dry and destination.exists() and not config.overwrite:
                        job['status']='skipped_existing'
                        sidecar=existing_metadata_path(config.output,destination)
                        previous=json.loads(sidecar.read_text(encoding='utf8')) if sidecar.is_file() else {}
                        from .backend import verify_existing_png
                        verified=verify_existing_png(destination,previous,card['id'],language,config.quality,catalog.master_sha256,input_digest,renderer_digest)
                        job.update(quality=previous.get('quality'),verified_existing=verified,
                                   reason=None if verified else 'Existing PNG has incompatible provenance, quality or exporter version; use --overwrite to regenerate it.')
                    else:
                        prepared=prepare_job(config,card,language)
                        job.update(work=str(prepared['work']),passes=prepared['passes'],
                                   generated_font_glyphs=prepared['font_generation'],quality=prepared.get('quality','large'),
                                   fallback_reason=prepared.get('fallback_reason'),requested_quality=config.quality,
                                   fallback_details=prepared.get('fallback_details'),
                                   source_master_sha256=catalog.master_sha256)
                        if dry: job['status']='prepared'
                        else:
                            from .backend import VulkanBackend
                            result=VulkanBackend(root).render(prepared['assets_dir'],prepared['passes'],destination,
                                                              config.width,config.height)
                            job.update(status='rendered',result=result)
                            _write_json(metadata_path(config.output,destination),job)
                except (UnsupportedJob,UnsupportedLayout,CompilationError) as error:
                    job.update(status='unsupported',reason=str(error),details=getattr(error,'details',None))
                except Exception as error:
                    job.update(status='failed',error_type=type(error).__name__,reason=str(error))
                report['jobs'].append(job)
                report['counts']=dict(Counter(v['status'] for v in report['jobs']))
                report['complete'] = len(report['jobs']) == len(cards)*len(languages) and not any(
                    item['status'] in ('unsupported','failed') or (item['status']=='skipped_existing' and not item.get('verified_existing')) for item in report['jobs'])
                report['quality_counts']=dict(Counter(v.get('quality','unverified') for v in report['jobs']))
                _write_json(report_file,report)
                print('%s %s: %s%s%s'%(card['id'],language,job['status'],(' ['+job['quality']+']') if job.get('quality') else '',' — See the batch JSON report for details.' if job.get('reason') else ''))
        print('Result %s: %s'%('complete' if report['complete'] else 'partial',
              ', '.join('%s=%d'%(key,value) for key,value in report['counts'].items())))
        print('Presentations: '+', '.join('%s=%d'%(key,value) for key,value in report['quality_counts'].items()))
        print('Report: '+str(report_file))
        return 1 if report['counts'].get('failed') else (0 if report['complete'] else 2)
    except (ValueError,FileNotFoundError) as error:
        from .progress import error_details
        print('Error: '+error_details(error),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
