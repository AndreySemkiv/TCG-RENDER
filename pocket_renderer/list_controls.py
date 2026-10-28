"""Original OL/UL/LI expansion for the inspected ARM64 localizer.

The circled numbers, bullets and indent strings are read from the selected
native library and original metadata. MtUIMessageTagHandler.Parse consumes one
following CR/LF sequence when a command ends in U+000C; it adds no line break.
"""
from functools import lru_cache
from pathlib import Path
import re

from .localization import UnsupportedLayout

_TAGS=re.compile(r'\[(/?)Ctrl:(OL|UL|LI)([^\[\]]*)\]')
_LANGUAGE={'ja-JP':0,'en-US':1,'pt-BR':7} # MtUILocalizeDef.Language, not Asset3D.LanguageType.


@lru_cache(maxsize=4)
def native_list_contract(root):
    from .native_text_contract import source
    native=source(str(Path(root).resolve()))
    namespace='Lettuce.Infrastructure.LocLabel.TagCommandImpl';name='LtUICtrlTagCommand'
    inspected={'.ctor':'ebb6a5b7f629f1650b07f413853c4f0c8cfeca27f8d272e53f2fd5b551f680a6',
               'GetListItemStartString':'9bd28cd486746cc389497958d566c510dacb932da832cc38121d57a35c0d58df',
               'PreProcessForTMP':'9cdc491775d5a5fd6cf07b1b42c3e46950deb57575d2fde757bc1ca03ed7dd8a'}
    for method,digest in inspected.items():
        if native.normalized_method(namespace,name,method)!=digest:
            raise UnsupportedLayout('List-control native method structure is not recovered',method=method)
    if native.normalized_method('MtUISystem','MtUIMessageTagHandler','Parse')!='c22e50b082546dc93c584c4d744a28fc4d6df52fc1c7bfce72077626533923ed':
        raise UnsupportedLayout('Original list newline consumption is not recovered')
    def literals(method):
        values=[]
        for ref in native.method_references(namespace,name,method):
            try:values.append(native.literal(ref['target']))
            except ValueError:pass
        return values
    # The inspected constructor first registers literals, then stores the actual
    # ten-number array in order. Read that last sequence from this source.
    numbers=[value for value in literals('.ctor') if len(value)==1 and 0x2460<=ord(value)<=0x2469][-10:]
    if len(numbers)!=10 or len(set(numbers))!=10:
        raise UnsupportedLayout('Original list number array is not recovered')
    list_literals=literals('GetListItemStartString')
    ctrl_literals=literals('PreProcessForTMP')
    required={'<indent=','>',' ','{0}.<indent={1}>','{0}. '}
    if not required<=set(list_literals) or '</indent>' not in ctrl_literals:
        raise UnsupportedLayout('Original list formatting literals are not recovered')
    arrays={}
    for ref in native.method_references(namespace,name,'GetListItemStartString'):
        try:values=[native.literal(ref['target']+i*8) for i in range(12)]
        except ValueError:continue
        if all(re.fullmatch(r'\d+(?:\.\d+)?em',v) for v in values):arrays['indents']=values
        elif all(v in ('♦','◆') for v in values):arrays['bullets']=values
    if set(arrays)!={'indents','bullets'}:
        raise UnsupportedLayout('Original per-language list arrays are not recovered')
    # In the inspected branch, fallback literals are the first emitted values
    # after the two language arrays. Stable code fingerprints retain that flow.
    first_array_ref=next((i for i,ref in enumerate(native.method_references(namespace,name,'GetListItemStartString'))
        if ref['target'] and _array_matches(native,ref['target'],arrays['indents'])),None)
    if first_array_ref is None:
        raise UnsupportedLayout('Original list fallback branch is unavailable')
    refs=native.method_references(namespace,name,'GetListItemStartString')[first_array_ref+1:]
    fallbacks=[]
    for ref in refs:
        try:fallbacks.append(native.literal(ref['target']))
        except ValueError:pass
    default_indent=next((v for v in fallbacks if re.fullmatch(r'\d+(?:\.\d+)?em',v)),None)
    default_bullet=next((v for v in fallbacks if v in ('♦','◆')),None)
    if default_indent is None or default_bullet is None:
        raise UnsupportedLayout('Original list fallback literals are unavailable')
    return dict(arrays,numbers=numbers,default_bullet=default_bullet,default_indent=default_indent,
        indent_open='<indent=',indent_close='</indent>',suffix='>',space=' ',
        number_format='{0}.<indent={1}>',plain_number_format='{0}. ',
        provenance=dict(native.provenance,
            constructor=hex(native.method(namespace,name,'.ctor')),
            get_list_item=hex(native.method(namespace,name,'GetListItemStartString')),
            ctrl=hex(native.method(namespace,name,'PreProcessForTMP')),
            following_newline_consumption=hex(native.method('MtUISystem','MtUIMessageTagHandler','Parse')),
            language_enum='MtUILocalizeDef.Language'))


def _array_matches(native,address,values):
    try:return [native.literal(address+i*8) for i in range(len(values))]==values
    except ValueError:return False


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
