"""
Configurable Arcaea challenge state machine.

This module is independent from any QQ bot framework. Wire framework events to
ChallengeManager.start(), handle_recent_text(), handle_manual_score(), finish(),
reset(), cancel(), and check_timeout().
"""

from __future__ import annotations

import random
from typing import Optional

from challenge_config import ChallengeConfigStore
from challenge_models import ChallengeResponse, ChallengeSession
from challenge_runtime import ChallengeRuntimeMixin
from challenge_store import ChallengeStatsStore
from challenge_views import ChallengeViewsMixin


class ChallengeManager(ChallengeViewsMixin, ChallengeRuntimeMixin):
    def __init__(
        self,
        *,
        stats_store: Optional[ChallengeStatsStore] = None,
        challenge_store: Optional[ChallengeConfigStore] = None,
        rng: Optional[random.Random] = None,
    ):
        self.stats_store = stats_store or ChallengeStatsStore()
        self.challenge_store = challenge_store or ChallengeConfigStore()
        self.rng = rng or random.Random()
        self.sessions: dict[str, ChallengeSession] = {}
