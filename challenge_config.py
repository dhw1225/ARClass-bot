"""Challenge configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import scoring
from challenge_models import ChallengeDefinition
from challenge_recent import _chart_key


CHALLENGES_PATH = Path(__file__).parent / "challenges.json"
CHALLENGE_TYPES = {"random", "fixed", "timed", "infinite"}
CLEAR_TYPES = {"hp", "score"}


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n", ""}:
            return False
    return bool(value)


class ChallengeConfigStore:
    def __init__(self, path: Path = CHALLENGES_PATH):
        self.path = path
        self._challenges = self._load()

    def _load(self) -> dict[str, ChallengeDefinition]:
        if not self.path.exists():
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("challenges.json must contain a list")

        challenges: dict[str, ChallengeDefinition] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("each challenge must be an object")
            name = str(item.get("name", "")).strip()
            challenge_type = str(item.get("type", "")).strip().casefold()
            clear_type = str(item.get("clear_type", "hp")).strip().casefold()
            if not name:
                raise ValueError("challenge name cannot be empty")
            if challenge_type not in CHALLENGE_TYPES:
                raise ValueError(
                    f"challenge {name} has invalid type {challenge_type!r}"
                )
            if clear_type not in CLEAR_TYPES:
                raise ValueError(
                    f"challenge {name} has invalid clear_type {clear_type!r}"
                )
            if challenge_type == "infinite" and clear_type != "hp":
                raise ValueError(f"challenge {name} infinite type requires hp clear_type")
            if name in challenges:
                raise ValueError(f"duplicate challenge name: {name}")

            strict_faults = _bool_value(item.get("strict_faults", False))
            strict_multiplier = int(item.get("strict_multiplier", 1))
            if strict_faults and clear_type != "hp":
                raise ValueError(
                    f"challenge {name} strict_faults is only valid for hp clear_type"
                )
            if strict_faults and strict_multiplier <= 0:
                raise ValueError(f"challenge {name} strict_multiplier must be positive")

            clear_score: Optional[int] = None
            if clear_type == "score":
                if "clear_score" in item:
                    clear_score = int(item["clear_score"])
                    if clear_score < 0:
                        raise ValueError(
                            f"challenge {name} clear_score cannot be negative"
                        )
                else:
                    raise ValueError(
                        f"challenge {name} score clear_type requires clear_score"
                    )

            initial_hp = int(item.get("initial_hp", 0))
            heal_per_round = int(item.get("heal_per_round", 0))
            continue_on_zero_hp = _bool_value(item.get("continue_on_zero_hp", False))
            if clear_type == "hp" and initial_hp <= 0:
                raise ValueError(f"challenge {name} initial_hp must be positive")
            if heal_per_round < 0:
                raise ValueError(f"challenge {name} heal_per_round cannot be negative")
            hp_stages = self._parse_hp_stages(name, item, initial_hp, heal_per_round)

            charts = item.get("charts", [])
            if challenge_type in {"random", "infinite"}:
                rounds = int(item.get("rounds", 0)) if challenge_type == "random" else 1
                level_min = float(item.get("level_min"))
                level_max = float(item.get("level_max"))
                if challenge_type == "random" and rounds <= 0:
                    raise ValueError(f"challenge {name} rounds must be positive")
                if level_min > level_max:
                    raise ValueError(
                        f"challenge {name} level_min cannot exceed level_max"
                    )
                challenge = ChallengeDefinition(
                    name=name,
                    type=challenge_type,
                    clear_type=clear_type,
                    initial_hp=initial_hp,
                    heal_per_round=heal_per_round,
                    continue_on_zero_hp=continue_on_zero_hp,
                    strict_faults=strict_faults,
                    strict_multiplier=strict_multiplier,
                    clear_score=clear_score,
                    rounds=rounds,
                    level_min=level_min,
                    level_max=level_max,
                    hp_stages=hp_stages,
                )
            else:
                if not isinstance(charts, list) or not charts:
                    raise ValueError(
                        f"challenge {name} {challenge_type} charts cannot be empty"
                    )
                self._validate_config_charts(name, challenge_type, charts)
                time_limit_minutes: Optional[float] = None
                if challenge_type == "timed":
                    if "time_limit_minutes" not in item:
                        raise ValueError(
                            f"challenge {name} timed type requires time_limit_minutes"
                        )
                    time_limit_minutes = float(item["time_limit_minutes"])
                    if time_limit_minutes <= 0:
                        raise ValueError(
                            f"challenge {name} time_limit_minutes must be positive"
                        )
                challenge = ChallengeDefinition(
                    name=name,
                    type=challenge_type,
                    clear_type=clear_type,
                    initial_hp=initial_hp,
                    heal_per_round=heal_per_round,
                    continue_on_zero_hp=continue_on_zero_hp,
                    strict_faults=strict_faults,
                    strict_multiplier=strict_multiplier,
                    clear_score=clear_score,
                    hp_stages=hp_stages,
                    time_limit_minutes=time_limit_minutes,
                    charts=charts,
                )
            challenges[name] = challenge
        return challenges

    def _parse_hp_stages(
        self,
        name: str,
        item: dict,
        initial_hp: int,
        heal_per_round: int,
    ) -> list[dict]:
        raw_stages = item.get("hp_stages", [])
        if not raw_stages:
            return []
        if not isinstance(raw_stages, list):
            raise ValueError(f"challenge {name} hp_stages must be a list")

        stages: list[dict] = []
        previous_after = 0
        previous_max_hp = initial_hp
        previous_heal = heal_per_round
        for raw_stage in raw_stages:
            if not isinstance(raw_stage, dict):
                raise ValueError(f"challenge {name} hp_stages entries must be objects")
            after_clears = int(raw_stage.get("after_clears", 0))
            max_hp = int(raw_stage.get("max_hp", 0))
            stage_heal = int(raw_stage.get("heal_per_round", 0))
            if after_clears <= 0:
                raise ValueError(
                    f"challenge {name} hp_stages after_clears must be positive"
                )
            if after_clears <= previous_after:
                raise ValueError(
                    f"challenge {name} hp_stages after_clears must be ascending"
                )
            if max_hp <= 0:
                raise ValueError(f"challenge {name} hp_stages max_hp must be positive")
            if max_hp > previous_max_hp:
                raise ValueError(f"challenge {name} hp_stages max_hp cannot increase")
            if stage_heal < 0:
                raise ValueError(
                    f"challenge {name} hp_stages heal_per_round cannot be negative"
                )
            if stage_heal > previous_heal:
                raise ValueError(
                    f"challenge {name} hp_stages heal_per_round cannot increase"
                )
            stages.append(
                {
                    "after_clears": after_clears,
                    "max_hp": max_hp,
                    "heal_per_round": stage_heal,
                }
            )
            previous_after = after_clears
            previous_max_hp = max_hp
            previous_heal = stage_heal
        return stages

    def _validate_config_charts(
        self,
        name: str,
        challenge_type: str,
        charts: list[dict],
    ) -> None:
        db = scoring.get_db()
        seen: set[str] = set()
        for chart in charts:
            if not isinstance(chart, dict):
                raise ValueError(f"challenge {name} chart entries must be objects")
            song_name = str(chart.get("name", "")).strip()
            difficulty = str(chart.get("difficulty", "")).strip().upper()
            target = db.get_by_name_and_difficulty(song_name, difficulty)
            if target is None:
                raise ValueError(
                    f"challenge {name} cannot find chart: {song_name} [{difficulty}]"
                )
            key = _chart_key(target)
            if challenge_type == "timed" and key in seen:
                raise ValueError(
                    f"challenge {name} timed charts cannot repeat: {song_name} [{difficulty}]"
                )
            seen.add(key)

    def get(self, name: str) -> Optional[ChallengeDefinition]:
        return self._challenges.get(name.strip())

    def names(self) -> list[str]:
        return list(self._challenges)

    def definitions(self) -> list[ChallengeDefinition]:
        return list(self._challenges.values())
