"""Dynamic collection coverage without treating eligibility as visual proof."""
from collections import Counter

from .assets import _prefab_paths
from .catalog import Catalog, project_root


def collection_coverage(root):
    root = project_root(root)
    catalog = Catalog(root)
    cards = catalog.list_cards()
    prefabs = _prefab_paths(str(root))
    collections = []
    for expansion in dict.fromkeys(card['expansion'] for card in cards):
        members = [card for card in cards if card['expansion'] == expansion]
        eligible = [card for card in members if card['supported']]
        native = sum(card.get('native_available',False) for card in members)
        reasons = Counter(reason for card in members for reason in card['unsupported_reasons'])
        collections.append({
            'expansion':expansion, 'total':len(members), 'layout_eligible':len(eligible),
            'pending_layout':len(members)-len(eligible),
            'eligible_with_local_large_prefab':sum(card['asset_id'] in prefabs for card in eligible),
            'all_layouts_implemented':len(eligible) == len(members),
            'native_medium_files_available':native,
            'auto_files_available':sum(card.get('exportable',False) for card in members),
            'pending_reasons':dict(reasons.most_common()),
        })
    return {'authority':'original local MasterMemory + current source-bound renderer profile',
            'master_sha256':catalog.master_sha256, 'root':str(root),
            'total_cards':len(cards), 'collection_count':len(collections),
            'layout_eligible':sum(card['supported'] for card in cards),
            'native_medium_files_available':sum(card.get('native_available',False) for card in cards),
            'auto_files_available':sum(card.get('exportable',False) for card in cards),
            'collections':collections,
            'limits':[
                'Collections are read from the selected local source, not from an online latest-release catalogue.',
                'Layout eligibility and downloaded prefab presence do not certify complete resource compatibility or visual 1:1.',
                'Every selected card must still pass source compatibility, asset preparation and rendering validation.',
                'Pending layouts are never silently counted as successfully exported cards.',
                'Automatic mode uses the original static medium presentation at 367x512 for large layouts not implemented; this is not HD and does not emulate large foil/parallax.',
            ]}
