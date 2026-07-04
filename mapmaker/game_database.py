"""Game database — NPC/hero character database and model directory scanner.

Parses npcstat.engb and herostat.engb (XMLB binary) to build a searchable
character list. Scans the game's models/ directory for a browsable asset library.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Character info
# ---------------------------------------------------------------------------

@dataclass
class CharacterInfo:
    """Parsed character entry from npcstat or herostat."""
    name: str = ""                  # Internal name (stats element 'name')
    charactername: str = ""         # Display name ('charactername')
    team: str = ""                  # 'hero', 'enemy', etc.
    skin: str = ""                  # Skin code -> actors/{skin}.igb
    characteranims: str = ""        # Animation set
    level: int = 0                  # Character level
    dangerrating: int = 0           # Danger rating (enemies)
    source: str = ""                # 'herostat' or 'npcstat'
    extra_fields: Dict = field(default_factory=dict)  # All additional fields (skin_*, etc.)


# ---------------------------------------------------------------------------
# Model info
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """A model file discovered in the game's models/ directory."""
    rel_path: str = ""              # Relative path (no ext), e.g. "sanctuary/bridge_spire_san_e"
    category: str = ""              # Subdirectory, e.g. "sanctuary"
    display_name: str = ""          # Filename, e.g. "bridge_spire_san_e"


# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

_char_db_cache: Dict[str, CharacterInfo] = {}
_char_db_game_dir: str = ""

_model_db_cache: List[ModelInfo] = []
_model_db_game_dir: str = ""


# ---------------------------------------------------------------------------
# Character database loading
# ---------------------------------------------------------------------------

def _parse_stats_elements(root, source_name: str) -> Dict[str, CharacterInfo]:
    """Parse <stats ...> elements under <characters> root."""
    chars = {}
    for child in root:
        if child.tag != "stats":
            continue
        name = child.get("name", "")
        if not name:
            continue

        # Collect extra fields (skin_*, etc.)
        extra = {}
        for attr_name, attr_val in child.attrib.items():
            if attr_name.startswith("skin_"):
                extra[attr_name] = attr_val

        info = CharacterInfo(
            name=name,
            charactername=child.get("charactername", name),
            team=child.get("team", ""),
            skin=child.get("skin", ""),
            characteranims=child.get("characteranims", ""),
            level=_safe_int(child.get("level", "0")),
            dangerrating=_safe_int(child.get("dangerrating", "0")),
            source=source_name,
            extra_fields=extra,
        )
        chars[name] = info
    return chars


def _safe_int(val: str) -> int:
    """Safely parse an integer, returning 0 on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _read_stat_root(stat_path: Path):
    """Read a stat file as an ElementTree root, handling BOTH compiled XMLB
    (.engb — XML2/MUA PC) and plain-text XML (.eng — XML1 console decompiles).

    XML1's npcstat.eng/herostat.eng are the SAME <characters><stats .../>
    schema as XML2's compiled tables, just uncompiled text, so once we have a
    root the existing _parse_stats_elements handles either identically.
    """
    with open(stat_path, "rb") as f:
        head = f.read(16)
    # first non-whitespace byte '<' => plain-text XML. Strip a leading UTF-8
    # BOM too, else a BOM'd .eng is misrouted to the compiled-XMLB reader and
    # silently drops the whole table.
    if head.lstrip(b"\xef\xbb\xbf \t\r\n\x0b\x0c")[:1] == b"<":
        # plain-text XML (XML1). Game .eng dumps can carry stray/unescaped
        # bytes or odd encoding declarations, so recover via a lenient tag scan
        # on ANY strict-parse failure (ParseError, bad-encoding LookupError, …).
        import xml.etree.ElementTree as ET
        try:
            return ET.parse(str(stat_path)).getroot()
        except Exception:
            return _lenient_stats_root(stat_path)
    from .xmlb_compile import decompile_xmlb
    return decompile_xmlb(stat_path)


def _lenient_stats_root(stat_path: Path):
    """Fallback for malformed plain-text stat XML: synthesize a <characters>
    root from each `<stats ...>` opening tag via regex, ignoring nested
    children and unescaped-entity noise that strict ElementTree rejects."""
    import re
    import xml.etree.ElementTree as ET
    text = open(stat_path, encoding="cp1252", errors="replace").read()
    root = ET.Element("characters")
    for m in re.finditer(r"<stats\b([^>]*?)/?>", text):
        el = ET.SubElement(root, "stats")
        for am in re.finditer(r'([\w]+)\s*=\s*"([^"]*)"', m.group(1)):
            el.set(am.group(1), am.group(2))
    return root


def load_character_db(game_data_dir: str) -> Dict[str, CharacterInfo]:
    """Load character database from npcstat + herostat.

    Prefers the compiled English binary tables (npcstat.engb/herostat.engb —
    XML2/MUA PC) and falls back to the plain-text .eng tables (XML1 console
    decompiles), which share the same <stats> schema.

    Args:
        game_data_dir: Root game data directory (contains data/ subfolder)

    Returns:
        dict mapping character name -> CharacterInfo
    """
    chars: Dict[str, CharacterInfo] = {}
    data_dir = Path(game_data_dir) / "data"

    for source, candidates in [("npcstat", ["npcstat.engb", "npcstat.eng"]),
                               ("herostat", ["herostat.engb", "herostat.eng"])]:
        for fname in candidates:
            stat_path = data_dir / fname
            if not stat_path.exists():
                continue
            try:
                root = _read_stat_root(stat_path)
                chars.update(_parse_stats_elements(root, source))
            except Exception as e:
                print(f"[MapMaker] Warning: Failed to parse {fname}: {e}")
            break  # first existing candidate per source wins

    return chars


def get_character_db(game_data_dir: str, force_reload: bool = False) -> Dict[str, CharacterInfo]:
    """Get cached character database, loading if needed."""
    global _char_db_cache, _char_db_game_dir

    if force_reload or game_data_dir != _char_db_game_dir or not _char_db_cache:
        _char_db_cache = load_character_db(game_data_dir)
        _char_db_game_dir = game_data_dir

    return _char_db_cache


def get_skin_actor_path(skin: str, game_data_dir: str) -> Optional[str]:
    """Return full path to actor IGB file for a given skin code.

    Args:
        skin: Skin code (e.g. '0303')
        game_data_dir: Root game data directory

    Returns:
        Full path to actors/{skin}.igb, or None if not found
    """
    if not skin:
        return None
    actor_path = Path(game_data_dir) / "actors" / f"{skin}.igb"
    if actor_path.exists():
        return str(actor_path)
    return None


def search_characters(db: Dict[str, CharacterInfo], query: str) -> List[CharacterInfo]:
    """Search character database by name/charactername (case-insensitive)."""
    if not query:
        return sorted(db.values(), key=lambda c: c.charactername or c.name)

    query_lower = query.lower()
    results = []
    for char in db.values():
        if (query_lower in char.name.lower() or
                query_lower in char.charactername.lower() or
                query_lower in char.team.lower()):
            results.append(char)

    return sorted(results, key=lambda c: c.charactername or c.name)


def get_all_skin_codes(char_info: CharacterInfo) -> List[Tuple[str, str]]:
    """Get all skin variant codes for a character.

    Combines the default skin with all skin_XXX variant fields.

    Args:
        char_info: CharacterInfo instance.

    Returns:
        List of (variant_name, skin_code) tuples, e.g.,
        [("default", "0303"), ("70s", "0321"), ("aoa", "0302")].
    """
    skins = []

    if not char_info.skin:
        return skins

    skins.append(("default", char_info.skin))

    # Extract character ID prefix from the default skin code
    # Skin codes are CCDD where CC is the character ID
    # The CC part length varies (2 digits for IDs < 100, more for higher)
    char_id = char_info.skin[:-2] if len(char_info.skin) > 2 else char_info.skin

    for key, val in char_info.extra_fields.items():
        if key.startswith("skin_"):
            variant_name = key[5:]
            # val is the variant suffix (e.g., "21" for skin_70s = 21)
            variant_suffix = str(val).zfill(2)
            full_code = char_id + variant_suffix
            skins.append((variant_name, full_code))

    return skins


# ---------------------------------------------------------------------------
# Model directory scanner
# ---------------------------------------------------------------------------

def scan_models_dir(game_data_dir: str) -> List[ModelInfo]:
    """Scan game's models/ directory for all .igb model files.

    Args:
        game_data_dir: Root game data directory

    Returns:
        List of ModelInfo entries sorted by category, then display name
    """
    models_dir = Path(game_data_dir) / "models"
    if not models_dir.exists():
        return []

    models = []
    for dirpath, dirnames, filenames in os.walk(models_dir):
        rel_dir = Path(dirpath).relative_to(models_dir)
        category = str(rel_dir) if str(rel_dir) != "." else "root"

        for filename in filenames:
            if not filename.lower().endswith(".igb"):
                continue
            stem = filename[:-4]  # Remove .igb
            rel_path = str(rel_dir / stem) if str(rel_dir) != "." else stem
            # Normalize path separators
            rel_path = rel_path.replace("\\", "/")
            category_clean = category.replace("\\", "/")

            models.append(ModelInfo(
                rel_path=rel_path,
                category=category_clean,
                display_name=stem,
            ))

    models.sort(key=lambda m: (m.category, m.display_name))
    return models


def get_model_db(game_data_dir: str, force_reload: bool = False) -> List[ModelInfo]:
    """Get cached model database, loading if needed."""
    global _model_db_cache, _model_db_game_dir

    if force_reload or game_data_dir != _model_db_game_dir or not _model_db_cache:
        _model_db_cache = scan_models_dir(game_data_dir)
        _model_db_game_dir = game_data_dir

    return _model_db_cache


def get_model_categories(models: List[ModelInfo]) -> List[str]:
    """Get sorted list of unique categories from model list."""
    categories = set()
    for m in models:
        categories.add(m.category)
    return sorted(categories)


def search_models(models: List[ModelInfo], query: str, category: str = "") -> List[ModelInfo]:
    """Search/filter model database.

    Args:
        models: Full model list
        query: Search text (case-insensitive, matches display_name)
        category: Category filter (empty = all)

    Returns:
        Filtered list of ModelInfo
    """
    results = models
    if category:
        results = [m for m in results if m.category == category]
    if query:
        query_lower = query.lower()
        results = [m for m in results if query_lower in m.display_name.lower()]
    return results
