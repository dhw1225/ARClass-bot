"""Shared labels and lightweight format helpers for challenge messages."""

from __future__ import annotations

from challenge_models import ChallengeDefinition, ChallengeSession


def challenge_type_label(challenge_type: str) -> str:
    labels = {
        "random": "随机",
        "fixed": "固定顺序",
        "timed": "限时任意顺序",
        "infinite": "无限随机",
    }
    return labels.get(challenge_type, challenge_type)


def challenge_type_short_label(challenge_type: str) -> str:
    labels = {
        "random": "随机",
        "fixed": "固定",
        "timed": "限时",
        "infinite": "无限",
    }
    return labels.get(challenge_type, challenge_type)


def clear_type_label(clear_type: str) -> str:
    labels = {
        "hp": "血量",
        "score": "总分",
    }
    return labels.get(clear_type, clear_type)


def format_strict_faults_rule(strict_multiplier: int) -> str:
    return (
        "开启，"
        f"小p扣1、far扣{strict_multiplier + 1}、lost扣{2 * strict_multiplier + 1}"
    )


def ceil_div(total: int, count: int) -> int:
    return (total + count - 1) // count


def total_rounds(definition: ChallengeDefinition) -> int:
    if definition.type == "infinite":
        return 1
    total = definition.rounds if definition.type == "random" else len(definition.charts)
    assert total is not None
    return total


def infinite_cleared_charts(session: ChallengeSession) -> int:
    return sum(1 for record in session.records if record.hp_after > 0)


def recorded_cleared_charts(session: ChallengeSession, passed: bool) -> int:
    if session.challenge_type == "infinite":
        return infinite_cleared_charts(session)
    return len(session.records) if passed else 0


def hp_cap(session: ChallengeSession) -> int:
    return session.max_hp or session.initial_hp


def format_round_label(session: ChallengeSession) -> str:
    if session.challenge_type == "infinite":
        return f"第 {session.round_no} 首"
    return f"第 {session.round_no}/{session.total_rounds} 首"


def format_song(song: dict) -> str:
    return f"{song['name']} [{song['difficulty']}] 定数 {song['level']}"


def format_hp(session: ChallengeSession, hp: int) -> str:
    return f"{hp}/{hp_cap(session)}"
