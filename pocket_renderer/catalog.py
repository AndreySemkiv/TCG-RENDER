"""Card identifiers and data from the verified local Pocket master database.

No game process, network service, translated catalogue or image manipulation is
used. Tables are read from the original decoded MessagePack/LZ4 master rather
than trusting editable per-table JSON exports.
"""
from pathlib import Path
import hashlib
import json
import re
import struct


def project_root(root):
    path = Path(root).resolve()
    if (path / 'pocket_renderer').is_dir() and (path.parent / 'pocket-extraction/input').is_dir():
        return path.parent / 'pocket-extraction'
    return path / "pocket-extraction" if (path / "pocket-extraction/input").is_dir() else path


class CatalogError(ValueError):
    """An identifier or original database record could not be resolved."""


class _MessagePack:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, length):
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        if len(value) != length:
            raise CatalogError("Truncated original master MessagePack")
        return value

    def mapping(self, count):
        result = {}
        for _ in range(count):
            key = self.value()
            result[key] = self.value()
        return result

    def value(self):
        tag = self.read(1)[0]
        if tag < 128:
            return tag
        if tag >= 224:
            return tag - 256
        if 160 <= tag < 192:
            return self.read(tag & 31).decode("utf8")
        if 144 <= tag < 160:
            return [self.value() for _ in range(tag & 15)]
        if 128 <= tag < 144:
            return self.mapping(tag & 15)
        if tag == 192:
            return None
        if tag in (194, 195):
            return tag == 195
        formats = {202: ">f", 203: ">d", 204: ">B", 205: ">H", 206: ">I", 207: ">Q", 208: ">b", 209: ">h", 210: ">i", 211: ">q"}
        if tag in formats:
            fmt = formats[tag]
            return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]
        widths = {196: 1, 197: 2, 198: 4, 217: 1, 218: 2, 219: 4, 220: 2, 221: 4, 222: 2, 223: 4, 199: 1, 200: 2, 201: 4}
        if tag in widths:
            length = int.from_bytes(self.read(widths[tag]), "big")
            if tag in (196, 197, 198):
                return self.read(length)
            if tag in (217, 218, 219):
                return self.read(length).decode("utf8")
            if tag in (220, 221):
                return [self.value() for _ in range(length)]
            if tag in (222, 223):
                return self.mapping(length)
            return {"extension": int.from_bytes(self.read(1), "big", signed=True), "body": self.read(length)}
        if 212 <= tag <= 216:
            length = (1, 2, 4, 8, 16)[tag - 212]
            return {"extension": int.from_bytes(self.read(1), "big", signed=True), "body": self.read(length)}
        raise CatalogError("Unsupported MessagePack tag %s" % hex(tag))


ENERGY_NAMES = {0: "UNSPECIFIED", 1: "Colorless", 2: "Grass", 3: "Fire", 4: "Water", 5: "Lightning", 6: "Psychic", 7: "Fighting", 8: "Darkness", 9: "Metal", 10: "Dragon"}


class Catalog:
    def __init__(self, root):
        self.root = project_root(root)
        self.master_path = self.root / "input/master-cache-decoded/MasterMemory.bytes"
        self._data = self.master_path.read_bytes()
        self.master_sha256 = hashlib.sha256(self._data).hexdigest()
        provenance_file = self.master_path.parent / "PROVENANCE.json"
        provenance = json.loads(provenance_file.read_text(encoding="utf8"))
        expected = next((v["sha256"] for v in provenance if Path(v["output"].replace("\\", "/")).name == self.master_path.name), None)
        if not expected or self.master_sha256 != expected:
            raise CatalogError("Original master SHA256 does not match its verified extraction provenance")
        reader = _MessagePack(self._data)
        self._header = reader.value()
        self._base = reader.pos
        self._tables, self._indexes = {}, {}
        self._cards = None
        self._aliases = None

    def table(self, name):
        if name not in self._tables:
            if name not in self._header:
                raise CatalogError("Original master table not found: " + name)
            offset, size = self._header[name]
            raw = self._data[self._base + offset:self._base + offset + size]
            reader = _MessagePack(raw)
            rows = reader.value()
            if reader.pos != len(raw):
                raise CatalogError("Trailing data in original table " + name)
            if isinstance(rows, dict) and rows.get("extension") == 99:
                import lz4.block
                compressed = _MessagePack(rows["body"])
                length = compressed.value()
                decoded = lz4.block.decompress(rows["body"][compressed.pos:], uncompressed_size=length)
                if len(decoded) != length:
                    raise CatalogError("Incorrect LZ4 table length: " + name)
                reader = _MessagePack(decoded)
                rows = reader.value()
                if reader.pos != len(decoded):
                    raise CatalogError("Trailing decoded table data: " + name)
            if not isinstance(rows, list):
                raise CatalogError("Expected original row list for " + name)
            self._tables[name] = rows
        return self._tables[name]

    def _index(self, table, key):
        if table not in self._indexes:
            self._indexes[table] = {v[key]: v for v in self.table(table)}
        return self._indexes[table]

    def _build(self):
        if self._cards is not None:
            return
        pokemon_cards = self._index("PokemonCard", "CardID")
        trainer_cards = self._index("TrainerCard", "CardID")
        trainers = self._index('Trainer', 'TrainerID')
        pokemon = self._index("Pokemon", "PokemonID")
        attacks = self._index("PokemonAttack", "PokemonAttackID")
        attack_names = self._index("PokemonAttackName", "PokemonAttackNameID")
        characters = self._index("Character", "CharacterID")
        pokedex = self._index("Pokedex", "ID")
        expansions = self._index("Expansion", "ExpansionID")
        cards = []
        for collection in self.table("ExpansionCollectionNumber"):
            card_id, expansion = collection["CardID"], collection["ExpansionID"]
            raw_card = pokemon_cards.get(card_id) or trainer_cards.get(card_id)
            if not raw_card:
                raise CatalogError('Original collection entry references an unsupported or missing card record: '
                                   + str(expansion) + ' / ' + str(card_id))
            mon = pokemon.get(raw_card.get("PokemonID"))
            trainer = trainers.get(raw_card.get('TrainerID')) if not mon else None
            character = characters.get((mon or trainer or {}).get('CharacterID'))
            raw_attacks = [attacks[k] for k in mon.get("PokemonAttackIDs", [])] if mon else []
            raw_attack_names = [attack_names.get(v.get("PokemonAttackNameID"), {}) for v in raw_attacks]
            dex = pokedex.get(raw_card.get("PokedexID"), {})
            exp = expansions.get(expansion, {})
            number = int(collection["CollectionNumber"])
            public_id = "%s-%03d" % (expansion, number)
            reasons = []
            if not mon and not (trainer and trainer.get('TrainerType') in (1,2,3,4,5)):
                reasons.append("trainer-layout-not-supported")
            if mon and len(mon.get("PokemonTypes", [])) != 1:
                reasons.append("multiple-energy-types-not-supported")
            # Native Master Rarity.IM=800 uses the recovered PokemonCardUI base and Text/Holo
            # targets, with its own CardSettings-selected font/material groups.
            # This enables the original static card face, not its movie player.
            # S=830 now reconstructs the source KiraPuyo/CircularKira initial
            # Awake frame. Actual scene contracts (MPB/UVs/arrays/rotation)
            # remain validated during preparation; later animation is separate.
            if mon and raw_card.get("Rarity", 0) not in (100, 200, 300,400,800,830) and not raw_card.get('IllustrationID','').endswith(('_AR','_SR','_SAR','_UR','_SSR')):
                reasons.append("special-rarity-layout-not-supported")
            # Mirror artwork has its own original L prefab/materials. Let the
            # actual scene compiler validate them instead of rejecting the
            # family before inspecting its unchanged shader/resource bindings.
            if mon and raw_card.get('MirrorType', 0) not in (0, 1, 2):
                reasons.append('unknown-original-mirror-type')
            artist_keys = list(raw_card.get("IllustratorNameMSIDs", []))
            row = {"id": public_id, "card_id": card_id, "asset_id": raw_card.get("IllustrationID", ""),
                   "expansion": expansion, "number": number, "collection_total": exp.get("CollectionNumber", ""),
                   "name_key": (character or {}).get("DisplayNameMSID", ""),
                   "attack_name_keys": [v.get("NameMSID", "") for v in raw_attack_names],
                   "flavor_key": raw_card.get("FlavorTextMSID", ""), "artist_key": artist_keys[0] if artist_keys else "",
                   "artist_keys": artist_keys, "dex_key": dex.get("SpecMSID", ""),
                   "card": raw_card, "pokemon": mon, "trainer": trainer, "attacks": raw_attacks, "attack_names": raw_attack_names,
                   "character": character, "pokedex": dex, "supported": not reasons, "unsupported_reasons": reasons,
                   "profile": "pokemon-window-v1" if not reasons else None,
                   "provenance": {"authority": "original-local-master", "source": str(self.master_path), "sha256": self.master_sha256},
                   "raw_records": {"PokemonCard" if mon else "TrainerCard": raw_card, "Pokemon": mon,
                                   "PokemonAttack": raw_attacks, "PokemonAttackName": raw_attack_names, "Character": character,
                                   "Pokedex": dex, "ExpansionCollectionNumber": collection, "Expansion": exp}}
            row["data_supported"] = not reasons
            if not reasons:
                # Keep CLI eligibility aligned with the source-bound UI subset,
                # rather than advertising a mesh-only success as a full card.
                from .layout import validate_layout_profile
                from .localization import UnsupportedLayout
                try:
                    if mon:validate_layout_profile(row)
                    else:
                        from .trainer_ui import validate_trainer
                        validate_trainer(row)
                except UnsupportedLayout as exc:
                    reasons.append("unsupported-layout: " + str(exc))
                    row["supported"] = False
                    row["profile"] = None
            # Large layout support and stored native presentation are distinct.
            # File availability is only a preflight; shader/mesh provenance is
            # checked again during preparation, including for future imports.
            from .native_card import native_availability
            native = native_availability(self.root,row)
            row['native_available'] = native['available']
            row['exportable'] = row['supported'] or native['available']
            row['large_supported'] = row['supported']
            row['native_preflight'] = {key:value for key,value in native.items() if key != 'paths'}
            cards.append(row)
        cards.sort(key=lambda c: (expansions.get(c["expansion"], {}).get("SortOrderPriorityFromB1", 0), c["expansion"], c["number"]))
        aliases = {}
        for card in cards:
            for alias in (card["id"], card["id"].replace("-", "_"), card["card_id"], card["asset_id"]):
                aliases.setdefault(alias.casefold(), []).append(card)
        self._cards, self._aliases = cards, aliases

    def list_cards(self, expansion=None):
        self._build()
        return [c for c in self._cards if expansion is None or c["expansion"].casefold() == expansion.casefold()]

    def resolve(self, selector):
        self._build()
        normalized = str(selector).strip()
        match = re.match(r"^([A-Za-z][A-Za-z0-9-]*?)[_-](\d+)$", normalized)
        if match and not normalized.startswith(("PK_", "TR_", "cPK_", "cTR_")):
            normalized = "%s-%03d" % (match[1], int(match[2]))
        rows = self._aliases.get(normalized.casefold())
        if not rows:
            raise CatalogError("Card not found in the original master: " + str(selector))
        # Card and art IDs may occur in several expansions. The canonical first
        # expansion is deterministic; use an explicit expansion-number ID when
        # the user needs a particular reprint collection number.
        return rows[0]
