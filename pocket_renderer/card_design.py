"""Follow the selected card's original CardSettings design PPtr.

The design is source data, not a rarity-name heuristic. Paths start from the
already indexed original L prefab to preserve source filename aliases.
"""
from pathlib import Path
from .assets import _prefab_paths


def load_card_design(resources,card):
    from .localization import UnsupportedLayout
    prefab=_prefab_paths(str(resources.root)).get(card['asset_id'])
    if not prefab:raise UnsupportedLayout('Original card prefab is not available for design lookup',asset_id=card['asset_id'])
    relative=str(Path(prefab).parent.parent.parent/'CardSettings.asset_bundles')
    settings=[o for o in resources.load(relative).objects if o.type.name=='MonoBehaviour' and '_cardDesignSettings' in o.read_typetree()]
    if len(settings)!=1:raise UnsupportedLayout('Original per-card settings resource is ambiguous',file=relative)
    owner=settings[0];data=owner.read_typetree()
    if data['_id']!=card['card_id']:raise UnsupportedLayout('Original per-card settings identity differs from master',source_id=data['_id'],master_id=card['card_id'])
    design,file=resources.resolve(owner,data['_cardDesignSettings'])
    return owner,relative,design,file


def design_activation(resources,design):
    """Evaluate original DynamicUIView child selection without changing source."""
    from .localization import UnsupportedLayout
    overrides={}
    for switch in design.read_typetree()['_dynamicUIs']:
        key,_=resources.resolve(design,switch['_key']);view=key.read_typetree()
        go=view['m_GameObject']['m_PathID']
        if go not in resources.transforms:raise UnsupportedLayout('Original dynamic UI key belongs to another prefab',key=key.path_id)
        parent=resources.trees[resources.transforms[go]]
        chosen=switch['_gameObject']['m_PathID']
        children=[resources.trees[p['m_PathID']]['m_GameObject']['m_PathID'] for p in parent['m_Children']]
        if chosen and chosen not in children:raise UnsupportedLayout('Original design chooses a non-child dynamic UI object',chosen=chosen)
        for child in children:overrides[child]=child==chosen
    return overrides
