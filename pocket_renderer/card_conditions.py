"""Current-source contracts for serialized card and font predicates.

The inspected matcher adds Dual/RGB predicates and moves HasSubName. Match
method structure rather than app versions; changed logic fails explicitly.
"""
from functools import lru_cache
from pathlib import Path
from .localization import UnsupportedLayout

CORE = 'Lettuce.Infrastructure.Card.Core'
OLD_MATCHER = '9622c7036ed4f554332d372b368efa6f1cdc28f8dabe00cb0a4242db4ded15bf'
NEW_MATCHER = '81958f0638af65ee08e09ff7ee92fb338af0d6c20ad746af4cbb3bd9909369ef'
OLD_FONT = '8c8c42135fffa8fd51d2ebfd0cb193e7457bcc8629a2eb8320b700bfb8175985'
NEW_FONT = 'f82aa6842a59655e4a39bdef94ef9c7ed0b63a27f80cff268fb902f7f3b979bf'


@lru_cache(maxsize=4)
def native_condition_contract(root):
    from .native_text_contract import source
    native = source(str(Path(root).resolve()))
    matcher = native.normalized_method(CORE, 'CardConditionView', 'SetActive')
    font = native.normalized_method(CORE, 'FontGroupConditions', 'GetFontGroup')
    if (matcher, font) == (OLD_MATCHER, OLD_FONT):
        return {'dual': 0, 'rgb': 0, 'sub_name': 512, 'font_dual': False,
                'provenance': native.provenance}
    if (matcher, font) != (NEW_MATCHER, NEW_FONT):
        raise UnsupportedLayout('Original card condition matcher structure is not recovered')
    # SetActive compares condition bytes48/49 with PokemonCardData bytes192/193.
    # It bypasses every Pokemon-only test when the PokemonCardData cast is null.
    # CreatePokemonCardData calls IsDualType and supplies Rarity ==1000 as RGB.
    checks = [
        ('Lettuce.Domain.Common', 'PokemonEntity', 'get_PokemonTypes',
         '6b819324b4a3bc2d390c83c7fe02ea41a1f57ae5d4f2a9b0c9256341a5b8d37f'),
        ('Lettuce.Domain.Common', 'PokemonEntity', 'get_IsDualType',
         'eb8bf7f4020d927209dd4f615faa2da394216aeb56178567c3d6687ed52125e0'),
        ('Lettuce.Domain.Common', 'PokemonCardEntity', 'get_Rarity',
         '1f31d5ff0e22c7c2e3b958e4e0186cb71a2e67a614d53dd8d744824fcd4b1d53'),
        ('Lettuce.Infrastructure.Card', 'ValueConverter', 'CreatePokemonCardData',
         '8ae99b10950c53782e9f22cfae8801afbf336f42f3dbcf741eb31f73aa82ceae')]
    checks.extend([
        (CORE, 'Condition', 'get_IsDual',
         'b5f756a94e2a4fd04c581b81886364460d3cd7c165e6a51d5f3195a9439a10c4'),
        (CORE, 'Condition', 'get_IsRGBFormat',
         '6a3cb8c4f92b5961effe3a4ee16cd73e98d1d0f94c954c6b45545a35177d46d8'),
        (CORE+'.Data', 'PokemonCardData', 'get_IsDual',
         'b0a3d2f0cb191f85457175a85ae32267cbdee7c6bcc85435bf66452e79aa83d1'),
        (CORE+'.Data', 'PokemonCardData', 'get_IsRGBFormat',
         '3fbd9ac04a57c1b55ae6f08b72f234db081008d104b7934efc93b0b2fd5c0d3e'),
        (CORE+'.Data', 'PokemonCardData', '.ctor',
         'b05470641cba32409ed92f040debbc5f439bd5f5bdb38d7c6a263589bfa80f70')])
    for namespace, owner, method, digest in checks:
        if native.normalized_method(namespace, owner, method) != digest:
            raise UnsupportedLayout('Original card condition data mapping is not recovered', method=method)
    # Normalization ignores relocated call addresses: bind the inspected call
    # to the actual source IsDualType method as well.
    import struct
    address = native.method('Lettuce.Infrastructure.Card', 'ValueConverter', 'CreatePokemonCardData')
    calls = set()
    for i, (word,) in enumerate(struct.iter_unpack('<I', native.method_bytes(
            'Lettuce.Infrastructure.Card', 'ValueConverter', 'CreatePokemonCardData'))):
        if word & 0xfc000000 == 0x94000000:
            relative = word & 0x3ffffff
            if relative & 0x2000000: relative -= 0x4000000
            calls.add(address+i*4+relative*4)
    expected_calls = (
        native.method('Lettuce.Domain.Common', 'PokemonEntity', 'get_IsDualType'),
        native.method(CORE+'.Data', 'PokemonCardData', '.ctor'))
    if not set(expected_calls) <= calls:
        raise UnsupportedLayout('Original Dual/RGB mapping source call targets are not recovered')
    return {'dual': 512, 'rgb': 1024, 'sub_name': 2048, 'font_dual': True,
            'provenance': native.provenance}


def pokemon_features(card):
    """Native PokemonEntity.IsDualType: Count>1; RGB: card rarity ==1000."""
    pokemon = card.get('pokemon')
    if not pokemon:
        return False, False
    types = pokemon.get('PokemonTypes')
    rarity = (card.get('card') or {}).get('Rarity')
    if not isinstance(types, list) or not types or not isinstance(rarity, int):
        raise UnsupportedLayout('Original Dual/RGB master inputs are unavailable')
    return len(types) > 1, rarity == 1000


def pokemon_condition_extra(contract, condition, card):
    dual, rgb = pokemon_features(card)
    flags = condition['_conditionType']
    return ((not flags & contract['dual'] or bool(condition['_isDual']) == dual)
            and (not flags & contract['rgb'] or bool(condition['_isRGBFormat']) == rgb))


def trainer_condition_matches(contract, condition, asset_type, categories, language):
    """Native Pokemon tests are skipped when the data is a trainer."""
    flags = condition['_conditionType']
    trainer_flags = 1|4|128|256|contract['sub_name']
    pokemon_flags = 2|8|32|64|contract['dual']|contract['rgb']
    if flags & ~(trainer_flags|pokemon_flags):
        raise UnsupportedLayout('Unrecovered trainer UI condition', condition=condition)
    return ((not flags&1 or condition['_language']==language)
        and (not flags&4 or condition['_trainersType']==asset_type)
        and (not flags&contract['sub_name'] or not condition['_hasSubType'])
        and (not flags&128 or bool(condition['_isFuture'])==(3 in categories))
        and (not flags&256 or bool(condition['_isAncient'])==(2 in categories)))
