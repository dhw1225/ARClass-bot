from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from challenge import ChallengeManager, ChallengeResponse
from challenge_store import ChallengeStatsStore


def recent_text(target: dict, score: int = 10_000_000) -> str:
    return (
        "[Arcaea Recent]\n"
        f"Chart: {target['name']} [{target['difficulty']}]\n"
        f"Score: {score}\n"
        "Play Potential: 12.0000, LS: 0"
    )


class ChallengeRuntimeRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.stats_path = self.root / "stats.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def manager(self, seed: int = 1) -> ChallengeManager:
        return ChallengeManager(
            stats_store=ChallengeStatsStore(self.stats_path),
            rng=random.Random(seed),
        )

    def read_stats(self) -> dict:
        if not self.stats_path.exists():
            return {}
        return json.loads(self.stats_path.read_text(encoding="utf-8"))

    def first_definition(self, manager: ChallengeManager, challenge_type: str):
        return next(
            item
            for item in manager.challenge_store.definitions()
            if item.type == challenge_type
        )

    def complete_current_ordered_challenge(
        self, manager: ChallengeManager, user_id: str, now: datetime
    ) -> ChallengeResponse:
        response: ChallengeResponse | None = None
        while user_id in manager.sessions:
            target = manager.sessions[user_id].current_target
            response = manager.handle_recent_text(user_id, recent_text(target), now=now)
        assert response is not None
        return response

    def complete_infinite_rounds(
        self, manager: ChallengeManager, user_id: str, count: int
    ) -> None:
        for index in range(count):
            target = manager.sessions[user_id].current_target
            response = manager.handle_recent_text(
                user_id,
                recent_text(target),
                now=datetime(2026, 1, 1, 12, 1 + index, 0),
            )
            self.assertEqual(response.status, "round_completed")

    def assert_infinite_record(self, user_id: str, challenge_name: str) -> None:
        record = self.read_stats()["users"][user_id]["challenges"][challenge_name]
        best = record["best_scores"][0]
        self.assertEqual(record["pass_count"], 0)
        self.assertFalse(best["passed"])
        self.assertEqual(best["cleared_charts"], 4)
        self.assertEqual(best["score"], 40_000_000)
        self.assertEqual(best["total_faults"], 0)

    def test_random_and_fixed_success_write_stats(self) -> None:
        for challenge_type in ("random", "fixed"):
            with self.subTest(challenge_type=challenge_type):
                manager = self.manager()
                definition = self.first_definition(manager, challenge_type)
                user_id = f"success-{challenge_type}"
                self.assertEqual(
                    manager.start(
                        user_id,
                        definition.name,
                        now=datetime(2026, 1, 1, 12, 0, 0),
                    ).status,
                    "started",
                )

                response = self.complete_current_ordered_challenge(
                    manager, user_id, datetime(2026, 1, 1, 12, 1, 0)
                )

                self.assertEqual(response.status, "finished_passed")
                record = self.read_stats()["users"][user_id]["challenges"][
                    definition.name
                ]
                self.assertEqual(record["pass_count"], 1)
                self.assertTrue(record["best_scores"][0]["passed"])

    def test_ordered_cancel_timeout_and_timeout_recent_do_not_write_stats(self) -> None:
        manager = self.manager()
        random_definition = self.first_definition(manager, "random")
        self.assertEqual(
            manager.start(
                "timeout-user",
                random_definition.name,
                now=datetime(2026, 1, 1, 12, 0, 0),
            ).status,
            "started",
        )
        self.assertEqual(
            manager.check_timeout(
                "timeout-user", now=datetime(2026, 1, 1, 12, 7, 0)
            ).status,
            "timeout_failed",
        )
        self.assertFalse(self.stats_path.exists())

        manager = self.manager()
        self.assertEqual(manager.start("cancel-user", random_definition.name).status, "started")
        self.assertEqual(manager.cancel("cancel-user").status, "cancelled_failed")
        self.assertFalse(self.stats_path.exists())

        manager = self.manager()
        self.assertEqual(
            manager.start(
                "late-recent",
                random_definition.name,
                now=datetime(2026, 1, 1, 12, 0, 0),
            ).status,
            "started",
        )
        target = manager.sessions["late-recent"].current_target
        self.assertEqual(
            manager.handle_recent_text(
                "late-recent",
                recent_text(target),
                now=datetime(2026, 1, 1, 12, 7, 0),
            ).status,
            "timeout_failed",
        )
        self.assertFalse(self.stats_path.exists())

    def test_fixed_hp_fail_does_not_write_stats(self) -> None:
        manager = self.manager()
        fixed_definition = self.first_definition(manager, "fixed")
        self.assertEqual(manager.start("hp-user", fixed_definition.name).status, "started")
        target = manager.sessions["hp-user"].current_target
        response = manager.handle_recent_text("hp-user", recent_text(target, 8_600_000))
        self.assertEqual(response.status, "hp_failed")
        self.assertFalse(self.stats_path.exists())

    def test_random_and_infinite_waiting_messages_include_song_hint(self) -> None:
        for challenge_type in ("random", "infinite"):
            with self.subTest(challenge_type=challenge_type):
                manager = self.manager()
                definition = self.first_definition(manager, challenge_type)
                user_id = f"hint-{challenge_type}"
                self.assertEqual(manager.start(user_id, definition.name).status, "started")
                response = manager.check_timeout(user_id)
                self.assertEqual(response.status, "waiting")
                self.assertIn("/a song", response.message)

    def test_infinite_cancel_after_clears_records_score(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "infinite")
        user_id = "inf-cancel"
        self.assertEqual(
            manager.start(
                user_id, definition.name, now=datetime(2026, 1, 1, 12, 0, 0)
            ).status,
            "started",
        )
        self.complete_infinite_rounds(manager, user_id, 4)

        response = manager.cancel(user_id, now=datetime(2026, 1, 1, 12, 10, 0))

        self.assertEqual(response.status, "cancelled_failed")
        self.assertIn("已记录本次无限段成绩：通关 4 首", response.message)
        self.assert_infinite_record(user_id, definition.name)
        self.assertIn("最佳通关曲数 4 首", manager.query_user_message(user_id))
        self.assertIn("通关 4 首", manager.challenge_rank_message(definition.name))

    def test_infinite_timeout_after_clears_records_score(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "infinite")
        user_id = "inf-timeout"
        self.assertEqual(
            manager.start(
                user_id, definition.name, now=datetime(2026, 1, 1, 12, 0, 0)
            ).status,
            "started",
        )
        self.complete_infinite_rounds(manager, user_id, 4)
        deadline = manager.sessions[user_id].deadline

        response = manager.check_timeout(user_id, now=deadline)
        self.assertEqual(response.status, "waiting")
        response = manager.check_timeout(user_id, now=deadline + timedelta(minutes=1))

        self.assertEqual(response.status, "timeout_failed")
        self.assertIn("已记录本次无限段成绩：通关 4 首", response.message)
        self.assert_infinite_record(user_id, definition.name)

    def test_infinite_timeout_recent_and_unavailable_record_score(self) -> None:
        for method_name in ("handle_recent_text", "handle_unavailable_song_text"):
            with self.subTest(method_name=method_name):
                manager = self.manager()
                definition = self.first_definition(manager, "infinite")
                user_id = f"inf-{method_name}"
                self.assertEqual(
                    manager.start(
                        user_id,
                        definition.name,
                        now=datetime(2026, 1, 1, 12, 0, 0),
                    ).status,
                    "started",
                )
                self.complete_infinite_rounds(manager, user_id, 4)
                target = manager.sessions[user_id].current_target
                method = getattr(manager, method_name)
                text = recent_text(target) if method_name == "handle_recent_text" else ""
                deadline = manager.sessions[user_id].deadline
                response = method(
                    user_id,
                    text,
                    now=deadline + timedelta(minutes=1),
                )
                self.assertEqual(response.status, "timeout_failed")
                self.assert_infinite_record(user_id, definition.name)

    def test_infinite_cancel_or_timeout_before_first_clear_does_not_write_stats(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "infinite")
        self.assertEqual(manager.start("inf-empty-cancel", definition.name).status, "started")
        self.assertEqual(manager.cancel("inf-empty-cancel").status, "cancelled_failed")
        self.assertFalse(self.stats_path.exists())

        manager = self.manager()
        self.assertEqual(
            manager.start(
                "inf-empty-timeout",
                definition.name,
                now=datetime(2026, 1, 1, 12, 0, 0),
            ).status,
            "started",
        )
        self.assertEqual(
            manager.check_timeout(
                "inf-empty-timeout", now=datetime(2026, 1, 1, 12, 7, 0)
            ).status,
            "timeout_failed",
        )
        self.assertFalse(self.stats_path.exists())


if __name__ == "__main__":
    unittest.main()
