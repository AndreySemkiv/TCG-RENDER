"""Interactive CMD workflow over a selected, verified local game source.

The menu does not capture screens, download card images or change emulator
settings. Discovery/import and batch execution are injectable for testing.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import uuid
from .progress import error_details

from .config import CODE_ROOT, PROJECT_ROOT


BANNER = r"""
 _____    ____    ____     ____    _____   _   _   ____    _____   ____  
|_   _|  / ___|  / ___|   |  _ \  | ____| | \ | | |  _ \  | ____| |  _ \ 
  | |   | |     | |  _    | |_) | |  _|   |  \| | | | | | |  _|   | |_) |
  | |   | |___  | |_| |   |  _ <  | |___  | |\  | | |_| | | |___  |  _ < 
  |_|    \____|  \____|   |_| \_\ |_____| |_| \_| |____/  |_____| |_| \_\
"""

REQUIREMENTS = """Before rendering:
  - Keep the BlueStacks instance with Pocket installed running during import.
  - In Settings > Advanced, enable Android Debug Bridge (ADB).
  - Finish downloading additional resources in Pocket before importing.
"""


@dataclass
class SourceSession:
    root: Path
    version: str = "unknown"
    description: str = "prepared local files"
    warnings: list = field(default_factory=list)


def _session(value):
    if isinstance(value, SourceSession):
        return value
    if isinstance(value, (str, Path)):
        return SourceSession(Path(value).resolve())
    if not isinstance(value, dict) or not (value.get("root") or value.get("data_root")):
        raise ValueError("The importer did not return a prepared data folder.")
    if value.get("prepared") is False:
        raise ValueError("Files were copied, but preparation has not finished.")
    return SourceSession(Path(value.get("root") or value["data_root"]).resolve(),
                         str(value.get("version") or "unknown"),
                         str(value.get("description") or value.get("kind") or "local files"),
                         list(value.get("warnings") or []))


def _path(value):
    return Path(os.path.expandvars(value.strip().strip('"'))).expanduser()


def _source_label(source):
    values = [source.get("instance"), source.get("serial"), source.get("location")]
    return " | ".join(str(v) for v in values if v) or source.get("kind", "local source")


def default_resolve_source(value, input_fn=input, output=print):
    """Discover one source and pass raw snapshots through the preparation stage."""
    from .bluestacks import discover_sources, import_source
    automatic = value.strip().casefold() in ("a", "s")
    sources = discover_sources(None if automatic else _path(value))
    if not sources:
        raise ValueError("No accessible installation found. Check the running instance and ADB, or enter an extracted files folder.")
    selected = sources[0]
    if len(sources) > 1:
        output("\nAvailable instances/sources:")
        for index, source in enumerate(sources, 1):
            output("  %d. %s" % (index, _source_label(source)))
        while True:
            choice = input_fn("Source number (0 = back): ").strip()
            if choice == "0":
                raise ValueError("Source selection cancelled.")
            if choice.isdigit() and 1 <= int(choice) <= len(sources):
                selected = sources[int(choice)-1]
                break
            output("Select a number from the list.")
    run_id = uuid.uuid4().hex[:8]
    # Keep decoded Windows paths short; card resource names can be very long.
    from .progress import ProgressDisplay
    with ProgressDisplay(output) as progress:
        result = import_source(selected, CODE_ROOT.parent/"tcg-data"/run_id, progress=progress)
        if not result.get("prepared"):
            from .materializer import prepare_import
            result = prepare_import(result, progress=progress)
    summary = result.get('inventory_summary')
    if summary:
        output('Source recognized: %d collections, %d card entries, %d large models available.' %
               (summary['collections'], summary['cards'], summary['large_prefabs_present']))
        changes = summary.get('changes')
        if changes:
            output('Changes since the last import: %d new collections, %d new cards, %d changed cards.' %
                   (len(changes['added_collections']), len(changes['added_cards']), len(changes['changed_cards'])))
    return _session(result)


def collections(catalog, root, language="en-US"):
    """List every collection in the selected original master, with real counts."""
    from .localization import LocaleRepository
    locale = LocaleRepository(root, language)
    grouped = {}
    for card in catalog.list_cards():
        grouped.setdefault(card["expansion"], []).append(card)
    result = []
    for expansion in catalog.table("Expansion"):
        expansion_id = expansion["ExpansionID"]
        name = expansion_id
        for name_key in (expansion.get("LongNameMSID"), expansion.get("NameMSID")):
            if not name_key:
                continue
            try:
                localized, _ = locale.lookup("Master", name_key, allow_empty=True)
                if localized:
                    name = locale.resolve_references(localized) if '[Mst:' in localized else localized
                    break
            except (ValueError, FileNotFoundError):
                pass
        cards = grouped.get(expansion_id, [])
        ready = sum(bool(c.get("supported")) for c in cards)
        native = sum(bool(c.get('native_available')) for c in cards)
        available = sum(bool(c.get('exportable',c.get('supported'))) for c in cards)
        result.append({"id":expansion_id, "name":name, "total":len(cards),
                       "supported":ready, "pending":len(cards)-available,
                       "available":available, "native":native,
                       "recency":expansion.get("RecencyScore", 0)})
    return sorted(result, key=lambda row:(-row["recency"], row["id"]))


def _choose_collection(rows, input_fn, output):
    output("\nCollections in the selected installation:")
    output("New collections appear after updating/importing the game files.")
    output("This list reflects the selected installation.")
    for index, row in enumerate(rows, 1):
        output("  %2d. %-9s %s | %d cards, %d eligible for export, %d with native resources, %d pending" %
               (index, row["id"], row["name"], row["total"], row["supported"],row.get('native',0), row["pending"]))
    while True:
        value = input_fn("Collection number or ID (0 = back): ").strip()
        if value == "0":
            return None
        if value.isdigit() and 1 <= int(value) <= len(rows):
            return rows[int(value)-1]
        found = next((r for r in rows if r["id"].casefold() == value.casefold()), None)
        if found:
            return found
        output("Collection not found. Enter a number or ID from the list.")


def _choose_language(input_fn, output):
    from .config import normalize_language
    output("\nLanguage: 1. English (default)  2. Portuges(Brazil)  3. Japanese  4. All three")
    choices = {"":"en-US", "1":"en-US", "2":"pt-BR", "3":"ja-JP", "4":"all", "all":"all"}
    while True:
        value = input_fn("Language [Enter = English, 0 = back]: ").strip()
        if value == "0": return None
        if value in choices: return choices[value]
        try: return normalize_language(value)
        except ValueError: output("Choose 1, 2, 3 or 4.")


def run_menu(source_resolver=None, catalog_factory=None, runner=None,
             input_fn=input, output=print, interactive=None):
    """Run the terminal workflow. No GPU execution occurs until Export is chosen."""
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda:False)())
    if not interactive:
        output("This menu requires an interactive terminal. Open start.cmd or use render_cards.py --help.")
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf8", errors="replace")
    if catalog_factory is None:
        from .catalog import Catalog
        catalog_factory = Catalog
    if runner is None:
        from .cli import main
        runner = main
    if source_resolver is None:
        source_resolver = lambda value:default_resolve_source(value, input_fn, output)
    output(BANNER)
    output(REQUIREMENTS)
    if (PROJECT_ROOT/"input/master-cache-decoded/MasterMemory.bytes").is_file():
        output("Existing prepared data: " + str(PROJECT_ROOT))
    current = catalog = None
    try:
        while True:
            if current is None:
                value = input_fn("\nBlueStacks/TCG files folder, A to search automatically, or 0 to exit: ").strip()
                if value == "0":
                    return 0
                if not value:
                    output("Enter a folder or press A to search automatically.")
                    continue
                try:
                    selected = _session(source_resolver(value))
                    selected_catalog = catalog_factory(selected.root)
                    selected_catalog.list_cards()
                    current, catalog = selected, selected_catalog
                except (ValueError, OSError, RuntimeError, ImportError) as error:
                    output("Could not prepare the source: " + error_details(error))
                    continue
                output("\nSource: %s | version: %s" % (current.description, current.version))
                output("Workspace: " + str(current.root))
                for warning in current.warnings:
                    if warning and "perfil de layouts" not in str(warning):
                        output("Warning: " + str(warning))
            output("\n1. Export a collection as transparent PNGs")
            output("2. Update/import or choose another source")
            output("0. Exit")
            action = input_fn("Option: ").strip()
            if action == "0":
                return 0
            if action == "2":
                current = catalog = None
                continue
            if action != "1":
                output("Choose a menu option.")
                continue
            try:
                rows = collections(catalog, current.root)
                if not rows:
                    output("No collections found in this source.")
                    continue
                collection = _choose_collection(rows, input_fn, output)
                if collection is None:
                    continue
                if collection['supported'] == 0:
                    output("This collection has no supported large models available for export.")
                    continue
                if collection["pending"]:
                    output("Resources available for %d of %d cards. The %d pending cards will be recorded in the report; the collection will be incomplete." %
                           (collection.get('available',collection["supported"]), collection["total"], collection["pending"]))
                language = _choose_language(input_fn, output)
                if language is None:
                    continue
                quality = "large"
                default_output = current.root/"renders"
                destination = input_fn("Output folder [Enter = %s]: " % default_output).strip()
                destination = _path(destination).resolve() if destination else default_output
                command = "render"
                arguments = ["--root", str(current.root), command, "--expansion", collection["id"],
                             "--lang", language, "--output", str(destination), '--quality',quality]
                output("\n%s %s (%s)..." % ("Exporting" if command == "render" else "Preparing", collection["id"], language))
                status = runner(arguments)
                from .export_paths import metadata_root
                output("Finished. Reports and JSON: " + str(metadata_root(destination)))
                if status:
                    output("Batch returned code %s; check the report for failed or unsupported cards." % status)
            except (ValueError, OSError, RuntimeError) as error:
                output("Could not complete the operation: " + error_details(error))
    except (KeyboardInterrupt, EOFError):
        output("\nMenu closed.")
        return 130


if __name__ == "__main__":
    raise SystemExit(run_menu())
