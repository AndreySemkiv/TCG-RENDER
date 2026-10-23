"""Original OL/UL/LI expansion for the inspected ARM64 localizer.

The circled numbers, bullets and indent strings are read from the selected
native library and original metadata. MtUIMessageTagHandler.Parse consumes one
following CR/LF sequence when a command ends in U+000C; it adds no line break.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import re
import struct

from .localization import UnsupportedLayout

_TAGS=re.compile(r'\[(/?)Ctrl:(OL|UL|LI)([^\[\]]*)\]')
_LANGUAGE={'ja-JP':0,'en-US':1,'pt-BR':7} # MtUILocalizeDef.Language, not Asset3D.LanguageType.


@lru_cache(maxsize=4)
def native_list_contract(root):
    root=Path(root)
    paths=[root/'input/native-code/libil2cpp.so',
           root/'original-render-pipeline/metadata/global-metadata.original-decoded.dat']
    hashes=['eefba3938aa7bc1751135abb56b82f12d46c290c0a598b59e459d6f1a15c4d5c',
            '905b9505a935828ac7174b7086d15e23640dcf61451e604e0085ae98e06c8618']
    inputs=[]
    for path,digest in zip(paths,hashes):
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise UnsupportedLayout('List-control native source differs from inspected original',file=str(path))
        inputs.append(raw)
    binary,metadata=inputs
    ph=struct.unpack_from('<Q',binary,32)[0];size,count=struct.unpack_from('<HH',binary,54)
    segments=[struct.unpack_from('<IIQQQQQQ',binary,ph+i*size) for i in range(count)]
    def offset(address):
        segment=next(s for s in segments if s[0]==1 and s[3]<=address<s[3]+s[5])
        return segment[2]+address-segment[3]
    sh=struct.unpack_from('<Q',binary,40)[0];size,count=struct.unpack_from('<HH',binary,58)
    relocations={}
    for i in range(count):
        section=struct.unpack_from('<IIQQQQIIQQ',binary,sh+i*size)
        if section[1]!=4:continue
        for pos in range(section[4],section[4]+section[5],section[9]):
            address,kind,value=struct.unpack_from('<QQq',binary,pos)
            if kind&0xffffffff==1027:relocations[address]=value
    def qword(address):
        return relocations.get(address,struct.unpack_from('<Q',binary,offset(address))[0])
    literal_offset,literal_size,text_offset,text_size=struct.unpack_from('<4I',metadata,8)
    def literal(address):
        value=qword(address);encoded=value if value>>29==5 else qword(value)
        if encoded>>29!=5:raise UnsupportedLayout('Original list literal is unavailable',slot=hex(address))
        index=(encoded&0x1fffffff)>>1
        if index>=literal_size//8:raise UnsupportedLayout('Original list literal index is invalid')
        length,pos=struct.unpack_from('<II',metadata,literal_offset+index*8)
        if pos+length>text_size:raise UnsupportedLayout('Original list literal bounds are invalid')
        return metadata[text_offset+pos:text_offset+pos+length].decode('utf8')
    return {'numbers':[literal(v) for v in (0x72f5a60,0x72f5a40,0x72f5a28,0x72f5a18,0x72f5a50,
                 0x72f5a30,0x72f5a58,0x72f5a48,0x72f5a20,0x72f5a38)],
        'bullets':[literal(qword(0x6e2f448+i*8)) for i in range(12)],
        'indents':[literal(qword(0x6e2f4a8+i*8)) for i in range(12)],
        'default_bullet':literal(0x72eedc8),'default_indent':literal(0x72f5c60),
        'indent_open':literal(0x72f5c48),'indent_close':literal(0x72f5af8),
        'suffix':literal(0x72684b8),'space':literal(0x7269ab8),
        'number_format':literal(0x72f5c50),'plain_number_format':literal(0x72f5c40),
        'provenance':{'native_sha256':hashes[0],'metadata_sha256':hashes[1],
            'constructor':'0x4A1C180','get_list_item':'0x4A1F4C4','ctrl':'0x4A1D780',
            'following_newline_consumption':'0x5BF9380-0x5BF9410',
            'language_enum':'MtUILocalizeDef.Language'}}


def expand_list_controls(value,locale,audit=None):
    if not _TAGS.search(value):return value
    if locale is None:raise UnsupportedLayout('Original list controls require the selected source locale')
    contract=native_list_contract(str(Path(locale.root).resolve()))
    language=_LANGUAGE[locale.language]
    indent=contract['indents'][language-1] if 1<=language<=12 else contract['default_indent']
    bullet=contract['bullets'][language-1] if 1<=language<=12 else contract['default_bullet']
    if not re.fullmatch(r'\d+(?:\.\d+)?em',indent):
        raise UnsupportedLayout('Original list indent unit is not recovered',indent=indent)
    active=None;in_item=False;counter=0;parts=[];end=0;commands=[]
    for match in _TAGS.finditer(value):
        if match.start()<end:continue
        parts.append(value[end:match.start()]);closing=bool(match[1]);kind=match[2]
        if match[3].strip():raise UnsupportedLayout('Original list-control attributes are not recovered',tag=match[0])
        if kind in ('OL','UL'):
            if closing:
                if active!=kind or in_item:raise UnsupportedLayout('Unmatched original list close',tag=match[0])
                active=None
            else:
                if active is not None:raise UnsupportedLayout('Nested original lists are not recovered')
                active=kind;counter=0
            replacement='' # Native command returns U+000C; Parse removes it.
            end=match.end()
            # Parse skips an optional CR followed by an optional LF, only here.
            if end<len(value) and value[end]=='\r':end+=1
            if end<len(value) and value[end]=='\n':end+=1
        elif closing:
            if not active or not in_item:raise UnsupportedLayout('Unmatched original list item close')
            in_item=False;replacement=contract['indent_close'];end=match.end()
        else:
            if not active or in_item:raise UnsupportedLayout('Original list item is outside a recovered list')
            in_item=True;counter+=1;end=match.end()
            if active=='OL' and counter>10:
                replacement=contract['number_format'].format(counter,indent)
            else:
                prefix=contract['numbers'][counter-1] if active=='OL' else bullet
                replacement=prefix+contract['indent_open']+indent+contract['suffix']
        parts.append(replacement)
        commands.append({'tag':match[0],'replacement':replacement,'counter':counter,
                         'consumed_following_source':value[match.end():end]})
    if active or in_item:raise UnsupportedLayout('Unclosed original list control')
    parts.append(value[end:]);result=''.join(parts)
    if audit is not None:audit.append({'kind':'Ctrl:List','language_enum':language,
        'indent':indent,'commands':commands,'native_contract':contract['provenance']})
    return result
