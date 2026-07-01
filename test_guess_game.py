import asyncio
import json
import random
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image

from guess_game import (
    GuessCatalog,
    GuessGameManager,
    GuessSong,
    normalize_song_name,
    parse_guess_command,
)
from guess_image import CARD_GAP, CARD_HEIGHT, OUTER_MARGIN, comparison_marker, render_guess_history
from tools.update_guess_aliases import generated_short_titles, load_catalog


def song(title, value, *, extra=None):
    return GuessSong(
        title=title,
        pack=f"Pack {value}",
        pack_type="Arcaea",
        side="光芒" if value == 1 else "纷争",
        bpm_min=100 + value,
        bpm_max=200 + value,
        pst=1 + value,
        prs=4 + value,
        ftr=7 + value,
        byd=extra,
        etr=None,
        year=2020 + value,
    )


class GuessCatalogTests(unittest.TestCase):
    def test_normalized_exact_alias_and_ambiguity(self):
        first, second = song("A Song", 1), song("B-Song", 2)
        catalog = GuessCatalog(
            (first, second), {"A Song": ["共同", "A.SONG"], "B-Song": ["共同"]}
        )
        self.assertEqual(normalize_song_name(" Ａ. song "), "asong")
        self.assertEqual(normalize_song_name(" ~ _ + "), "~_+")
        self.assertEqual(catalog.lookup("a song").song, first)
        self.assertEqual(catalog.lookup("共同").status, "ambiguous")
        self.assertEqual(catalog.lookup("unknown").status, "not_found")

    def test_command_parser_rejects_similar_commands(self):
        self.assertEqual(parse_guess_command(" /GuEsS "), "start")
        self.assertEqual(parse_guess_command("/guess stop"), "stop")
        self.assertIsNone(parse_guess_command("/guesser"))
        self.assertIsNone(parse_guess_command("/guess song"))

    def test_checked_in_player_aliases_and_casefold(self):
        catalog = GuessCatalog.load()
        expected = {
            "色号": "#1f1e33",
            "ad": "Abstruse Dilemma",
            "光追": "Aegleseeker",
            "绿ae": "ALTER EGO",
            "ac": "Axium Crisis",
            "temptation": "TEmPTaTiON",
        }
        for alias, title in expected.items():
            with self.subTest(alias=alias):
                self.assertEqual(catalog.lookup(alias).song.title, title)
        ae = catalog.lookup("ae")
        self.assertEqual(ae.status, "ambiguous")
        self.assertEqual(set(ae.candidates), {"ALTER EGO", "Arcana Eden"})
        expected_conflicts = {
            "ae", "genesis", "lc", "quon", "梦魇", "维耶拉",
        }
        actual_conflicts = {
            key
            for key, titles in catalog._index.items()
            if len(titles) > 1 and len(catalog._title_index.get(key, ())) != 1
        }
        self.assertEqual(actual_conflicts, expected_conflicts)
        red_mosquito = catalog.lookup("红蚊子")
        self.assertEqual(red_mosquito.status, "candidates")
        self.assertIn("Heavensdoor", red_mosquito.candidates)

        yurisaki_aliases = {
            "RTX": "Aegleseeker",
            "黑老二": "Sheriruth (Laur Remix)",
            "铁丝": "Testify",
            "公交车": "conflict",
        }
        for alias, title in yurisaki_aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(catalog.lookup(alias).song.title, title)
        self.assertEqual(catalog.lookup("维耶拉").status, "ambiguous")
        self.assertEqual(catalog.lookup("梦魇").status, "ambiguous")

    def test_arcaea_song_database_v46_aliases(self):
        catalog = GuessCatalog.load()
        expected = {
            "雷": "怒槌",
            "第7感": "7thSense",
            "第七感": "7thSense",
            "寒红": "Capella",
            "冬红": "Capella",
            "冬日红": "Capella",
            "月映丛云风语花": "月に叢雲華に風",
            "丛云遮月风落花": "月に叢雲華に風",
            "闲云遮月清风袭花": "月に叢雲華に風",
            "天体演练": "Astra walkthrough",
            "螳螂": "MANTIS (Arcaea Ultra-Bloodrush VIP)",
            "虚空脑补": "NULL APOPHENIA",
            "∞": "[X]",
            "impart": "IMPACT",
            "爬虫线程": "蜘蛛の糸",
            "蜘蛛线程": "蜘蛛の糸",
            "初始光": "PRIMITIVE LIGHTS",
            "原初之光": "PRIMITIVE LIGHTS",
            "cpfc": "コスモポップファンクラブ",
            "芭芭拉": "コスモポップファンクラブ",
            "无法地点": "無法地点",
            "银河之恋": "Galactic Love",
            "银河": "To the Milky Way",
            "牛奶路": "To the Milky Way",
            "混沌": "CHAOS",
            "失去情感": "Lost Emotion feat. nomico",
            "幸存者": "The Survivor (Game Edit)",
            "纽约": "New York Back Raise",
            "新乡": "New York Back Raise",
            "深渊": "Lost in the Abyss",
            "傲慢": "ヒュブリスの頂に聳えるのは",
            "恶魔球": "Devillic Sphere",
            "清醒旅人": "Lucid Traveler",
            "过时人物": "Used to be",
            "曾是": "Used to be",
            "大热狗": "DRG",
            "删游戏": "DRG",
            "马拉松": "͟͝͞Ⅱ́̕",
        }
        self.assertEqual(len(expected), 38)
        for alias, title in expected.items():
            with self.subTest(alias=alias):
                result = catalog.lookup(alias)
                self.assertEqual(result.status, "found")
                self.assertEqual(result.song.title, title)

        self.assertEqual(normalize_song_name("∞"), "∞")
        self.assertEqual(catalog.lookup("無法地点").song.title, "無法地点")
        self.assertEqual(catalog.lookup("无法地点").song.title, "無法地点")

    def test_generated_suffix_free_aliases(self):
        by_title, _ = load_catalog()
        generated = generated_short_titles(by_title)
        self.assertEqual(len(generated), 27)
        self.assertEqual(sum(map(len, generated.values())), 27)

        catalog = GuessCatalog.load()
        ambiguous = {
            "Genesis": {"Genesis (CHUNITHM)", "Genesis (Tone Sphere)"},
            "Quon": {"Quon (Lanota)", "Quon (WACCA)"},
        }
        for title, aliases in generated.items():
            alias = next(iter(aliases))
            if title == "World Fragments III(radio edit)":
                continue
            result = catalog.lookup(alias)
            with self.subTest(title=title, alias=alias):
                if alias in ambiguous:
                    self.assertEqual(result.status, "ambiguous")
                    self.assertEqual(set(result.candidates), ambiguous[alias])
                    self.assertIn(title, result.candidates)
                else:
                    self.assertEqual(result.status, "found")
                    self.assertEqual(result.song.title, title)

        self.assertEqual(
            catalog.lookup("World Fragments").song.title,
            "World Fragments III(radio edit)",
        )
        self.assertNotIn(
            normalize_song_name("World Fragments III"),
            catalog._fuzzy_names["World Fragments III(radio edit)"],
        )

        self.assertEqual(catalog.lookup("Sheriruth").song.title, "Sheriruth")
        self.assertEqual(catalog.lookup("MEGALOVANIA").song.title, "MEGALOVANIA")
        self.assertEqual(
            catalog.lookup("Sheriruth (Laur Remix)").song.title,
            "Sheriruth (Laur Remix)",
        )
        self.assertEqual(
            catalog.lookup("MEGALOVANIA (Camellia Remix)").song.title,
            "MEGALOVANIA (Camellia Remix)",
        )

    def test_fuzzy_typo_transposition_and_long_title_fragment(self):
        testify = song("Testify", 1)
        other = song("Pentiment", 2)
        catalog = GuessCatalog((testify, other))
        self.assertEqual(catalog.lookup("Testfy").song, testify)
        self.assertEqual(catalog.lookup("Tetsify").song, testify)

        full = GuessCatalog.load()
        self.assertEqual(
            full.lookup("shades of light").song.title,
            "Shades of Light in a Transcendent Realm",
        )

    def test_short_fuzzy_name_is_never_automatic(self):
        first, second = song("First", 1), song("Second", 2)
        catalog = GuessCatalog((first, second), {"First": ["ae"]})
        result = catalog.lookup("af")
        self.assertEqual(result.status, "candidates")
        self.assertEqual(result.candidates, ("First",))

    def test_close_fuzzy_results_are_candidates_and_too_many_fail(self):
        first, second = song("Testify", 1), song("Testifly", 2)
        catalog = GuessCatalog((first, second))
        result = catalog.lookup("Testifx")
        self.assertEqual(result.status, "candidates")
        self.assertEqual(set(result.candidates), {"Testifly", "Testify"})

        songs = tuple(song(f"Song {number}", number) for number in range(11))
        aliases = {
            item.title: [f"a{chr(ord('b') + index)}"]
            for index, item in enumerate(songs)
        }
        self.assertEqual(GuessCatalog(songs, aliases).lookup("aa").status, "not_found")


class GuessManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_scope_duplicate_success_and_no_immediate_repeat(self):
        now = [100.0]
        first, second = song("First", 1), song("Second", 2)
        manager = GuessGameManager(
            GuessCatalog((first, second), {"First": ["一"]}),
            rng=random.Random(4),
            clock=lambda: now[0],
        )
        self.assertEqual((await manager.start("1", "starter")).status, "started")
        answer = manager.sessions["1"].answer
        wrong = second if answer == first else first
        self.assertEqual((await manager.guess("1", wrong.title)).status, "incorrect")
        self.assertEqual((await manager.guess("1", wrong.title)).status, "duplicate")
        result = await manager.guess("1", answer.title)
        self.assertEqual(result.status, "correct")
        self.assertEqual(result.rounds, 2)
        await manager.start("1", "other")
        self.assertNotEqual(manager.sessions["1"].answer.title, answer.title)
        await manager.start("2", "other")
        self.assertIn("2", manager.sessions)

    async def test_timeout_only_valid_guess_refreshes_activity(self):
        now = [0.0]
        manager = GuessGameManager(
            GuessCatalog((song("First", 1), song("Second", 2))),
            clock=lambda: now[0],
            timeout_seconds=10,
        )
        await manager.start("g", "u")
        now[0] = 8
        self.assertEqual((await manager.guess("g", "missing")).status, "not_found")
        now[0] = 10
        expired = await manager.collect_expired()
        self.assertEqual(len(expired), 1)
        self.assertNotIn("g", manager.sessions)

    async def test_fuzzy_candidates_do_not_consume_round_or_refresh_activity(self):
        now = [0.0]
        first, second = song("Testify", 1), song("Testament", 2)
        manager = GuessGameManager(
            GuessCatalog((first, second)), clock=lambda: now[0]
        )
        await manager.start("g", "u")
        now[0] = 5.0
        result = await manager.guess("g", "Testifament")
        self.assertEqual(result.status, "candidates")
        self.assertEqual(manager.sessions["g"].history, [])
        self.assertEqual(manager.sessions["g"].last_activity, 0.0)

    async def test_start_after_expiry_returns_previous_answer(self):
        now = [0.0]
        manager = GuessGameManager(
            GuessCatalog((song("First", 1), song("Second", 2))),
            clock=lambda: now[0],
            timeout_seconds=10,
        )
        await manager.start("g", "u")
        previous = manager.sessions["g"].answer
        now[0] = 10
        result = await manager.start("g", "other")
        self.assertEqual(result.status, "started_after_expiry")
        self.assertEqual(result.answer, previous)
        self.assertNotEqual(manager.sessions["g"].answer, previous)

    async def test_stop_permission_and_round_limit(self):
        choices = tuple(song(f"Song {number}", number) for number in range(1, 5))
        manager = GuessGameManager(
            GuessCatalog(choices), rng=random.Random(1), max_rounds=2
        )
        await manager.start("g", "starter")
        self.assertEqual((await manager.stop("g", "other")).status, "stop_forbidden")
        self.assertEqual(
            (await manager.stop("g", "admin", is_admin=True)).status, "stopped"
        )
        await manager.start("g", "starter")
        answer = manager.sessions["g"].answer
        wrong = [item for item in choices if item != answer]
        await manager.guess("g", wrong[0].title)
        result = await manager.guess("g", wrong[1].title)
        self.assertEqual(result.status, "round_limit")
        self.assertEqual(result.rounds, 2)

    async def test_concurrent_guesses_are_serialized(self):
        choices = (song("Answer", 1), song("Wrong", 2))
        manager = GuessGameManager(GuessCatalog(choices), rng=random.Random(0))
        await manager.start("g", "u")
        answer = manager.sessions["g"].answer.title
        results = await asyncio.gather(
            manager.guess("g", answer), manager.guess("g", answer)
        )
        self.assertEqual(sum(result.status == "correct" for result in results), 1)

    async def test_default_round_limit_is_fifteen(self):
        choices = tuple(song(f"Song {number}", number) for number in range(16))
        manager = GuessGameManager(GuessCatalog(choices), rng=random.Random(2))
        await manager.start("g", "starter")
        answer = manager.sessions["g"].answer
        wrong = [item for item in choices if item != answer]
        for item in wrong[:14]:
            self.assertEqual((await manager.guess("g", item.title)).status, "incorrect")
        result = await manager.guess("g", wrong[14].title)
        self.assertEqual(result.status, "round_limit")
        self.assertEqual(result.rounds, 15)


class GuessImageAndDataTests(unittest.TestCase):
    def test_arrows_point_toward_answer(self):
        self.assertEqual(comparison_marker(100, 200), "↑")
        self.assertEqual(comparison_marker(200, 100), "↓")
        self.assertEqual(comparison_marker(None, 100), "")

    def test_render_history_has_bounded_expected_size(self):
        answer = song("答案 Answer", 2, extra=9.9)
        history = (song("第一首", 1), song("第二首", 2, extra=9.9))
        data = render_guess_history(history, answer)
        with Image.open(__import__("io").BytesIO(data)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.width, 1040)
            self.assertEqual(
                image.height, OUTER_MARGIN * 2 + CARD_HEIGHT * 2 + CARD_GAP
            )

    def test_checked_in_catalog_is_complete(self):
        root = Path(__file__).parent
        raw = json.loads((root / "guess_songs.json").read_text(encoding="utf-8"))
        catalog = GuessCatalog.load()
        self.assertEqual(len(raw), 532)
        self.assertEqual(len(catalog.songs), 532)
        self.assertNotIn("Last", catalog.by_title)
        expected = {
            "PRAGMATISM": 11.2,
            "Ignotus": 9.9,
            "Axium Crisis": 11.3,
            "Red and Blue": 10.2,
            "Singularity": 10.4,
            "dropdead": 10.5,
            "Vicious Heroism": 11.2,
        }
        for title, constant in expected.items():
            self.assertEqual(catalog.by_title[title].byd, constant)
        self.assertEqual(catalog.lookup("Quon").status, "ambiguous")
        self.assertEqual(catalog.lookup("~_+").song.title, "~_+")
        for title in catalog.by_title:
            with self.subTest(title=title):
                self.assertEqual(catalog.lookup(title).song.title, title)

    def test_offline_data_validator_checks_alias_conflicts(self):
        root = Path(__file__).parent
        completed = subprocess.run(
            [sys.executable, "tools/update_guess_data.py", "--check"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("validated 532 songs", completed.stdout)
        self.assertIn("alias conflicts (2)", completed.stdout)
        self.assertIn("genesis", completed.stdout)
        self.assertIn("quon", completed.stdout)

    def test_offline_community_alias_validator(self):
        root = Path(__file__).parent
        completed = subprocess.run(
            [sys.executable, "tools/update_guess_aliases.py", "--check"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("validated community aliases", completed.stdout)
        snapshot = json.loads(
            (root / "guess_community_aliases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["_meta"]["source_revision"], 75756)


if __name__ == "__main__":
    unittest.main()
