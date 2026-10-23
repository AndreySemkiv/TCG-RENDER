"""Original MS text references backed by master records and localized labels.

Static native evidence is retained in reports/ms-references-native. Unknown
attributes and unresolved plural cases fail; no substitute names are supplied.
"""
from functools import lru_cache
import re
from .localization import UnsupportedLayout,_read_json

METHODS={'Text:CardName':'0x496E444','Text:AttackName':'0x496EE48',
    'Text:AbilityName':'0x496EE84','Text:SpecialCondition':'0x496E830','Text:EvolutionPokemon':'0x496F5B4',
    'Text:AdditionalName':'0x496F88C','C:Nbsp':'0x5BFCE74','C:Nbh':'0x5BFCFB4'}

# Original Asset3D LanguageType indices 1..12, native int table 0x1E4A590.
# pt-BR is index 8 (74%), not the Domain.Common language enum.
_ADDITIONAL_PERCENT=(70,74,70,70,70,70,70,74,74,74,70,70)


def _additional_name(attrs,parameters,locale,audit):
    from .localization import LANGUAGES
    if set(attrs)-{'v','id','type','space'}:
        raise UnsupportedLayout('Original AdditionalName attributes are not recovered',attributes=attrs)
    if 'v' in attrs:key=attrs['v']
    else:
        try:key=parameters[int(attrs.get('id','0'))]
        except (ValueError,TypeError,IndexError):
            raise UnsupportedLayout('Original AdditionalName parameter is missing',attributes=attrs)
    kind=attrs.get('type');space=attrs.get('space','none') or 'none'
    if kind not in ('A','B','C','region','origin'):
        raise UnsupportedLayout('Original AdditionalName type is not recovered',type=kind)
    if space not in ('none','pre','post'):
        raise UnsupportedLayout('Original AdditionalName spacing is not recovered',space=space)
    selected=_named_label(locale,key,'AdditionalName')
    if selected is None:raise UnsupportedLayout('Original AdditionalName localized label is missing',key=key)
    label,provenance=selected
    enum=LANGUAGES[locale.language]['asset_enum']
    percentage=100
    if kind in ('A','C','region'):
        percentage=_ADDITIONAL_PERCENT[enum-1] if 1<=enum<=12 else 74
    elif 1<=enum<=12 and (0xc7d>>(enum-1))&1:
        percentage=70
    result=label if percentage==100 else '<size=%d%%>%s</size>'%(percentage,label)
    narrow='<size=75%> </size>' # GetNarrowSpaceChar(language, TMP=1, size=75).
    if space=='pre':result=narrow+result
    elif space=='post':result+=narrow
    if audit is not None:audit.append({'kind':'Text:AdditionalName','attributes':attrs,
        'native_method':METHODS['Text:AdditionalName'],'resolve_method':'0x496FAA4',
        'size_method':'0x496FEE0','narrow_space_method':'0x496FF58',
        'asset_language_enum':enum,'percentage_size':percentage,'space_percentage_size':75,
        'localized_key':key,'localization':provenance,'result':result})
    return result


def expand_additional_names(value,locale,audit=None):
    """Expand direct name slots without reinterpreting unrelated MS commands."""
    pattern=re.compile(r'\[Text:AdditionalName(?:\s+[^\[\]]*)?\]')
    attribute=re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
    return pattern.sub(lambda m:_additional_name(dict(attribute.findall(m[0])),[],locale,audit),value)


@lru_cache(maxsize=16)
def _records(root,table,key):
    from .catalog import Catalog
    return {r[key]:r for r in Catalog(root).table(table)}


def _category(locale,name):
    path=locale.root/'reports/file-only-analysis/locale-original/en_US/Master_Attribute.json'
    rows=[r for r in _read_json(str(path))['entries'] if r['key']=='LocalizeMaster'+name and len(r['value'])==1]
    if len(rows)!=1:raise UnsupportedLayout('Original reference category is ambiguous',attribute=name)
    return ord(rows[0]['value'])



def _named_label(locale,key,file_name):
    """Read original localized physical-file categories, including plural variants.

    The complete mapping is in Master itself. Master_Attribute omits plural
    categories; guessing base+1 or using the English ID for Japanese is invalid.
    None matches native IsLabelExists=false for missing/empty variant labels.
    """
    data=_read_json(str(locale.root/'reports/file-only-analysis/locale-original'/locale.directory/'Master.json'))
    mappings=[r for r in data['entries'] if r['key']=='LocalizeMaster'+file_name
              and r['category']==1 and len(r['value'])==1]
    if not mappings:return None
    if len(mappings)!=1:raise UnsupportedLayout('Original physical reference file category is ambiguous',file_name=file_name)
    mapping=mappings[0];category=ord(mapping['value'])
    rows=[r for r in data['entries'] if r['key']==key and r['category']==category and r['value']]
    if not rows:return None
    if len({r['value'] for r in rows})!=1:
        raise UnsupportedLayout('Original physical reference label is ambiguous',file_name=file_name,key=key)
    row=rows[0]
    return row['value'],{'authority':'original-localization','language':locale.language,
        'source':data['source'],'source_sha256':data['sha256'],'table':'Master','key':key,
        'category':category,'entry_offset':row['entry_offset'],
        'physical_file_category':{'attribute':mapping['key'],'entry_offset':mapping['entry_offset']}}


def resolve_reference(kind,attrs,parameters,locale,audit=None):
    if kind not in METHODS:return None
    if kind=='C:Nbsp':return '\u00a0'
    if kind=='C:Nbh':return '\u2011'
    if kind=='Text:AdditionalName':return _additional_name(attrs,parameters,locale,audit)
    unknown=set(attrs)-{'id','v','ref','num_ref','case','space','style','max_length','v0','v1','suffix','plural'}
    if unknown:raise UnsupportedLayout('Original reference attributes are not recovered',kind=kind,attributes=sorted(unknown))
    if 'v' in attrs:value=attrs['v']
    else:
        try:value=parameters[int(attrs.get('id','0'))]
        except (ValueError,TypeError,IndexError):raise UnsupportedLayout('Original reference parameter is missing',kind=kind,attributes=attrs)
    plural=attrs.get('plural')=='1'
    if attrs.get('suffix') and kind!='Text:AbilityName':
        raise UnsupportedLayout('Original reference suffix is not recovered for this kind',kind=kind)
    ref=attrs.get('num_ref',attrs.get('ref'))
    if ref is not None:
        try:count=int(parameters[int(ref)])
        except (ValueError,TypeError,IndexError):raise UnsupportedLayout('Original reference count is not recovered',kind=kind,attributes=attrs)
        plural = plural or abs(count)!=1
    if kind=='Text:CardName':
        record=_records(str(locale.root),'Character','CharacterID').get(value)
        if not record:raise UnsupportedLayout('Original referenced Character is missing',value=value)
        key=record['DisplayNameMSID'];category=262
    elif kind in ('Text:AttackName','Text:AbilityName'):
        table='PokemonAttackName' if kind=='Text:AttackName' else 'PokemonAbilityName'
        record=_records(str(locale.root),table,table+'ID').get(value)
        if not record:raise UnsupportedLayout('Original referenced attack/ability is missing',value=value)
        key=record['NameMSID'];category=50 if kind=='Text:AttackName' else 258
    elif kind=='Text:SpecialCondition':
        if value not in ('Poisoned','Burned','Confused','Asleep','Paralyzed'):
            raise UnsupportedLayout('Original special condition value is not recovered',value=value)
        key=value;category=_category(locale,'SpecialConditionType')
    else:
        if value not in ('Basic','One','Two'):raise UnsupportedLayout('Original evolution-stage reference is not recovered',value=value)
        key='EVOLUTION_STAGE_POKEMON_'+value;category=_category(locale,'StageTypePokemon')
    file_name={'Text:CardName':'CardCharacterName','Text:AttackName':'CardAttackName',
               'Text:AbilityName':'CardAbilityName','Text:SpecialCondition':'SpecialConditionType',
               'Text:EvolutionPokemon':'StageTypePokemon'}[kind]
    selected=None
    suffix=attrs.get('suffix')
    if suffix:
        # Native 0x4970E94–0x4970EE8 checks the localized variant before selecting it.
        selected=_named_label(locale,key+'_'+suffix,file_name+suffix)
        if selected is not None:
            key+='_'+suffix;file_name+=suffix
    if plural and locale.language in ('en-US','pt-BR'):
        candidate=_named_label(locale,key,file_name+'_plural')
        if candidate is not None:
            selected=candidate;file_name+='_plural'
    label,provenance=selected if selected is not None else locale.lookup('Master',key,category)
    category=provenance['category']
    # The native plain-style branch removes bold formatting only, preserving
    # the localized wording and any non-breaking group.
    if attrs.get('style')=='plain':label=re.sub(r'\[/?Ctrl:Bold(?:\s+[^\[\]]*)?\]','',label)
    elif attrs.get('style') not in (None,''):raise UnsupportedLayout('Original reference style is not recovered',style=attrs['style'])
    case=attrs.get('case')
    if case=='upper':label=label[:1].upper()+label[1:]
    elif case=='lower':label=label[:1].lower()+label[1:]
    elif case in ('upper_all','lower_all'):
        if '[' in label or '<' in label:raise UnsupportedLayout('Original case conversion around nested markup is not recovered')
        label=label.upper() if case=='upper_all' else label.lower()
    elif case not in (None,''):raise UnsupportedLayout('Original reference case is not recovered',case=case)
    space=attrs.get('space')
    if space in ('nbsp','hyphen','nbh'):label=label.replace(' ',{'nbsp':'\u00a0','hyphen':'-','nbh':'\u2011'}[space])
    elif space not in (None,''):raise UnsupportedLayout('Original reference space conversion is not recovered',space=space)
    if kind=='Text:CardName':
        label=label.replace('\r\n','').replace('\r','').replace('\n','')
        if value.endswith('_EX'):label=re.sub(r'\s?-?(ex)$','[Img:ex]',label)
    if audit is not None:audit.append({'kind':kind,'attributes':attrs,'parameter_value':value,'native_method':METHODS[kind],
        'localized_key':key,'category':category,'localization':provenance,'result':label})
    return label
