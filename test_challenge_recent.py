from __future__ import annotations

import unittest

import scoring
from challenge_recent import parse_recent_text, parse_unavailable_song_text


class ChallengeRecentMatchingTests(unittest.TestCase):
    def recent_text(self, name: str, difficulty: str) -> str:
        return (
            "[Arcaea Recent]\n"
            f"Chart: {name} [{difficulty}]\n"
            "Score: 9900000\n"
        )

    def unavailable_text(self, name: str, difficulty: str) -> str:
        return f"[Arcaea Score]\n暂未游玩该曲目（{name} [{difficulty}]）"

    def assert_recent_matches(self, name: str, difficulty: str) -> None:
        parsed = parse_recent_text(self.recent_text(name, difficulty))
        self.assertEqual(parsed.song, name)
        self.assertEqual(parsed.difficulty, difficulty)
        self.assertEqual(parsed.match_confidence, 1.0)

    def assert_unavailable_matches(self, name: str, difficulty: str) -> None:
        parsed = parse_unavailable_song_text(self.unavailable_text(name, difficulty))
        self.assertEqual(parsed.song, name)
        self.assertEqual(parsed.difficulty, difficulty)
        self.assertEqual(parsed.match_confidence, 1.0)

    def test_symbol_only_song_name_matches_recent_and_unavailable_text(self) -> None:
        self.assert_recent_matches("~_+", "FTR")
        self.assert_unavailable_matches("~_+", "FTR")

    def test_regex_metacharacter_song_names_match_literally(self) -> None:
        cases = [
            ("#1f1e33", "FTR"),
            ("AI[UE]OON", "FTR"),
            ("Vicious [ANTi] Heroism", "BYD"),
            ("BATTLE NO.1", "FTR"),
            ("~_+", "FTR"),
        ]
        for name, difficulty in cases:
            with self.subTest(name=name, difficulty=difficulty):
                self.assert_recent_matches(name, difficulty)
                self.assert_unavailable_matches(name, difficulty)

    def test_all_exact_chart_names_match_recent_and_unavailable_text(self) -> None:
        for song in scoring.get_db().songs:
            name = str(song["name"])
            difficulty = str(song["difficulty"]).upper()
            with self.subTest(name=name, difficulty=difficulty):
                self.assert_recent_matches(name, difficulty)
                self.assert_unavailable_matches(name, difficulty)


if __name__ == "__main__":
    unittest.main()
