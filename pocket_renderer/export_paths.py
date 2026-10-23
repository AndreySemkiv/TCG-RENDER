"""Organized export paths from the selected original collection catalogue.

No existing export is moved or deleted. A verified legacy PNG can be copied
byte-for-byte into the new collection folder when a batch is resumed.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import unicodedata
import uuid

from .config import normalize_language
from .localization import LocaleRepository


def _identifier(value):
    value=str(value)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',value) or value.endswith('.'):
        raise ValueError('Identificador inválido para organizar a exportação: '+value)
    return value


def safe_component(value,limit=180):
    """Keep localized Unicode while respecting Windows component restrictions."""
    value=unicodedata.normalize('NFC',str(value))
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]','-',value)
    value=re.sub(r'\s+',' ',value).strip(' .')
    if not value:value='_'
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',value,re.I):value='_'+value
    if len(value.encode('utf-16-le'))//2>limit:
        digest=hashlib.sha256(value.encode('utf8')).hexdigest()[:10]
        # Slice whole Unicode characters; never leave an unpaired surrogate.
        while len(value.encode('utf-16-le'))//2>limit-11:value=value[:-1]
        value=value.rstrip(' .')+'-'+digest
    return value


def _contained(output,*components):
    base=Path(output).resolve()
    candidate=base.joinpath(*components).resolve()
    try:candidate.relative_to(base)
    except ValueError:raise ValueError('O caminho de exportação sai da pasta escolhida: '+str(candidate))
    return candidate


class ExportLayout:
    """Cache original collection titles independently for each export language."""
    def __init__(self,catalog,root):
        self.root=Path(root);self.catalog=catalog;self.rows={};self.locales={};self.folders={}
        seen={}
        for row in catalog.table('Expansion'):
            code=_identifier(row['ExpansionID']);key=code.casefold()
            if key in seen:raise ValueError('Coleções colidem no sistema de arquivos Windows: '+seen[key]+' / '+code)
            seen[key]=code;self.rows[code]=row

    def collection(self,code,language):
        code=_identifier(code);language=normalize_language(language);key=(code,language)
        if key in self.folders:return self.folders[key]
        if code not in self.rows:raise ValueError('Coleção ausente do banco original: '+code)
        if language not in self.locales:self.locales[language]=LocaleRepository(self.root,language)
        locale=self.locales[language]
        row=self.rows[code];title=code;provenance=None;errors=[]
        for name_key in (row.get('LongNameMSID'),row.get('NameMSID')):
            if not name_key:continue
            try:
                value,source=locale.lookup('Master',name_key,allow_empty=True)
                if '[Mst:' in value:value=locale.resolve_references(value)
                if value and value!=code:
                    title=value;provenance=source;break
            except (ValueError,FileNotFoundError) as error:
                errors.append({'key':name_key,'reason':str(error)})
        folder=safe_component(code+('-'+title if title!=code else ''))
        result={'code':code,'language':language,'name':title,'folder':folder,
            'name_provenance':provenance,'master_sha256':self.catalog.master_sha256,
            'name_fallback':title==code,'name_lookup_errors':errors}
        self.folders[key]=result
        return result


def resolve_destination(output,card,language,layout):
    """Return <output>/<language>/<code>-<original title>/<card>.png."""
    language=normalize_language(language)
    collection=layout.collection(card['expansion'],language)
    filename=safe_component(_identifier(card['id']))+'.png'
    return _contained(output,language,collection['folder'],filename)


def metadata_root(output):
    """Keep receipts outside the tree containing exported images."""
    output=Path(output).resolve()
    return output.with_name(output.name+'-metadata')


def metadata_path(output,destination):
    output=Path(output).resolve()
    relative=Path(destination).resolve().relative_to(output).with_suffix('.json')
    return _contained(metadata_root(output),*relative.parts)


def existing_metadata_path(output,destination):
    separated=metadata_path(output,destination)
    return separated if separated.is_file() else Path(destination).with_suffix('.json')


def copy_verified_legacy(output,destination,card,language,quality,master_sha256,source_input_sha256=None):
    """Copy an accepted old <language>/<code> export, preserving its bytes.

    Return the copied metadata, or None if no compatible old result exists.
    Invalid/medium/wrong-language exports never satisfy a large render request.
    """
    from .backend import verify_existing_png
    destination=Path(destination).resolve();sidecar=metadata_path(output,destination)
    try:destination.relative_to(Path(output).resolve())
    except ValueError:raise ValueError('O destino organizado sai da pasta escolhida: '+str(destination))
    legacy=_contained(output,normalize_language(language),_identifier(card['expansion']),safe_component(_identifier(card['id']))+'.png')
    if destination.exists() or sidecar.exists() or legacy==destination:return None
    legacy_metadata=existing_metadata_path(output,legacy)
    if not legacy.is_file() or not legacy_metadata.is_file():return None
    try:previous=json.loads(legacy_metadata.read_text(encoding='utf8'))
    except (OSError,ValueError):return None
    if not verify_existing_png(legacy,previous,card['id'],language,quality,master_sha256,source_input_sha256):return None
    previous['output']=str(destination)
    previous['organization']={'copied_from':str(legacy),'policy':'Verified byte-for-byte copy; original files retained.',
        'original_metadata_sha256':hashlib.sha256(legacy_metadata.read_bytes()).hexdigest()}
    destination.parent.mkdir(parents=True,exist_ok=True)
    sidecar.parent.mkdir(parents=True,exist_ok=True)
    suffix='.'+uuid.uuid4().hex+'.partial'
    png_temp=destination.with_name(destination.name+suffix);json_temp=sidecar.with_name(sidecar.name+suffix)
    try:
        shutil.copyfile(legacy,png_temp)
        if not verify_existing_png(png_temp,previous,card['id'],language,quality,master_sha256,source_input_sha256):
            raise ValueError('O PNG legado mudou durante a cópia verificada: '+str(legacy))
        json_temp.write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf8')
        if destination.exists() or sidecar.exists():return None
        png_temp.replace(destination);json_temp.replace(sidecar)
    finally:
        for path in (png_temp,json_temp):
            if path.exists():path.unlink()
    return previous
