#!/usr/bin/env python3
"""Build and validate player aliases for the `/guess` game.

Community aliases are imported from the Arcaea Chinese Wiki MediaWiki export.
The checked-in manual alias file is read for validation but is never rewritten.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TITLE = "User:WYXkk/曲目常见别称"
SOURCE_URL = "https://wiki.arcaea.cn/index.php/Special:Export"
SOURCE_PAGE_URL = (
    "https://wiki.arcaea.cn/index.php/User:WYXkk/曲目常见别称"
)
RESERVED_ALIASES = {"status", "cancel", "reset", "finish", "score"}
INITIALISM_STOPWORDS = {
    "a", "an", "and", "edit", "feat", "ft", "in", "of", "on", "remix",
    "rmx", "the", "to", "version", "d", "ll", "re", "s", "t", "ve",
}
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
SPECIAL_TITLES = {
    "Genesis (Iris)": "Genesis (Tone Sphere)",
    "Genesis (Morrigan)": "Genesis (CHUNITHM)",
    "Quon (Feryquitous)": "Quon (Lanota)",
    "Quon (DJ Noriken)": "Quon (WACCA)",
    "Divine Light of Myriad": "光速神授説 - Divine Light of Myriad -",
    "Particle Arts": "光速神授説 - Divine Light of Myriad -",
    "Hikari": "光",
    "Spear of Justice": "正義の槍",
    "Spider Dance": "スパイダーダンス",
    "ASGORE": "アズゴア",
}
EXPECTED_UNKNOWN_SOURCE_TITLES = {
    "Mistempered Malignance", "0xe0e1ccull", "HIVEMIND INTERLINKED",
    "Live Faster Die Younger",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char for char in value if char.isalnum())
    return normalized or "".join(char for char in value if not char.isspace())


def load_catalog():
    rows = json.loads((ROOT / "guess_songs.json").read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in rows}
    index = defaultdict(set)
    for row in rows:
        for name in (row["title"], *row.get("official_aliases", [])):
            key = normalize(name)
            if key:
                index[key].add(row["title"])
    return by_title, index


def resolve_title(value: str, by_title, index) -> str | None:
    value = SPECIAL_TITLES.get(value, value)
    value = RENAMED_BYD.get(value, value)
    if value == "Last" or value.startswith("Last {{!"):
        return None
    if value in by_title:
        return value
    candidates = index.get(normalize(value), ())
    return next(iter(candidates)) if len(candidates) == 1 else None


def fetch_export() -> bytes:
    url = SOURCE_URL + "?" + urllib.parse.urlencode({"pages": SOURCE_TITLE})
    request = urllib.request.Request(
        url, headers={"User-Agent": "ARClass-alias-updater/1.0"}
    )
    last_error = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                if raw.lstrip().startswith(b"<mediawiki"):
                    return raw
                if b"Making sure you&#39;re not a bot!" in raw:
                    break
                last_error = RuntimeError(f"non-XML response: {raw[:80]!r}")
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(attempt)
    curl = shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [
                curl, "-fsSL", "--connect-timeout", "10", "--max-time", "60",
                url,
            ],
            check=False,
            capture_output=True,
        )
        if completed.returncode == 0 and completed.stdout.lstrip().startswith(b"<mediawiki"):
            return completed.stdout
        last_error = RuntimeError(
            f"curl export failed with exit code {completed.returncode}"
        )
    raise RuntimeError(f"community alias request failed: {last_error}")


def parse_export(raw: bytes):
    root = ET.fromstring(raw)
    namespace = {"mw": "http://www.mediawiki.org/xml/export-0.11/"}
    revision = root.find(".//mw:revision", namespace)
    if revision is None:
        raise ValueError("MediaWiki export does not contain a revision")
    revision_id = revision.findtext("mw:id", namespaces=namespace)
    revision_timestamp = revision.findtext("mw:timestamp", namespaces=namespace)
    wikitext = revision.findtext("mw:text", namespaces=namespace)
    if not revision_id or not revision_timestamp or not wikitext:
        raise ValueError("MediaWiki export revision is incomplete")
    version_match = re.search(r"目前更新到\s*(v[\d.]+)\s*曲目", wikitext)
    source_version = version_match.group(1) if version_match else "unknown"
    return revision_id, revision_timestamp, source_version, wikitext


def _row_cells(row: str) -> list[str]:
    return [line[1:] for line in row.strip().splitlines() if line.startswith("|")]


def _clean_aliases(cell: str) -> list[str]:
    cell = re.sub(r"<s>.*?</s>", "", cell, flags=re.IGNORECASE | re.DOTALL)
    cell = re.sub(r"<del>.*?</del>", "", cell, flags=re.IGNORECASE | re.DOTALL)
    cell = re.sub(r"~~.*?~~", "", cell, flags=re.DOTALL)
    aliases = []
    for value in re.split(r"<br\s*/?>", cell, flags=re.IGNORECASE):
        value = html.unescape(value)
        value = re.sub(r"<[^>]+>", "", value)
        value = value.strip(" '\t")
        if not value or value in {"暂无", "无"}:
            continue
        if re.search(r"\[(?:PST|PRS|FTR|BYD|ETR)\]", value, re.IGNORECASE):
            continue
        if "{{" in value or "[[" in value:
            continue
        if normalize(value) in RESERVED_ALIASES:
            continue
        aliases.append(value)
    return aliases


def community_aliases(wikitext: str, by_title, index):
    result = defaultdict(set)
    unknown = set()
    current_title = None
    for row in re.split(r"^\|-\s*$", wikitext, flags=re.MULTILINE)[1:]:
        if row.lstrip().startswith("|}"):
            break
        cells = _row_cells(row)
        if not cells:
            continue
        title_match = re.search(r"\{\{定数表组排单元\|([^|}]+)", cells[0])
        if title_match:
            displayed_title = title_match.group(1).strip()
            link_match = re.search(r"\blink=([^|}]+)", cells[0])
            source_title = (
                link_match.group(1).strip() if link_match else displayed_title
            )
            current_title = resolve_title(source_title, by_title, index)
            if current_title is None:
                current_title = resolve_title(displayed_title, by_title, index)
            if (
                current_title is None
                and displayed_title != "Last"
                and not displayed_title.startswith("Last {{!")
            ):
                unknown.add(displayed_title)
            alias_cell = cells[1] if len(cells) > 1 else ""
        else:
            alias_cell = cells[0]
        if current_title is not None:
            cleaned = _clean_aliases(alias_cell)
            if cleaned:
                result[current_title].update(cleaned)
    unexpected = unknown - EXPECTED_UNKNOWN_SOURCE_TITLES
    if unexpected:
        raise ValueError(
            "unmatched community source titles: "
            + ", ".join(sorted(unexpected, key=str.casefold))
        )
    return result


def generated_initialisms(by_title, imported) -> dict[str, set[str]]:
    candidates = defaultdict(set)
    for title in by_title:
        if any(not char.isascii() for char in title if char.isalnum()):
            continue
        words = re.findall(r"[A-Za-z0-9]+", title)
        words = [word for word in words if word.casefold() not in INITIALISM_STOPWORDS]
        if not 2 <= len(words) <= 5:
            continue
        value = "".join(word[0] for word in words).casefold()
        if 2 <= len(value) <= 5 and value not in RESERVED_ALIASES:
            candidates[value].add(title)
    imported_index = defaultdict(set)
    for title, values in imported.items():
        for value in values:
            imported_index[normalize(value)].add(title)
    result = defaultdict(set)
    for value, titles in candidates.items():
        if len(titles) != 1:
            continue
        title = next(iter(titles))
        if imported_index.get(normalize(value), {title}) - {title}:
            continue
        result[title].add(value)
    return result


def generated_short_titles(by_title) -> dict[str, set[str]]:
    """Generate suffix-free aliases without shadowing another official title."""

    canonical_by_key = defaultdict(set)
    for title in by_title:
        canonical_by_key[normalize(title)].add(title)

    result = defaultdict(set)
    for title in by_title:
        shortened = None
        parenthesized = re.fullmatch(r"(.+?)\s*\([^()]+\)\s*", title)
        if parenthesized:
            shortened = parenthesized.group(1).strip()
        else:
            featured = re.fullmatch(
                r"(.+?)\s+feat\.?\s+.+", title, flags=re.IGNORECASE
            )
            if featured:
                shortened = featured.group(1).strip()
        if not shortened or normalize(shortened) in RESERVED_ALIASES:
            continue
        official_collisions = canonical_by_key.get(normalize(shortened), set()) - {
            title
        }
        if official_collisions:
            continue
        result[title].add(shortened)
    return result


def build_snapshot(raw: bytes) -> dict:
    by_title, index = load_catalog()
    revision_id, timestamp, source_version, wikitext = parse_export(raw)
    imported = community_aliases(wikitext, by_title, index)
    initialisms = generated_initialisms(by_title, imported)
    short_titles = generated_short_titles(by_title)
    aliases = defaultdict(set)
    for title, values in imported.items():
        aliases[title].update(values)
    for title, values in initialisms.items():
        aliases[title].update(values)
    for title, values in short_titles.items():
        aliases[title].update(values)
    rendered_aliases = {
        title: sorted(values, key=lambda value: (value.casefold(), value))
        for title, values in sorted(aliases.items(), key=lambda item: item[0].casefold())
        if values
    }
    return {
        "_meta": {
            "source": SOURCE_PAGE_URL,
            "source_title": SOURCE_TITLE,
            "source_revision": int(revision_id),
            "source_revision_timestamp": timestamp,
            "source_game_version": source_version,
            "license": "CC BY-NC-SA (see source site terms)",
            "filter_policy": (
                "non-empty aliases; struck/deleted, difficulty-specific, "
                "unavailable, and reserved-command values excluded"
            ),
            "community_song_count": len(imported),
            "community_alias_count": sum(len(values) for values in imported.values()),
            "initialism_song_count": len(initialisms),
            "initialism_alias_count": sum(len(values) for values in initialisms.values()),
            "short_title_song_count": len(short_titles),
            "short_title_alias_count": sum(len(values) for values in short_titles.values()),
        },
        "aliases": rendered_aliases,
    }


def snapshot_aliases(value: dict) -> dict[str, list[str]]:
    if not isinstance(value, dict) or not isinstance(value.get("_meta"), dict):
        raise ValueError("community alias snapshot is missing _meta")
    aliases = value.get("aliases")
    if not isinstance(aliases, dict):
        raise ValueError("community alias snapshot aliases must be an object")
    return aliases


def merge_local_extras(generated: dict, output_path: Path) -> dict:
    """Keep locally added aliases when refreshing a generated snapshot."""

    if not output_path.exists():
        return generated
    try:
        current = json.loads(output_path.read_text(encoding="utf-8"))
        current_aliases = snapshot_aliases(current)
    except (OSError, ValueError, json.JSONDecodeError):
        return generated
    aliases = {
        title: set(values) for title, values in snapshot_aliases(generated).items()
    }
    extra_count = 0
    for title, values in current_aliases.items():
        target = aliases.setdefault(title, set())
        for value in values:
            if value not in target:
                target.add(value)
                extra_count += 1
    generated["aliases"] = {
        title: sorted(values, key=lambda value: (value.casefold(), value))
        for title, values in sorted(aliases.items(), key=lambda item: item[0].casefold())
    }
    generated["_meta"]["preserved_local_extra_alias_count"] = extra_count
    return generated


def contains_generated_snapshot(current: dict, generated: dict) -> bool:
    current_meta = current.get("_meta", {})
    generated_meta = generated.get("_meta", {})
    if current_meta.get("source_revision") != generated_meta.get("source_revision"):
        return False
    current_aliases = snapshot_aliases(current)
    for title, values in snapshot_aliases(generated).items():
        if not set(values).issubset(set(current_aliases.get(title, []))):
            return False
    return True


def validate_snapshot(value: dict) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    errors = []
    by_title, index = load_catalog()
    aliases = snapshot_aliases(value)
    manual = json.loads((ROOT / "guess_aliases.json").read_text(encoding="utf-8"))
    lookup = defaultdict(set)
    canonical_lookup = defaultdict(set)
    for title, row in by_title.items():
        canonical_lookup[normalize(title)].add(title)
        for name in (title, *row.get("official_aliases", [])):
            lookup[normalize(name)].add(title)
    for source_name, source in (("community", aliases), ("manual", manual)):
        for title, values in source.items():
            if title not in by_title:
                errors.append(f"{source_name} aliases reference unknown title: {title}")
                continue
            if not isinstance(values, list):
                errors.append(f"{source_name} aliases for {title} must be a list")
                continue
            for alias in values:
                if not isinstance(alias, str) or not normalize(alias):
                    errors.append(f"{source_name} aliases for {title} contain invalid value")
                    continue
                if normalize(alias) in RESERVED_ALIASES:
                    errors.append(f"reserved alias {alias!r} is assigned to {title}")
                    continue
                lookup[normalize(alias)].add(title)
    conflicts = {
        key: tuple(sorted(titles, key=str.casefold))
        for key, titles in lookup.items()
        if len(titles) > 1 and len(canonical_lookup.get(key, ())) != 1
    }
    return errors, conflicts


def report(value: dict, conflicts) -> None:
    aliases = snapshot_aliases(value)
    meta = value["_meta"]
    print(
        f"validated community aliases: {len(aliases)} songs, "
        f"{sum(len(values) for values in aliases.values())} aliases; "
        f"source revision {meta.get('source_revision')}"
    )
    print(f"combined alias conflicts: {len(conflicts)}")
    for key, titles in sorted(conflicts.items()):
        print(f"  {key} => {', '.join(titles)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="read a local MediaWiki export")
    parser.add_argument("--output", type=Path, default=ROOT / "guess_community_aliases.json")
    parser.add_argument("--refresh", action="store_true", help="download the live export")
    parser.add_argument("--check", action="store_true", help="validate/compare without writing")
    args = parser.parse_args()
    if args.source and args.refresh:
        parser.error("--source and --refresh are mutually exclusive")

    if args.source or args.refresh:
        raw = args.source.read_bytes() if args.source else fetch_export()
        value = build_snapshot(raw)
        if args.check:
            if not args.output.exists():
                print("guess_community_aliases.json is out of date", file=sys.stderr)
                return 1
            current = json.loads(args.output.read_text(encoding="utf-8"))
            if not contains_generated_snapshot(current, value):
                print("guess_community_aliases.json is out of date", file=sys.stderr)
                return 1
            value = current
        else:
            value = merge_local_extras(value, args.output)
            rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            args.output.write_text(rendered, encoding="utf-8")
    else:
        if not args.check:
            parser.error("use --check, --refresh, or --source PATH")
        value = json.loads(args.output.read_text(encoding="utf-8"))

    errors, conflicts = validate_snapshot(value)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    report(value, conflicts)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ET.ParseError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"guess alias update failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
