from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import scoring
from challenge import ChallengeManager, ChallengeResponse
from challenge_labels import display_song_name, format_song
from challenge_models import ChallengeSession, RoundRecord, TimedChartResult
from challenge_recent import _chart_key
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

    def alias_song(self) -> dict:
        return next(song for song in scoring.get_db().songs if song.get("aliases"))

    def no_alias_song(self) -> dict:
        return next(song for song in scoring.get_db().songs if not song.get("aliases"))

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

    def test_song_display_prefers_alias_and_keeps_canonical_logic_names(self) -> None:
        alias_song = self.alias_song()
        no_alias_song = self.no_alias_song()
        alias = alias_song["aliases"][0]

        self.assertEqual(display_song_name(alias_song), alias)
        self.assertEqual(display_song_name(alias_song["name"], alias_song["difficulty"]), alias)
        self.assertEqual(display_song_name(no_alias_song), no_alias_song["name"])
        self.assertIn(f"{alias} [{alias_song['difficulty']}]", format_song(alias_song))

    def test_visible_messages_use_alias_song_names(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "random")
        alias_song = self.alias_song()
        alias = alias_song["aliases"][0]
        user_id = "alias-display"
        self.assertEqual(manager.start(user_id, definition.name).status, "started")
        session = manager.sessions[user_id]
        session.targets = [alias_song]
        session.timed_results = {
            _chart_key(alias_song): TimedChartResult(
                song=alias_song["name"],
                difficulty=alias_song["difficulty"],
                level=alias_song["level"],
                notes=alias_song["notes"],
            )
        }

        waiting = manager.check_timeout(user_id).message
        self.assertIn(alias, waiting)
        self.assertIn(f"/a song {alias}", waiting)
        self.assertNotIn(alias_song["name"], waiting)

        self.assertIn(alias, manager._format_target_list([alias_song]))
        self.assertIn(alias, manager._format_timed_progress(session))
        session.timed_results[_chart_key(alias_song)].submission_count = 1
        self.assertIn(alias, manager._format_timed_progress(session))

        score_result = scoring.query(
            alias_song["name"], 10_000_000, difficulty=alias_song["difficulty"]
        )
        assert score_result is not None
        summary = manager._format_round_summary(session, score_result, 10, 10)
        self.assertIn(alias, summary)
        self.assertNotIn(alias_song["name"], summary)

        records = manager._format_records(
            [
                RoundRecord(
                    song=alias_song["name"],
                    difficulty=alias_song["difficulty"],
                    level=alias_song["level"],
                    notes=alias_song["notes"],
                    score=10_000_000,
                    faults=0,
                    max_pure=alias_song["notes"],
                    hp_before=10,
                    hp_after=10,
                    submitted_at="2026-01-01T12:00:00",
                )
            ]
        )
        self.assertIn(alias, records)
        self.assertNotIn(alias_song["name"], records)

        timed_session = ChallengeSession(
            user_id="timed-alias",
            challenge_name="timed",
            challenge_type="timed",
            clear_type="hp",
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            targets=[alias_song],
            initial_hp=10,
            time_limit_minutes=30,
        )
        timed_session.timed_results = session.timed_results
        timed_message = manager._format_session_message(
            timed_session,
            prefix="timed start",
            now=datetime(2026, 1, 1, 12, 0, 0),
        )
        self.assertIn(alias, timed_message)
        self.assertNotIn(alias_song["name"], timed_message)

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

    def test_random_reset_replaces_first_target_and_can_finish(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "random")
        user_id = "reset-user"
        started_at = datetime(2026, 1, 1, 12, 0, 0)
        reset_at = datetime(2026, 1, 1, 12, 2, 0)
        self.assertEqual(
            manager.start(user_id, definition.name, now=started_at).status,
            "started",
        )
        original_targets = list(manager.sessions[user_id].targets)
        original_first_key = _chart_key(original_targets[0])

        response = manager.reset(user_id, now=reset_at)

        session = manager.sessions[user_id]
        self.assertEqual(response.status, "reset")
        self.assertIn("已重新抽取第一首", response.message)
        self.assertIn("/a recent text", response.message)
        self.assertNotEqual(_chart_key(session.targets[0]), original_first_key)
        self.assertEqual(session.targets[1:], original_targets[1:])
        self.assertIn(original_first_key, session.random_excluded_chart_keys)
        self.assertEqual(session.round_announced_at, reset_at)

        finish = self.complete_current_ordered_challenge(
            manager, user_id, datetime(2026, 1, 1, 12, 3, 0)
        )
        self.assertEqual(finish.status, "finished_passed")
        self.assertTrue(
            self.read_stats()["users"][user_id]["challenges"][definition.name][
                "best_scores"
            ][0]["passed"]
        )

    def test_reset_ignores_non_random_or_played_states(self) -> None:
        for challenge_type in ("fixed", "timed", "infinite"):
            with self.subTest(challenge_type=challenge_type):
                manager = self.manager()
                definition = next(
                    (
                        item
                        for item in manager.challenge_store.definitions()
                        if item.type == challenge_type
                    ),
                    None,
                )
                if definition is None:
                    continue
                user_id = f"reset-{challenge_type}"
                self.assertEqual(manager.start(user_id, definition.name).status, "started")
                targets = list(manager.sessions[user_id].targets)

                response = manager.reset(user_id)

                self.assertEqual(response.status, "reset_ignored")
                self.assertEqual(response.message, "")
                self.assertEqual(manager.sessions[user_id].targets, targets)

        manager = self.manager()
        definition = self.first_definition(manager, "random")
        self.assertEqual(manager.start("reset-round-two", definition.name).status, "started")
        first_target = manager.sessions["reset-round-two"].current_target
        self.assertEqual(
            manager.handle_recent_text("reset-round-two", recent_text(first_target)).status,
            "round_completed",
        )
        second_target = manager.sessions["reset-round-two"].current_target
        response = manager.reset("reset-round-two")
        self.assertEqual(response.status, "reset_ignored")
        self.assertEqual(response.message, "")
        self.assertEqual(manager.sessions["reset-round-two"].current_target, second_target)

        manager = self.manager()
        self.assertEqual(manager.start("reset-recent", definition.name).status, "started")
        target = manager.sessions["reset-recent"].current_target
        manager.sessions["reset-recent"].recent_text_received_at = datetime.now()
        response = manager.reset("reset-recent")
        self.assertEqual(response.status, "reset_ignored")
        self.assertEqual(response.message, "")
        self.assertEqual(manager.sessions["reset-recent"].current_target, target)

        manager = self.manager()
        self.assertEqual(manager.start("reset-manual", definition.name).status, "started")
        target = manager.sessions["reset-manual"].current_target
        manager.sessions["reset-manual"].pending_manual_target = target
        response = manager.reset("reset-manual")
        self.assertEqual(response.status, "reset_ignored")
        self.assertEqual(response.message, "")
        self.assertEqual(manager.sessions["reset-manual"].current_target, target)

    def test_random_reset_excludes_previous_first_targets_and_candidate_exhaustion(self) -> None:
        manager = self.manager()
        definition = self.first_definition(manager, "random")
        user_id = "reset-repeat"
        self.assertEqual(manager.start(user_id, definition.name).status, "started")
        first_key = _chart_key(manager.sessions[user_id].current_target)

        self.assertEqual(manager.reset(user_id).status, "reset")
        second_key = _chart_key(manager.sessions[user_id].current_target)
        self.assertNotEqual(second_key, first_key)
        self.assertEqual(manager.reset(user_id).status, "reset")
        third_key = _chart_key(manager.sessions[user_id].current_target)
        self.assertNotIn(third_key, {first_key, second_key})

        manager = self.manager()
        user_id = "reset-exhausted"
        self.assertEqual(manager.start(user_id, definition.name).status, "started")
        session = manager.sessions[user_id]
        current_target_keys = {_chart_key(target) for target in session.targets}
        session.random_excluded_chart_keys.update(
            _chart_key(song)
            for song in scoring.get_db().songs
            if definition.level_min <= float(song["level"]) <= definition.level_max
            and _chart_key(song) not in current_target_keys
        )
        current_target = session.current_target

        response = manager.reset(user_id)

        self.assertEqual(response.status, "reset_ignored")
        self.assertEqual(response.message, "")
        self.assertEqual(session.current_target, current_target)

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
