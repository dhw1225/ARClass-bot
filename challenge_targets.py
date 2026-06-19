"""Target selection and chart-list helpers for challenges."""

from __future__ import annotations

from typing import Optional

import scoring
from challenge_models import ChallengeDefinition, ChallengeSession
from challenge_labels import display_song_name
from challenge_recent import _chart_key


def build_targets(definition: ChallengeDefinition, rng, format_song) -> tuple[list[dict], Optional[str]]:
    db = scoring.get_db()
    if definition.type in {"random", "infinite"}:
        assert definition.rounds is not None
        assert definition.level_min is not None
        assert definition.level_max is not None
        candidates = [
            song
            for song in db.songs
            if definition.level_min <= float(song["level"]) <= definition.level_max
        ]
        if definition.type == "infinite":
            if not candidates:
                return [], (
                    f"{definition.name} 候选谱面不足："
                    f"当前区间 {definition.level_min:g}-{definition.level_max:g} 没有谱面。"
                )
            return [rng.choice(candidates)], None
        if len(candidates) < definition.rounds:
            return [], (
                f"{definition.name} 候选谱面不足：需要 {definition.rounds} 首，"
                f"当前区间 {definition.level_min:g}-{definition.level_max:g} 只有 {len(candidates)} 首。"
            )
        return rng.sample(candidates, definition.rounds), None

    targets: list[dict] = []
    seen: set[str] = set()
    for chart in definition.charts:
        if not isinstance(chart, dict):
            return [], f"{definition.name} 曲目格式错误。"
        song_name = str(chart.get("name", "")).strip()
        difficulty = str(chart.get("difficulty", "")).strip().upper()
        target = db.get_by_name_and_difficulty(song_name, difficulty)
        if target is None:
            return [], f"{definition.name} 找不到谱面：{song_name} [{difficulty}]。"
        key = _chart_key(target)
        if definition.type == "timed" and key in seen:
            return [], f"{definition.name} timed 曲目不能重复：{format_song(target)}。"
        seen.add(key)
        targets.append(target)
    return targets, None


def replacement_random_target(session: ChallengeSession, unavailable_key: str, challenge_store, rng) -> Optional[dict]:
    definition = challenge_store.get(session.challenge_name)
    if definition is None or definition.type not in {"random", "infinite"}:
        return None
    assert definition.level_min is not None
    assert definition.level_max is not None

    excluded = set(session.random_excluded_chart_keys)
    excluded.add(unavailable_key)
    if session.challenge_type == "infinite":
        if session.current_index > 0:
            excluded.add(_chart_key(session.targets[session.current_index - 1]))
    else:
        excluded.update(_chart_key(target) for target in session.targets)
        excluded.update(
            _chart_key(record.song, record.difficulty) for record in session.records
        )
    candidates = [
        song
        for song in scoring.get_db().songs
        if definition.level_min <= float(song["level"]) <= definition.level_max
        and _chart_key(song) not in excluded
    ]
    if not candidates:
        return None
    return rng.choice(candidates)


def next_infinite_target(session: ChallengeSession, challenge_store, rng) -> tuple[Optional[dict], Optional[str]]:
    definition = challenge_store.get(session.challenge_name)
    if definition is None or definition.type != "infinite":
        return None, "当前段位不是无限段，无法生成下一首。"
    assert definition.level_min is not None
    assert definition.level_max is not None

    excluded = set(session.random_excluded_chart_keys)
    excluded.add(_chart_key(session.current_target))
    candidates = [
        song
        for song in scoring.get_db().songs
        if definition.level_min <= float(song["level"]) <= definition.level_max
        and _chart_key(song) not in excluded
    ]
    if not candidates:
        return None, (
            f"{session.challenge_name} 候选谱面不足："
            f"当前区间 {definition.level_min:g}-{definition.level_max:g} 没有可作为下一首的谱面。"
        )
    return rng.choice(candidates), None


def find_timed_target(session: ChallengeSession, song: str, difficulty: str) -> Optional[dict]:
    key = _chart_key(song, difficulty)
    for target in session.targets:
        if _chart_key(target) == key:
            return target
    return None


def format_target_list(targets: list[dict]) -> str:
    return "、".join(
        f"{display_song_name(target)} [{target['difficulty']}]" for target in targets
    )
