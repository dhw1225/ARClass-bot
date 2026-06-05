"""Yurisaki recent text parsing and chart matching helpers."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from math import floor
from typing import Optional

import scoring
from challenge_models import RecentTextResult, UnavailableSongResult


MIN_SCORE = 0
MAX_SCORE = 10_010_000
SONG_MATCH_THRESHOLD = 0.88
SONG_MATCH_MARGIN = 0.08

SONG_MATCH_ALIASES = {
    ("Quon (WACCA)", "BYD"): ["Quon", "Quon (WACCA)", "Quon (wacca)"],
}

AMBIGUOUS_SONG_GROUPS = {
    "quon": {"Quon (Lanota)", "Quon (WACCA)"},
    "genesis": {"Genesis (Tone Sphere)", "Genesis (CHUNITHM)"},
}

DIFFICULTY_ALIASES = {
    "PAST": "PST",
    "PST": "PST",
    "PRESENT": "PRS",
    "PRS": "PRS",
    "FUTURE": "FTR",
    "FTR": "FTR",
    "BEYOND": "BYD",
    "BYD": "BYD",
    "ETERNAL": "ETR",
    "ETR": "ETR",
}


def _normalize_song(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("ω", "w").replace("Ω", "w")
    return re.sub(r"[\s'\"`~!@#$%^&*_=+|\\/()[\]{}:;,.<>?，。・-]+", "", value)


def _extract_difficulty(value: str) -> Optional[str]:
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    return DIFFICULTY_ALIASES.get(normalized)


def _extract_chart_parts(chart_raw: str) -> tuple[str, Optional[str]]:
    chart_parts = re.match(r"(?s)^(.+)\s+\[([^\[\]]+)\]\s*$", chart_raw)
    if not chart_parts:
        return "", None
    return chart_parts.group(1).strip(), _extract_difficulty(chart_parts.group(2))


def _song_match_aliases(song: dict, difficulty: str) -> list[str]:
    song_name = str(song["name"])
    aliases = [song_name]
    for alias in song.get("aliases", []):
        alias_text = str(alias).strip()
        if alias_text and alias_text not in aliases:
            aliases.append(alias_text)
    for alias in SONG_MATCH_ALIASES.get((song_name, difficulty), []):
        alias_text = str(alias).strip()
        if alias_text and alias_text not in aliases:
            aliases.append(alias_text)
    return aliases


def _ambiguous_group_for_song(song_name: str) -> Optional[str]:
    normalized = _normalize_song(song_name)
    for group_name, canonical_names in AMBIGUOUS_SONG_GROUPS.items():
        if normalized == group_name:
            return group_name
        for canonical_name in canonical_names:
            if normalized == _normalize_song(canonical_name):
                return group_name
    return None


def _score_modifier(score: int) -> float:
    if score >= 10_000_000:
        return 2.0
    if score >= 9_800_000:
        return 1.0 + (score - 9_800_000) / 200_000
    return (score - 9_500_000) / 300_000


def _round_chart_constant(value: float) -> float:
    return floor(value * 10 + 0.5) / 10


def _match_ambiguous_song_by_potential(
    raw_song: str,
    difficulty: Optional[str],
    score: Optional[int],
    play_potential: Optional[float],
) -> Optional[str]:
    if difficulty is None or score is None or play_potential is None:
        return None
    if play_potential <= 0:
        return None

    group_name = _ambiguous_group_for_song(raw_song)
    if group_name is None:
        return None

    inferred_level = _round_chart_constant(play_potential - _score_modifier(score))
    matches = [
        song["name"]
        for song in scoring.get_db().songs
        if song["name"] in AMBIGUOUS_SONG_GROUPS[group_name]
        and str(song["difficulty"]).upper() == difficulty
        and _round_chart_constant(float(song["level"])) == inferred_level
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _match_song(
    raw_song: str, difficulty: Optional[str] = None
) -> tuple[Optional[str], float]:
    normalized = _normalize_song(raw_song)
    if not normalized:
        return None, 0.0

    best_by_song: dict[str, float] = {}
    for song in scoring.get_db().songs:
        song_difficulty = str(song["difficulty"]).upper()
        if difficulty is not None and song_difficulty != difficulty:
            continue
        song_name = song["name"]
        for alias in _song_match_aliases(song, song_difficulty):
            candidate = _normalize_song(alias)
            if not candidate:
                continue
            if len(candidate) <= 3 or len(normalized) <= 3:
                ratio = 1.0 if normalized == candidate else 0.0
            elif normalized == candidate:
                ratio = 1.0
            else:
                ratio = SequenceMatcher(None, normalized, candidate).ratio()
            best_by_song[song_name] = max(ratio, best_by_song.get(song_name, 0.0))

    scores = [(ratio, song) for song, ratio in best_by_song.items()]
    if not scores:
        return None, 0.0
    scores.sort(key=lambda item: item[0], reverse=True)
    best_score, best_song = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if best_score == 1.0 or (
        best_score >= SONG_MATCH_THRESHOLD
        and best_score - second_score >= SONG_MATCH_MARGIN
    ):
        return best_song, best_score
    return None, best_score


def is_relaxed_unavailable_song_match(
    chart_raw: str,
    parsed_difficulty: Optional[str],
    target: dict,
) -> bool:
    if parsed_difficulty is None:
        return False
    if parsed_difficulty != str(target["difficulty"]).upper():
        return False
    raw_song, _difficulty = _extract_chart_parts(chart_raw)
    raw_group = _ambiguous_group_for_song(raw_song)
    target_group = _ambiguous_group_for_song(str(target["name"]))
    return raw_group is not None and raw_group == target_group


def _chart_key(song: dict | str, difficulty: Optional[str] = None) -> str:
    if isinstance(song, dict):
        return f"{song['name'].casefold()}\0{song['difficulty'].upper()}"
    assert difficulty is not None
    return f"{song.casefold()}\0{difficulty.upper()}"


def parse_recent_text(text: str) -> RecentTextResult:
    chart_raw = ""
    score_raw = ""
    play_potential_raw = ""
    song: Optional[str] = None
    difficulty: Optional[str] = None
    score: Optional[int] = None
    play_potential: Optional[float] = None
    match_confidence = 0.0

    score_match = re.search(r"(?im)^\s*Score\s*:\s*([0-9]*)\s*$", text)
    if score_match:
        score_raw = score_match.group(1).strip()
        if score_raw:
            parsed_score = int(score_raw)
            if MIN_SCORE <= parsed_score <= MAX_SCORE:
                score = parsed_score

    potential_match = re.search(
        r"(?im)^\s*Play\s+Potential\s*:\s*([0-9]+(?:\.[0-9]+)?)", text
    )
    if potential_match:
        play_potential_raw = potential_match.group(1).strip()
        play_potential = float(play_potential_raw)

    chart_match = re.search(r"(?im)^\s*Chart\s*:\s*(.+?)\s*$", text)
    if chart_match:
        chart_raw = chart_match.group(1).strip()
        raw_song, difficulty = _extract_chart_parts(chart_raw)
        if raw_song:
            song = _match_ambiguous_song_by_potential(
                raw_song, difficulty, score, play_potential
            )
            if song is not None:
                match_confidence = 1.0
            else:
                song, match_confidence = _match_song(raw_song, difficulty)

    return RecentTextResult(
        raw_text=text,
        chart_raw=chart_raw,
        score_raw=score_raw,
        song=song,
        difficulty=difficulty,
        score=score,
        match_confidence=match_confidence,
        play_potential_raw=play_potential_raw,
        play_potential=play_potential,
    )


def parse_unavailable_song_text(text: str) -> UnavailableSongResult:
    chart_raw = ""
    song: Optional[str] = None
    difficulty: Optional[str] = None
    match_confidence = 0.0

    if "[arcaea score]" not in text.casefold() or "暂未游玩该曲目" not in text:
        return UnavailableSongResult(
            raw_text=text,
            chart_raw=chart_raw,
            song=song,
            difficulty=difficulty,
            match_confidence=match_confidence,
        )

    chart_match = re.search(
        r"暂未游玩该曲目[（(]\s*(.+?\[[^\[\]]+\])\s*[）)]", text
    )
    if chart_match:
        chart_raw = chart_match.group(1).strip()
        raw_song, difficulty = _extract_chart_parts(chart_raw)
        if raw_song:
            song, match_confidence = _match_song(raw_song, difficulty)

    return UnavailableSongResult(
        raw_text=text,
        chart_raw=chart_raw,
        song=song,
        difficulty=difficulty,
        match_confidence=match_confidence,
    )
