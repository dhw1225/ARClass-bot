"""Persistent store for challenge stats."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


DEFAULT_CHALLENGE_NAME = "超上级"
TOP_SCORE_LIMIT = 10
STATS_PATH = Path(__file__).parent / "challenge_stats.json"


class ChallengeStatsStore:
    def __init__(self, path: Path = STATS_PATH):
        self.path = path

    def _load(self) -> dict:
        if not self.path.exists():
            return {"users": {}}
        with open(self.path, "r", encoding="utf-8") as f:
            return self._migrate(json.load(f))

    @staticmethod
    def _empty_challenge_stats() -> dict:
        return {"pass_count": 0, "best_scores": []}

    def _migrate(self, data: dict) -> dict:
        users = data.setdefault("users", {})
        for user in users.values():
            if "challenges" in user:
                continue
            old_stats = {
                "pass_count": int(user.get("pass_count", 0)),
                "best_scores": list(user.get("best_scores", [])),
            }
            user.clear()
            user["challenges"] = {DEFAULT_CHALLENGE_NAME: old_stats}
        return data

    def _save(self, data: dict) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    @staticmethod
    def _best_score_sort_key(item: dict) -> tuple:
        if item.get("challenge_type") == "infinite":
            return (
                -int(item.get("cleared_charts", 0)),
                -int(item.get("score", 0)),
                int(item.get("total_faults", 0)),
                str(item.get("finished_at", "")),
            )
        return (
            -int(item.get("score", 0)),
            int(item.get("total_faults", 0)),
            str(item.get("finished_at", "")),
        )

    def record_completed(
        self,
        user_id: str,
        challenge_name: str,
        total_score: int,
        passed: bool,
        finished_at: datetime,
        *,
        clear_type: str,
        total_faults: int,
        challenge_type: str,
        cleared_charts: Optional[int] = None,
    ) -> dict:
        data = self._load()
        users = data.setdefault("users", {})
        user = users.setdefault(user_id, {"challenges": {}})
        challenges = user.setdefault("challenges", {})
        challenge_stats = challenges.setdefault(
            challenge_name, self._empty_challenge_stats()
        )
        if passed:
            challenge_stats["pass_count"] = (
                int(challenge_stats.get("pass_count", 0)) + 1
            )
        scores = list(challenge_stats.get("best_scores", []))
        score_entry = {
            "score": total_score,
            "total_faults": total_faults,
            "clear_type": clear_type,
            "challenge_type": challenge_type,
            "passed": passed,
            "finished_at": finished_at.isoformat(timespec="seconds"),
        }
        if cleared_charts is not None:
            score_entry["cleared_charts"] = int(cleared_charts)
        scores.append(score_entry)
        scores.sort(key=self._best_score_sort_key)
        challenge_stats["best_scores"] = scores[:TOP_SCORE_LIMIT]
        self._save(data)
        return challenge_stats

    def get_user(
        self, user_id: str, challenge_name: str = DEFAULT_CHALLENGE_NAME
    ) -> dict:
        data = self._load()
        user = data.get("users", {}).get(user_id, {})
        return (
            user.get("challenges", {}).get(challenge_name)
            or self._empty_challenge_stats()
        )

    def get_user_challenges(self, user_id: str) -> dict[str, dict]:
        data = self._load()
        user = data.get("users", {}).get(user_id, {})
        return dict(user.get("challenges", {}))

    def passed_user_ids(self, challenge_name: str) -> list[str]:
        data = self._load()
        user_ids = []
        for user_id, user in data.get("users", {}).items():
            challenge_stats = user.get("challenges", {}).get(challenge_name)
            if not challenge_stats:
                continue
            if int(challenge_stats.get("pass_count", 0)) > 0 or any(
                item.get("passed")
                for item in challenge_stats.get("best_scores", [])
            ):
                user_ids.append(str(user_id))
        return user_ids

    def first_clear_record(self, challenge_name: str) -> Optional[dict]:
        data = self._load()
        first_clear = None
        for user_id, user in data.get("users", {}).items():
            challenge_stats = user.get("challenges", {}).get(challenge_name)
            if not challenge_stats:
                continue
            for score in challenge_stats.get("best_scores", []):
                if not score.get("passed"):
                    continue
                finished_at = str(score.get("finished_at", "")).strip()
                record = {
                    "user_id": str(user_id),
                    "achieved_at": finished_at,
                    "score": int(score.get("score", 0)),
                    "total_faults": int(score.get("total_faults", 0)),
                }
                if first_clear is None or (
                    finished_at,
                    record["user_id"],
                ) < (
                    str(first_clear.get("achieved_at", "")),
                    str(first_clear.get("user_id", "")),
                ):
                    first_clear = record
        return first_clear

    def get_challenge_user_records(self, challenge_name: str) -> list[dict]:
        data = self._load()
        records = []
        for user_id, user in data.get("users", {}).items():
            challenge_stats = user.get("challenges", {}).get(challenge_name)
            if not challenge_stats:
                continue
            best_scores = list(challenge_stats.get("best_scores", []))
            if not best_scores:
                continue
            best_scores.sort(key=self._best_score_sort_key)
            records.append(
                {
                    "user_id": str(user_id),
                    "pass_count": int(challenge_stats.get("pass_count", 0)),
                    "record_count": len(best_scores),
                    "best": best_scores[0],
                }
            )
        return records
