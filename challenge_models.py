"""Shared data models for ARClass challenge core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


ROUND_TIMEOUT = timedelta(minutes=6)


@dataclass
class RecentTextResult:
    raw_text: str
    chart_raw: str
    score_raw: str
    song: Optional[str]
    difficulty: Optional[str]
    score: Optional[int]
    match_confidence: float
    play_potential_raw: str = ""
    play_potential: Optional[float] = None


@dataclass
class UnavailableSongResult:
    raw_text: str
    chart_raw: str
    song: Optional[str]
    difficulty: Optional[str]
    match_confidence: float


@dataclass
class RoundRecord:
    song: str
    difficulty: str
    level: float
    notes: int
    score: int
    faults: int
    max_pure: int
    hp_before: int
    hp_after: int
    submitted_at: str


@dataclass
class TimedChartResult:
    song: str
    difficulty: str
    level: float
    notes: int
    best_score: int = 0
    best_score_at: str = ""
    best_faults: Optional[int] = None
    best_faults_score: int = 0
    best_faults_at: str = ""
    best_max_pure: int = 0
    submission_count: int = 0

    @property
    def settled_faults(self) -> int:
        return self.best_faults if self.best_faults is not None else 2 * self.notes


@dataclass(frozen=True)
class ChallengeDefinition:
    name: str
    type: str
    clear_type: str
    initial_hp: int = 0
    heal_per_round: int = 0
    continue_on_zero_hp: bool = False
    strict_faults: bool = False
    strict_multiplier: int = 1
    clear_score: Optional[int] = None
    rounds: Optional[int] = None
    level_min: Optional[float] = None
    level_max: Optional[float] = None
    hp_stages: list[dict] = field(default_factory=list)
    time_limit_minutes: Optional[float] = None
    charts: list[dict] = field(default_factory=list)


@dataclass
class ChallengeSession:
    user_id: str
    challenge_name: str
    challenge_type: str
    clear_type: str
    started_at: datetime
    targets: list[dict]
    hp: int = 0
    initial_hp: int = 0
    max_hp: int = 0
    heal_per_round: int = 0
    hp_stages: list[dict] = field(default_factory=list)
    continue_on_zero_hp: bool = False
    strict_faults: bool = False
    strict_multiplier: int = 1
    clear_score: Optional[int] = None
    time_limit_minutes: Optional[float] = None
    current_index: int = 0
    round_announced_at: datetime = field(default_factory=datetime.now)
    failed_by_hp: bool = False
    total_score: int = 0
    records: list[RoundRecord] = field(default_factory=list)
    timed_results: dict[str, TimedChartResult] = field(default_factory=dict)
    recent_text_received_at: Optional[datetime] = None
    recent_text_raw: str = ""
    pending_manual_target: Optional[dict] = None
    random_excluded_chart_keys: set[str] = field(default_factory=set)

    @property
    def current_target(self) -> dict:
        return self.targets[self.current_index]

    @property
    def deadline(self) -> datetime:
        if self.challenge_type == "timed":
            assert self.time_limit_minutes is not None
            return self.started_at + timedelta(minutes=self.time_limit_minutes)
        return self.round_announced_at + ROUND_TIMEOUT

    @property
    def round_no(self) -> int:
        return self.current_index + 1

    @property
    def total_rounds(self) -> int:
        return len(self.targets)


@dataclass
class ChallengeResponse:
    status: str
    message: str
    session: Optional[ChallengeSession] = None
    parsed: Optional[RecentTextResult] = None
