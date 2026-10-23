"""Original FontGroupConditions.GetFontGroup selection, ARM64 1.7.5.

The native loop at 0x480B140..0x480B184 returns the first matching condition.
Zero flags skip an entry. For Pokemon data, bit zero tests its energy enum;
other bits do not add tests in this inspected binary. Non-Pokemon data bypasses
the energy test. Unknown native binaries still require their own source profile.
"""


def select_font_condition(conditions, pokemon_energy=None):
    """Return the actual selected source record, or None for the default PPtr."""
    for condition in conditions:
        flags=condition['_conditionType']
        if not flags:
            continue
        if pokemon_energy is None or not flags & 1 or condition['_energyType']==pokemon_energy:
            return condition
    return None
