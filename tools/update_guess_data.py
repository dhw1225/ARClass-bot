#!/usr/bin/env python3
"""Build and validate guess_songs.json from public Wiki snapshots.

The runtime never contacts a Wiki.  This maintenance helper accepts cached
responses for reproducible builds and can download fresh responses with
``--refresh``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIKI_API = "https://arcaea.fandom.com/api.php"
EXPECTED_SONG_COUNT = 532
RENAMED_BYD = {
    "PRAGMATISM -RESURRECTION-": "PRAGMATISM",
    "Ignotus Afterburn": "Ignotus",
    "Axium Divergence": "Axium Crisis",
    "Red and Blue and Green": "Red and Blue",
    "Singularity VVVIP": "Singularity",
    "overdead.": "dropdead",
    "Vicious [ANTi] Heroism": "Vicious Heroism",
    "Last | Moment": "Last",
    "Last | Eternity": "Last",
}
MAIN_ACT_I = {
    "Eternal Core", "Vicious Labyrinth", "Luminous Sky", "Adverse Prelude",
    "Black Fate", "Final Verdict", "Silent Answer",
}
MAIN_ACT_II = {
    "Lasting Eden", "Lasting Eden Chapter 2", "Shifting Veil",
    "Absolute Nihil", "Lucent Historia", "Liminal Eclipse",
}
SIDE_STORY = {
    "Crimson Solace", "Ambivalent Vision", "Binary Enfold", "Shared Time",
    "Absolute Reason", "Sunset Radiance", "Ephemeral Page",
    "The Journey Onwards", "Esoteric Order", "Pale Tapestry",
    "Light of Salvation", "Divided Heart", "Extant Anima",
    "Chapter Experientia",
}
COLLAB_PREFIXES = (
    "Dynamix", "Lanota", "Tone Sphere", "Groove Coaster", "CHUNITHM",
    "O.N.G.E.K.I.", "Maimai", "maimai", "WACCA", "Muse Dash", "Cytus II",
    "Rotaeno", "UNDERTALE", "DJMAX", "MEGAREX",
)
SIDE_LABELS = {
    "Light": "光芒",
    "Conflict": "纷争",
    "Achromic": "无色",
    "Lephon": "Lephon",
}
LATEST_TITLES = (
    "Altersist|Cosmogyral|MIRROR - kamome sano remix|Riot in the System|"
    "C.s.q.n.|Spider Dance|Spear of Justice|ASGORE|flexidefine"
)
SPECIAL_PAGE_TO_CANONICAL = {
    "Genesis (Iris)": "Genesis (Tone Sphere)",
    "Genesis (Morrigan)": "Genesis (CHUNITHM)",
    "Quon (Feryquitous)": "Quon (Lanota)",
    "Quon (DJ Noriken)": "Quon (WACCA)",
    "Nέo κósmo": "nέο κόsmo",
    "Particle Arts": "光速神授説 - Divine Light of Myriad -",
    "Hikari": "光",
    "Hikari (Song)": "光",
    "Spear of Justice": "正義の槍",
    "Spider Dance": "スパイダーダンス",
    "ASGORE": "アズゴア",
    "~ +": "~_+",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char for char in value if char.isalnum())
    if normalized:
        return normalized
    return "".join(char for char in value if not char.isspace())


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[dict]]] = []
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "table":
            self.table = []
            self.tables.append(self.table)
        elif tag == "tr" and self.table is not None:
            self.row = []
            self.table.append(self.row)
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "links": []}
            self.row.append(self.cell)
        elif tag == "a" and self.cell is not None:
            title = data.get("title")
            if title:
                self.cell["links"].append(title)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.cell = None
        elif tag == "tr":
            self.row = None
        elif tag == "table":
            self.table = None

    def handle_data(self, data):
        value = " ".join(data.split())
        if value and self.cell is not None:
            self.cell["text"] = (self.cell["text"] + " " + value).strip()


def fetch_json(params: dict) -> dict:
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ARClass-data-updater/1.0"})
    last_error = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(attempt)
    raise RuntimeError(f"Wiki request failed after 4 attempts: {last_error}")


def load_or_fetch(path: Path, params: dict, refresh: bool) -> dict:
    if refresh or not path.exists():
        value = fetch_json(params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return value
    return json.loads(path.read_text(encoding="utf-8"))


def build_local_catalog():
    charts = json.loads((ROOT / "songs.json").read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for chart in charts:
        canonical = RENAMED_BYD.get(chart["name"], chart["name"])
        if canonical != "Last":
            grouped[canonical].append(chart)
    aliases = defaultdict(set)
    index = defaultdict(set)
    for title, items in grouped.items():
        index[normalize(title)].add(title)
        for item in items:
            if item["name"] != title:
                aliases[title].add(item["name"])
            for alias in item.get("aliases", []):
                aliases[title].add(alias)
                index[normalize(alias)].add(title)
    return grouped, aliases, index


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_checked_snapshot(grouped) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Strictly validate checked-in runtime data without network access."""

    errors: list[str] = []
    songs_path = ROOT / "guess_songs.json"
    aliases_path = ROOT / "guess_aliases.json"
    try:
        songs = json.loads(songs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read {songs_path.name}: {exc}"], {}
    try:
        manual_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read {aliases_path.name}: {exc}"], {}

    if not isinstance(songs, list):
        return ["guess_songs.json root must be a list"], {}
    if not isinstance(manual_aliases, dict):
        return ["guess_aliases.json root must be an object"], {}
    if len(songs) != EXPECTED_SONG_COUNT:
        errors.append(f"expected {EXPECTED_SONG_COUNT} songs, got {len(songs)}")
    if len(grouped) != EXPECTED_SONG_COUNT:
        errors.append(
            f"songs.json produces {len(grouped)} canonical songs, "
            f"expected {EXPECTED_SONG_COUNT}"
        )

    required_fields = {
        "title", "pack", "pack_type", "side", "bpm_min", "bpm_max",
        "constants", "year", "official_aliases",
    }
    required_constants = {"PST", "PRS", "FTR", "BYD", "ETR"}
    by_title = {}
    for position, item in enumerate(songs, 1):
        if not isinstance(item, dict):
            errors.append(f"row {position}: must be an object")
            continue
        title = item.get("title")
        label = title if isinstance(title, str) and title else f"row {position}"
        if set(item) != required_fields:
            errors.append(f"{label}: invalid fields")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"row {position}: invalid title")
            continue
        if title in by_title:
            errors.append(f"duplicate canonical title: {title}")
        by_title[title] = item
        for field in ("pack", "pack_type", "side"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{title}: invalid {field}")
        bpm_min, bpm_max = item.get("bpm_min"), item.get("bpm_max")
        if not _is_number(bpm_min) or bpm_min <= 0:
            errors.append(f"{title}: invalid bpm_min")
        if not _is_number(bpm_max) or bpm_max <= 0:
            errors.append(f"{title}: invalid bpm_max")
        if _is_number(bpm_min) and _is_number(bpm_max) and bpm_min > bpm_max:
            errors.append(f"{title}: bpm_min exceeds bpm_max")
        year = item.get("year")
        if not isinstance(year, int) or isinstance(year, bool) or not 2017 <= year <= 2100:
            errors.append(f"{title}: invalid year")

        constants = item.get("constants")
        if not isinstance(constants, dict) or set(constants) != required_constants:
            errors.append(f"{title}: invalid constants")
            constants = {}
        for difficulty in ("PST", "PRS", "FTR"):
            if not _is_number(constants.get(difficulty)):
                errors.append(f"{title}: missing {difficulty} constant")
        for difficulty in ("BYD", "ETR"):
            value = constants.get(difficulty)
            if value is not None and not _is_number(value):
                errors.append(f"{title}: invalid {difficulty} constant")
        if constants.get("BYD") is not None and constants.get("ETR") is not None:
            errors.append(f"{title}: has both BYD and ETR")

        expected = {
            chart["difficulty"]: chart["level"] for chart in grouped.get(title, ())
        }
        for difficulty in required_constants:
            if constants.get(difficulty) != expected.get(difficulty):
                errors.append(
                    f"{title}: {difficulty} constant differs from songs.json"
                )

        official_aliases = item.get("official_aliases")
        if not isinstance(official_aliases, list):
            errors.append(f"{title}: official_aliases must be a list")
        else:
            for alias in official_aliases:
                if not isinstance(alias, str) or not normalize(alias):
                    errors.append(f"{title}: invalid official alias")

    expected_titles = set(grouped)
    actual_titles = set(by_title)
    for title in sorted(expected_titles - actual_titles, key=str.casefold):
        errors.append(f"missing canonical title: {title}")
    for title in sorted(actual_titles - expected_titles, key=str.casefold):
        errors.append(f"unexpected canonical title: {title}")
    if any(title == "Last" or title.startswith("Last |") for title in actual_titles):
        errors.append("Last and its special BYD charts must be excluded")

    unknown_alias_titles = set(manual_aliases) - actual_titles
    for title in sorted(unknown_alias_titles, key=str.casefold):
        errors.append(f"manual aliases reference unknown title: {title}")

    lookup = defaultdict(set)
    for title, item in by_title.items():
        names = [title, *item.get("official_aliases", [])]
        manual = manual_aliases.get(title, [])
        if not isinstance(manual, list):
            errors.append(f"{title}: manual aliases must be a list")
            manual = []
        names.extend(manual)
        for alias in names:
            if not isinstance(alias, str) or not normalize(alias):
                errors.append(f"{title}: invalid lookup name")
                continue
            key = normalize(alias)
            lookup[key].add(title)

    for renamed, canonical in RENAMED_BYD.items():
        if canonical == "Last":
            continue
        item = by_title.get(canonical)
        if item is None:
            continue
        official_aliases = item.get("official_aliases")
        if not isinstance(official_aliases, list) or renamed not in official_aliases:
            errors.append(f"{canonical}: missing renamed BYD alias {renamed}")

    conflicts = {
        key: tuple(sorted(titles, key=str.casefold))
        for key, titles in lookup.items()
        if len(titles) > 1
    }
    return errors, conflicts


def resolve_title(names, pack, grouped, index):
    for name in names:
        if name in SPECIAL_PAGE_TO_CANONICAL:
            return SPECIAL_PAGE_TO_CANONICAL[name]
    if names and names[0] == "Genesis":
        return "Genesis (CHUNITHM)" if "CHUNITHM" in pack else "Genesis (Tone Sphere)"
    if names and names[0] == "Quon":
        return "Quon (WACCA)" if "WACCA" in pack else "Quon (Lanota)"
    candidates = set()
    for name in names:
        candidates.update(index.get(normalize(name), ()))
    if len(candidates) == 1:
        return candidates.pop()
    direct = [name for name in names if name in grouped]
    return direct[0] if len(direct) == 1 else None


def parse_bpm(value: str):
    numbers = [float(number) for number in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        raise ValueError(f"invalid BPM: {value!r}")
    return min(numbers), max(numbers)


def pack_type(pack: str) -> str:
    if pack in MAIN_ACT_I:
        return "Main Story Act I"
    if pack in MAIN_ACT_II:
        return "Main Story Act II"
    if pack in SIDE_STORY:
        return "Side Story"
    if pack == "Arcaea":
        return "Arcaea"
    if pack == "Memory Archive":
        return "Memory Archive"
    if "Extend Archive" in pack or "World Extend" in pack:
        return "World/Extend Archive"
    if (
        "Collaboration" in pack
        or pack in {"Arcaea Next Stage", "UNDERTALE Pack Append"}
        or pack.startswith(COLLAB_PREFIXES)
    ):
        return "Collaboration"
    raise ValueError(f"unknown pack category: {pack}")


def approximate_year(version: str) -> int:
    explicit = re.search(r"\((20\d{2})[-/]", version)
    if explicit:
        return int(explicit.group(1))
    match = re.search(r"(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"invalid version: {version!r}")
    major, minor = map(int, match.groups())
    if major == 1:
        return 2017 if minor <= 4 else 2018 if minor <= 8 else 2019
    if major == 2:
        return 2019 if minor <= 3 else 2020
    if major == 3:
        return 2020 if minor == 0 else 2021 if minor <= 8 else 2022
    if major == 4:
        return 2022 if minor <= 2 else 2023
    if major == 5:
        return 2023 if minor <= 3 else 2024
    if major == 6:
        return 2024 if minor <= 1 else 2025 if minor <= 9 else 2026
    raise ValueError(f"unknown version year: {version!r}")


def csv_years(path: Path, grouped, index):
    result = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            title = resolve_title([row.get("Title", "")], "", grouped, index)
            match = re.search(r"\((\d{4})/", row.get("Release", ""))
            if title and match:
                result[title] = int(match.group(1))
    return result


def checked_snapshot_years() -> dict[str, int]:
    """Preserve exact historical years when the Wiki table only has a version."""

    try:
        rows = json.loads((ROOT / "guess_songs.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        row["title"]: row["year"]
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("title"), str)
        and isinstance(row.get("year"), int)
        and not isinstance(row.get("year"), bool)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".guess-data-cache")
    parser.add_argument("--legacy-csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "guess_songs.json")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    grouped, aliases, index = build_local_catalog()

    # `--check` is deliberately offline so it is reliable in a minimal runtime
    # image. Combine it with `--refresh` to compare against fresh Wiki data.
    if args.check and not args.refresh:
        errors, conflicts = validate_checked_snapshot(grouped)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"validated {len(grouped)} songs")
        if conflicts:
            rendered_conflicts = "; ".join(
                f"{key} => {', '.join(titles)}"
                for key, titles in sorted(conflicts.items())
            )
            print(f"alias conflicts ({len(conflicts)}): {rendered_conflicts}")
        else:
            print("alias conflicts (0)")
        return 0

    date_data = load_or_fetch(
        args.cache_dir / "songs_by_date.json",
        {"action": "parse", "page": "Songs_by_Date", "prop": "text", "format": "json", "formatversion": 2},
        args.refresh,
    )
    table_parser = TableParser()
    table_parser.feed(date_data["parse"]["text"])
    rows = table_parser.tables[0]
    source = {}
    for row in rows[1:]:
        if len(row) < 12:
            continue
        names = [row[1]["text"], *row[1]["links"]]
        pack = row[10]["links"][0] if row[10]["links"] else row[10]["text"]
        if "Last" in names:
            continue
        title = resolve_title(names, pack, grouped, index)
        if not title:
            raise ValueError(f"unmatched Wiki row: {names!r} ({pack})")
        bpm_min, bpm_max = parse_bpm(row[9]["text"])
        source[title] = {"pack": pack, "bpm_min": bpm_min, "bpm_max": bpm_max, "version": row[11]["text"]}
        clean_names = row[1]["links"] or [row[1]["text"]]
        aliases[title].update(name for name in clean_names if name and name != title)

    latest = load_or_fetch(
        args.cache_dir / "latest_pages.json",
        {"action": "query", "titles": LATEST_TITLES, "prop": "revisions", "rvprop": "content", "rvslots": "main", "format": "json", "formatversion": 2},
        args.refresh,
    )
    for page in latest["query"]["pages"]:
        page_title = page["title"]
        title = resolve_title([page_title], "", grouped, index)
        if not title:
            raise ValueError(f"unmatched latest Wiki page: {page_title}")
        content = page["revisions"][0]["slots"]["main"]["content"]
        def field(name):
            match = re.search(rf"^\|{re.escape(name)}\s*=\s*(.+?)\s*$", content, re.MULTILINE | re.IGNORECASE)
            return match.group(1).strip() if match else ""
        pack = field("Mobile Pack")
        bpm_min, bpm_max = parse_bpm(field("BPM"))
        source[title] = {"pack": pack, "bpm_min": bpm_min, "bpm_max": bpm_max, "version": field("Version")}
        if page_title != title:
            aliases[title].add(page_title)

    sides = {}
    for side in SIDE_LABELS:
        side_data = load_or_fetch(
            args.cache_dir / f"side_{side.casefold()}.json",
            {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{side} Side Songs", "cmlimit": 500, "cmnamespace": 0, "format": "json", "formatversion": 2},
            args.refresh,
        )
        for member in side_data["query"]["categorymembers"]:
            title = resolve_title([member["title"]], "", grouped, index)
            if title and title != "Last":
                sides[title] = SIDE_LABELS[side]

    # Liminal Eclipse uses its own displayed attribute in the reference game,
    # although the Wiki also categorizes each chart as Light or Conflict.
    for background in ("conflict", "light"):
        eclipse_data = load_or_fetch(
            args.cache_dir / f"side_eclipse_{background}.json",
            {"action": "query", "list": "categorymembers", "cmtitle": f"Category:Eclipse {background} Background Songs", "cmlimit": 500, "cmnamespace": 0, "format": "json", "formatversion": 2},
            args.refresh,
        )
        for member in eclipse_data["query"]["categorymembers"]:
            title = resolve_title([member["title"]], "", grouped, index)
            if title and title != "Last":
                sides[title] = "绚寂"

    years = checked_snapshot_years()
    if args.legacy_csv:
        years.update(csv_years(args.legacy_csv, grouped, index))
    output = []
    errors = []
    for title in sorted(grouped, key=str.casefold):
        metadata = source.get(title)
        side = sides.get(title)
        charts = {chart["difficulty"]: chart["level"] for chart in grouped[title]}
        if not metadata or not side:
            errors.append(f"{title}: metadata={bool(metadata)}, side={side!r}")
            continue
        for required in ("PST", "PRS", "FTR"):
            if required not in charts:
                errors.append(f"{title}: missing {required}")
        byd = charts.get("BYD")
        etr = charts.get("ETR")
        if byd is not None and etr is not None:
            errors.append(f"{title}: has both BYD and ETR")
        explicit_year = re.search(r"\((20\d{2})[-/]", metadata["version"])
        year = (
            int(explicit_year.group(1))
            if explicit_year
            else years.get(title, approximate_year(metadata["version"]))
        )
        output.append({
            "title": title,
            "pack": metadata["pack"],
            "pack_type": pack_type(metadata["pack"]),
            "side": side,
            "bpm_min": metadata["bpm_min"],
            "bpm_max": metadata["bpm_max"],
            "constants": {"PST": charts.get("PST"), "PRS": charts.get("PRS"), "FTR": charts.get("FTR"), "BYD": byd, "ETR": etr},
            "year": year,
            "official_aliases": sorted(
                aliases[title] - {title}, key=lambda value: (value.casefold(), value)
            ),
        })
    if len(output) != EXPECTED_SONG_COUNT:
        errors.append(f"expected {EXPECTED_SONG_COUNT} songs, got {len(output)}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    output_path = args.output
    if args.check:
        if not output_path.exists() or output_path.read_text(encoding="utf-8") != rendered:
            print("guess_songs.json is out of date", file=sys.stderr)
            return 1
    else:
        output_path.write_text(rendered, encoding="utf-8")
    print(f"validated {len(output)} songs")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"guess data update failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
