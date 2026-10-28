"""Source FontGroupConditions.GetFontGroup first-match selection.

Zero flags skip entries. Pokemon data tests energy bit1. The inspected newer
matcher also tests Dual bit2; callers select this behavior from the current
native contract. Trainer data bypasses both Pokemon predicates.
"""


def select_font_condition(conditions, pokemon_energy=None, *, pokemon_dual=False, dual_predicate=False):
    """Return the actual selected source record, or None for the default PPtr."""
    for condition in conditions:
        flags=condition['_conditionType']
        if not flags:
            continue
        if (pokemon_energy is None or
                ((not flags & 1 or condition['_energyType']==pokemon_energy) and
                 (not dual_predicate or not flags & 2 or bool(condition.get('_isDual', False))==pokemon_dual))):
            return condition
    return None
