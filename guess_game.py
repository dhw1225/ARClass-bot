"""Framework-independent Arcaea song guessing game."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


GAME_TIMEOUT_SECONDS = 10 * 60
GAME_MAX_ROUNDS = 15
FUZZY_MAX_CANDIDATES = 10
FUZZY_AUTO_MARGIN = 0.08


def normalize_song_name(value: str) -> str:
    """Normalize a title/alias without losing non-Latin letters or digits."""

    value = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(char for char in value if char.isalnum())
    if normalized:
        return normalized
    # A real chart title (`~_+`) consists only of punctuation. Preserve symbols
    # for such names so they remain guessable without making every punctuation-
    # only input equivalent to every other one.
    return "".join(char for char in value if not char.isspace())


def _damerau_levenshtein_distance(left: str, right: str) -> int:
    """Return the optimal-string-alignment edit distance for two names."""

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous_previous: Optional[list[int]] = None
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        for right_index, right_char in enumerate(right, 1):
            cost = 0 if left_char == right_char else 1
            value = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + cost,
            )
            if (
                previous_previous is not None
                and left_index > 1
                and right_index > 1
                and left_char == right[right_index - 2]
                and left[left_index - 2] == right_char
            ):
                value = min(value, previous_previous[right_index - 2] + 1)
            current.append(value)
        previous_previous, previous = previous, current
    return previous[-1]


def _edit_similarity(left: str, right: str) -> float:
    maximum = max(len(left), len(right))
    if maximum == 0:
        return 1.0
    return 1.0 - _damerau_levenshtein_distance(left, right) / maximum


def _partial_similarity(query: str, candidate: str) -> float:
    """Compare a query against the best similarly-sized candidate fragment."""

    if not query or len(query) > len(candidate):
        return 0.0
    best = 0.0
    for width in {max(1, len(query) - 1), len(query), len(query) + 1}:
        if width > len(candidate):
            continue
        for start in range(len(candidate) - width + 1):
            best = max(
                best, _edit_similarity(query, candidate[start : start + width])
            )
            if best == 1.0:
                return best
    return best


@dataclass(frozen=True)
class GuessSong:
    title: str
    pack: str
    pack_type: str
    side: str
    bpm_min: float
    bpm_max: float
    pst: float
    prs: float
    ftr: float
    byd: Optional[float]
    etr: Optional[float]
    year: int
    official_aliases: tuple[str, ...] = ()

    @property
    def extra_constant(self) -> Optional[float]:
        return self.byd if self.byd is not None else self.etr

    @property
    def extra_difficulty(self) -> Optional[str]:
        if self.byd is not None:
            return "BYD"
        if self.etr is not None:
            return "ETR"
        return None


@dataclass(frozen=True)
class SongLookup:
    status: str
    song: Optional[GuessSong] = None
    candidates: tuple[str, ...] = ()


class GuessCatalog:
    def __init__(
        self,
        songs: Iterable[GuessSong],
        aliases: Optional[dict[str, list[str]]] = None,
    ) -> None:
        self.songs = tuple(songs)
        if not self.songs:
            raise ValueError("guess song catalog is empty")
        self.by_title = {song.title: song for song in self.songs}
        if len(self.by_title) != len(self.songs):
            raise ValueError("duplicate canonical title in guess song catalog")

        index: dict[str, set[str]] = defaultdict(set)
        title_index: dict[str, set[str]] = defaultdict(set)
        fuzzy_names: dict[str, set[str]] = defaultdict(set)
        aliases = aliases or {}
        unknown_alias_titles = sorted(set(aliases) - set(self.by_title))
        if unknown_alias_titles:
            raise ValueError(
                "aliases reference unknown songs: " + ", ".join(unknown_alias_titles)
            )
        for song in self.songs:
            title_index[normalize_song_name(song.title)].add(song.title)
            for name in (song.title, *song.official_aliases, *aliases.get(song.title, [])):
                normalized = normalize_song_name(name)
                if normalized:
                    index[normalized].add(song.title)
                    fuzzy_names[song.title].add(normalized)
        self._index = {key: tuple(sorted(values)) for key, values in index.items()}
        self._title_index = {
            key: tuple(sorted(values)) for key, values in title_index.items()
        }
        self._fuzzy_names = {
            title: tuple(sorted(values)) for title, values in fuzzy_names.items()
        }

    @classmethod
    def load(
        cls,
        songs_path: Path | str = Path(__file__).with_name("guess_songs.json"),
        aliases_path: Path | str = Path(__file__).with_name("guess_aliases.json"),
        community_aliases_path: Path | str = Path(__file__).with_name(
            "guess_community_aliases.json"
        ),
    ) -> "GuessCatalog":
        raw_songs = json.loads(Path(songs_path).read_text(encoding="utf-8"))
        raw_aliases = json.loads(Path(aliases_path).read_text(encoding="utf-8"))
        raw_community = json.loads(
            Path(community_aliases_path).read_text(encoding="utf-8")
        )
        community_aliases = raw_community.get("aliases")
        if not isinstance(community_aliases, dict):
            raise ValueError("community alias snapshot is missing aliases")
        merged_aliases = defaultdict(list)
        for source in (community_aliases, raw_aliases):
            for title, values in source.items():
                merged_aliases[title].extend(values)
        songs = []
        for item in raw_songs:
            constants = item["constants"]
            songs.append(
                GuessSong(
                    title=item["title"],
                    pack=item["pack"],
                    pack_type=item["pack_type"],
                    side=item["side"],
                    bpm_min=float(item["bpm_min"]),
                    bpm_max=float(item["bpm_max"]),
                    pst=float(constants["PST"]),
                    prs=float(constants["PRS"]),
                    ftr=float(constants["FTR"]),
                    byd=(
                        float(constants["BYD"])
                        if constants.get("BYD") is not None
                        else None
                    ),
                    etr=(
                        float(constants["ETR"])
                        if constants.get("ETR") is not None
                        else None
                    ),
                    year=int(item["year"]),
                    official_aliases=tuple(item.get("official_aliases", [])),
                )
            )
        return cls(songs, dict(merged_aliases))

    def lookup(self, value: str) -> SongLookup:
        normalized = normalize_song_name(value)
        canonical = self._title_index.get(normalized, ())
        if len(canonical) == 1:
            return SongLookup("found", song=self.by_title[canonical[0]])
        if len(canonical) > 1:
            return SongLookup("ambiguous", candidates=canonical)
        candidates = self._index.get(normalized, ())
        if len(candidates) > 1:
            return SongLookup("ambiguous", candidates=candidates)
        if len(candidates) == 1:
            return SongLookup("found", song=self.by_title[candidates[0]])
        return self._fuzzy_lookup(normalized)

    def _fuzzy_lookup(self, normalized: str) -> SongLookup:
        if not normalized:
            return SongLookup("not_found")

        query_length = len(normalized)
        ranked = []
        all_scores = []
        for title, names in self._fuzzy_names.items():
            best_full = 0.0
            best_partial = 0.0
            best_distance = None
            for name in names:
                distance = _damerau_levenshtein_distance(normalized, name)
                full = 1.0 - distance / max(query_length, len(name))
                best_full = max(best_full, full)
                best_distance = distance if best_distance is None else min(
                    best_distance, distance
                )
                if query_length >= 6:
                    best_partial = max(
                        best_partial, _partial_similarity(normalized, name)
                    )
            score = max(best_full, best_partial)
            all_scores.append((score, title))
            if query_length <= 3:
                eligible = best_distance == 1
            elif query_length <= 5:
                eligible = best_full >= 0.76
            else:
                eligible = best_full >= 0.68 or (
                    query_length >= 8 and best_partial >= 0.92
                )
            if eligible:
                ranked.append(
                    (score, title.casefold(), title, best_full, best_partial, best_distance)
                )

        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        if not ranked or len(ranked) > FUZZY_MAX_CANDIDATES:
            return SongLookup("not_found")

        best = ranked[0]
        second_score = max(
            (score for score, title in all_scores if title != best[2]),
            default=0.0,
        )
        has_margin = best[0] - second_score >= FUZZY_AUTO_MARGIN
        auto_match = query_length >= 6 and has_margin and (
            best[5] == 1
            or best[3] >= 0.90
            or (query_length >= 8 and best[4] >= 0.92)
        )
        if auto_match:
            return SongLookup("found", song=self.by_title[best[2]])
        return SongLookup(
            "candidates", candidates=tuple(item[2] for item in ranked)
        )


@dataclass
class GuessSession:
    group_id: str
    starter_id: str
    answer: GuessSong
    started_at: float
    last_activity: float
    history: list[GuessSong] = field(default_factory=list)
    guessed_titles: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class GuessResult:
    status: str
    answer: Optional[GuessSong] = None
    guessed: Optional[GuessSong] = None
    history: tuple[GuessSong, ...] = ()
    candidates: tuple[str, ...] = ()

    @property
    def rounds(self) -> int:
        return len(self.history)


class GuessGameManager:
    def __init__(
        self,
        catalog: GuessCatalog,
        *,
        rng: Optional[random.Random] = None,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: int = GAME_TIMEOUT_SECONDS,
        max_rounds: int = GAME_MAX_ROUNDS,
    ) -> None:
        self.catalog = catalog
        self.rng = rng or random.Random()
        self.clock = clock
        self.timeout_seconds = timeout_seconds
        self.max_rounds = max_rounds
        self.sessions: dict[str, GuessSession] = {}
        self.last_answers: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, group_id: str) -> asyncio.Lock:
        return self._locks.setdefault(str(group_id), asyncio.Lock())

    def _choose_answer(self, group_id: str) -> GuessSong:
        previous = self.last_answers.get(group_id)
        choices = [song for song in self.catalog.songs if song.title != previous]
        return self.rng.choice(choices or list(self.catalog.songs))

    def _expired(self, session: GuessSession, now: float) -> bool:
        return now - session.last_activity >= self.timeout_seconds

    async def start(self, group_id: str, starter_id: str) -> GuessResult:
        group_id = str(group_id)
        async with self._lock(group_id):
            now = self.clock()
            current = self.sessions.get(group_id)
            if current and not self._expired(current, now):
                return GuessResult("already_active")
            expired_answer = None
            if current:
                expired_answer = current.answer
                self.last_answers[group_id] = current.answer.title
                self.sessions.pop(group_id, None)
            answer = self._choose_answer(group_id)
            self.sessions[group_id] = GuessSession(
                group_id=group_id,
                starter_id=str(starter_id),
                answer=answer,
                started_at=now,
                last_activity=now,
            )
            return GuessResult(
                "started_after_expiry" if expired_answer else "started",
                answer=expired_answer,
            )

    async def guess(self, group_id: str, value: str) -> GuessResult:
        group_id = str(group_id)
        async with self._lock(group_id):
            session = self.sessions.get(group_id)
            if session is None:
                return GuessResult("no_game")
            now = self.clock()
            if self._expired(session, now):
                self.sessions.pop(group_id, None)
                self.last_answers[group_id] = session.answer.title
                return GuessResult("expired", answer=session.answer)

            lookup = self.catalog.lookup(value)
            if lookup.status == "not_found":
                return GuessResult("not_found")
            if lookup.status == "ambiguous":
                return GuessResult("ambiguous", candidates=lookup.candidates)
            if lookup.status == "candidates":
                return GuessResult("candidates", candidates=lookup.candidates)
            assert lookup.song is not None
            guessed = lookup.song
            if guessed.title in session.guessed_titles:
                return GuessResult("duplicate", guessed=guessed)

            session.last_activity = now
            session.guessed_titles.add(guessed.title)
            session.history.append(guessed)
            history = tuple(session.history)
            if guessed.title == session.answer.title:
                self.sessions.pop(group_id, None)
                self.last_answers[group_id] = session.answer.title
                return GuessResult(
                    "correct", answer=session.answer, guessed=guessed, history=history
                )
            if len(history) >= self.max_rounds:
                self.sessions.pop(group_id, None)
                self.last_answers[group_id] = session.answer.title
                return GuessResult(
                    "round_limit",
                    answer=session.answer,
                    guessed=guessed,
                    history=history,
                )
            return GuessResult(
                "incorrect", answer=session.answer, guessed=guessed, history=history
            )

    async def stop(
        self, group_id: str, user_id: str, *, is_admin: bool = False
    ) -> GuessResult:
        group_id = str(group_id)
        async with self._lock(group_id):
            session = self.sessions.get(group_id)
            if session is None:
                return GuessResult("no_game")
            if str(user_id) != session.starter_id and not is_admin:
                return GuessResult("stop_forbidden")
            self.sessions.pop(group_id, None)
            self.last_answers[group_id] = session.answer.title
            return GuessResult("stopped", answer=session.answer)

    async def collect_expired(self) -> list[tuple[str, GuessSong]]:
        expired: list[tuple[str, GuessSong]] = []
        for group_id in list(self.sessions):
            async with self._lock(group_id):
                session = self.sessions.get(group_id)
                if session is None or not self._expired(session, self.clock()):
                    continue
                self.sessions.pop(group_id, None)
                self.last_answers[group_id] = session.answer.title
                expired.append((group_id, session.answer))
        return expired


def parse_guess_command(text: str) -> Optional[str]:
    match = re.fullmatch(r"\s*/guess(?:\s+(stop))?\s*", text, re.IGNORECASE)
    if not match:
        return None
    return "stop" if match.group(1) else "start"
