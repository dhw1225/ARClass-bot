"""Challenge runtime state transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import scoring
from challenge_models import ChallengeDefinition, ChallengeResponse, ChallengeSession, RecentTextResult, RoundRecord, TimedChartResult
from challenge_recent import (
    MAX_SCORE,
    MIN_SCORE,
    _chart_key,
    is_relaxed_unavailable_song_match,
    parse_recent_text,
    parse_unavailable_song_text,
)
from challenge_targets import (
    build_targets,
    find_timed_target,
    next_infinite_target,
    replacement_random_target,
)


class ChallengeRuntimeMixin:
    def start(
        self,
        user_id: str,
        challenge_name: str,
        now: Optional[datetime] = None,
    ) -> ChallengeResponse:
        now = now or datetime.now()
        if user_id in self.sessions:
            session = self.sessions[user_id]
            return ChallengeResponse(
                status="already_active",
                message=self._format_session_message(
                    session, prefix="你已经有进行中的挑战。", now=now
                ),
                session=session,
            )

        definition = self.challenge_store.get(challenge_name)
        if definition is None:
            return ChallengeResponse(
                status="unknown_challenge",
                message=self._unknown_challenge_message(challenge_name),
            )

        targets, error = self._build_targets(definition)
        if error:
            return ChallengeResponse(status="error", message=error)

        session = ChallengeSession(
            user_id=user_id,
            challenge_name=definition.name,
            challenge_type=definition.type,
            clear_type=definition.clear_type,
            started_at=now,
            targets=targets,
            hp=definition.initial_hp,
            initial_hp=definition.initial_hp,
            max_hp=definition.initial_hp,
            heal_per_round=definition.heal_per_round,
            hp_stages=definition.hp_stages,
            continue_on_zero_hp=definition.continue_on_zero_hp,
            strict_faults=definition.strict_faults,
            strict_multiplier=definition.strict_multiplier,
            clear_score=definition.clear_score,
            time_limit_minutes=definition.time_limit_minutes,
            round_announced_at=now,
        )
        if session.challenge_type == "timed":
            session.timed_results = {
                _chart_key(target): TimedChartResult(
                    song=target["name"],
                    difficulty=target["difficulty"],
                    level=target["level"],
                    notes=target["notes"],
                )
                for target in session.targets
            }

        self.sessions[user_id] = session
        if session.clear_type == "score":
            prefix = f"{definition.name} 挑战开始！目标总分 {definition.clear_score}。"
        else:
            prefix = f"{definition.name} 挑战开始！初始血量 {definition.initial_hp}。"
        return ChallengeResponse(
            status="started",
            message=self._format_session_message(session, prefix=prefix, now=now),
            session=session,
        )

    def _build_targets(
        self, definition: ChallengeDefinition
    ) -> tuple[list[dict], Optional[str]]:
        return build_targets(definition, self.rng, self._format_song)

    def cancel(self, user_id: str) -> ChallengeResponse:
        session = self.sessions.pop(user_id, None)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="你当前没有进行中的挑战。"
            )
        return ChallengeResponse(
            status="cancelled_failed",
            message=(
                f"{session.challenge_name} 挑战已由用户中止，判定失败。"
                f"\n进度 {self._format_progress(session)}，"
                "本次挑战不结算总分，也不会写入最高分。"
            ),
            session=session,
        )

    def finish(self, user_id: str, now: Optional[datetime] = None) -> ChallengeResponse:
        now = now or datetime.now()
        session = self.sessions.get(user_id)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="你当前没有进行中的挑战。"
            )
        if session.challenge_type != "timed":
            return ChallengeResponse(
                status="finish_rejected",
                message="只有限时任意顺序段位可以提前结算；当前段位需要按轮次继续完成。",
                session=session,
            )
        return self._finish_timed(user_id, session, now, "用户提前结算。")

    def check_timeout(
        self, user_id: str, now: Optional[datetime] = None
    ) -> ChallengeResponse:
        now = now or datetime.now()
        session = self.sessions.get(user_id)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="你当前没有进行中的挑战。"
            )
        if now <= session.deadline:
            return ChallengeResponse(
                status="waiting",
                message=self._format_waiting_message(session, now),
                session=session,
            )
        if session.challenge_type == "timed":
            return self._finish_timed(user_id, session, now, "限时结束，自动结算。")

        self.sessions.pop(user_id, None)
        return ChallengeResponse(
            status="timeout_failed",
            message=(
                f"{session.challenge_name} 本轮已超时，挑战失败。目标是 {self._format_song(session.current_target)}，"
                f"截止时间 {session.deadline:%H:%M}。"
            ),
            session=session,
        )

    def handle_unavailable_song_text(
        self,
        user_id: str,
        text: str,
        now: Optional[datetime] = None,
    ) -> ChallengeResponse:
        now = now or datetime.now()
        session = self.sessions.get(user_id)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="你当前没有进行中的挑战。"
            )
        if now > session.deadline:
            if session.challenge_type == "timed":
                return self._finish_timed(
                    user_id, session, now, "收到缺曲回复时限时已结束，自动结算。"
                )
            self.sessions.pop(user_id, None)
            return ChallengeResponse(
                status="timeout_failed",
                message="收到缺曲回复时已经超过 6 分钟，本次挑战失败。",
                session=session,
            )
        if session.challenge_type not in {"random", "infinite"}:
            return ChallengeResponse(
                status="unavailable_song_ignored",
                message="",
                session=session,
            )

        parsed = parse_unavailable_song_text(text)
        if parsed.difficulty is None:
            return ChallengeResponse(
                status="unavailable_song_rejected",
                message=(
                    "未能识别 Yurisaki 缺曲回复中的曲目。"
                    f"\n当前目标：{self._format_song(session.current_target)}"
                ),
                session=session,
            )

        target = session.current_target
        target_key = _chart_key(target)
        parsed_key = (
            _chart_key(parsed.song, parsed.difficulty)
            if parsed.song is not None
            else None
        )
        if parsed_key != target_key and not is_relaxed_unavailable_song_match(
            parsed.chart_raw, parsed.difficulty, target
        ):
            return ChallengeResponse(
                status="unavailable_song_rejected",
                message=(
                    f"Yurisaki 缺曲回复谱面不匹配：识别为 {parsed.song or parsed.chart_raw} [{parsed.difficulty}]，"
                    f"当前目标是 {self._format_song(target)}。"
                    "\n只有当前目标可以触发自动切换。"
                ),
                session=session,
            )
        parsed_key = target_key

        replacement = self._replacement_random_target(session, parsed_key)
        if replacement is None:
            return ChallengeResponse(
                status="unavailable_song_no_candidate",
                message=(
                    f"已确认当前目标未解锁：{self._format_song(target)}。"
                    "\n但当前随机区间已无可替换候选，无法自动切换。"
                    "\n请联系管理员或取消挑战。"
                ),
                session=session,
            )

        session.random_excluded_chart_keys.add(parsed_key)
        session.targets[session.current_index] = replacement
        session.pending_manual_target = None
        session.recent_text_received_at = None
        session.recent_text_raw = ""
        session.round_announced_at = now
        prefix = (
            f"已确认当前目标未解锁：{self._format_song(target)}。"
            f"\n已切换本轮目标；{'下一首不会与上一首重复。' if session.challenge_type == 'infinite' else '该曲目的该难度本次挑战不会再次抽到。'}"
            "\n本轮 6 分钟计时已重置。"
        )
        return ChallengeResponse(
            status="random_target_replaced",
            message=self._format_target_message(session, prefix=prefix),
            session=session,
        )

    def _replacement_random_target(
        self, session: ChallengeSession, unavailable_key: str
    ) -> Optional[dict]:
        return replacement_random_target(
            session, unavailable_key, self.challenge_store, self.rng
        )

    def _next_infinite_target(
        self, session: ChallengeSession
    ) -> tuple[Optional[dict], Optional[str]]:
        return next_infinite_target(session, self.challenge_store, self.rng)

    @staticmethod
    def _infinite_cleared_charts(session: ChallengeSession) -> int:
        return sum(1 for record in session.records if record.hp_after > 0)

    def _apply_infinite_stage(self, session: ChallengeSession) -> Optional[str]:
        cleared = self._infinite_cleared_charts(session)
        active_stage = None
        for stage in session.hp_stages:
            if cleared >= int(stage["after_clears"]):
                active_stage = stage
            else:
                break
        if active_stage is None:
            return None

        new_max_hp = int(active_stage["max_hp"])
        new_heal = int(active_stage["heal_per_round"])
        old_max_hp = session.max_hp or session.initial_hp
        old_heal = session.heal_per_round
        if new_max_hp == old_max_hp and new_heal == old_heal:
            return None

        session.max_hp = new_max_hp
        session.heal_per_round = new_heal
        if session.hp > session.max_hp:
            session.hp = session.max_hp
        return f"已通关 {cleared} 首，血量上限降至 {new_max_hp}，每轮回血 {new_heal}。"

    def handle_recent_text(
        self,
        user_id: str,
        text: str,
        now: Optional[datetime] = None,
    ) -> ChallengeResponse:
        now = now or datetime.now()
        session = self.sessions.get(user_id)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="你当前没有进行中的挑战。"
            )
        if now > session.deadline:
            if session.challenge_type == "timed":
                return self._finish_timed(
                    user_id, session, now, "收到 recent text 时限时已结束，自动结算。"
                )
            self.sessions.pop(user_id, None)
            return ChallengeResponse(
                status="timeout_failed",
                message="收到 recent text 时已经超过 6 分钟，本次挑战失败。",
                session=session,
            )

        parsed = parse_recent_text(text)
        if session.challenge_type == "timed":
            return self._handle_timed_recent_text(user_id, session, parsed, text, now)
        return self._handle_ordered_recent_text(user_id, session, parsed, text, now)

    def _handle_ordered_recent_text(
        self,
        user_id: str,
        session: ChallengeSession,
        parsed: RecentTextResult,
        text: str,
        now: datetime,
    ) -> ChallengeResponse:
        target = session.current_target
        illegal = self._validate_parsed_chart(parsed)
        if illegal:
            return ChallengeResponse(
                status="illegal_recent_text",
                message=f"{illegal}\n当前目标：{self._format_song(target)}\n请重新发送 Yurisaki 的 /a recent text。",
                session=session,
                parsed=parsed,
            )
        if parsed.song != target["name"] or parsed.difficulty != target["difficulty"]:
            return ChallengeResponse(
                status="illegal_recent_text",
                message=(
                    f"recent text 谱面不匹配：识别为 {parsed.song} [{parsed.difficulty}]，"
                    f"当前目标是 {self._format_song(target)}。"
                    "\n请确认打的是本轮目标，并重新发送 /a recent text。"
                ),
                session=session,
                parsed=parsed,
            )
        if parsed.score is None:
            self._set_pending_manual(session, target, text, now)
            return ChallengeResponse(
                status="recent_text_needs_score",
                message=(
                    f"已确认本轮谱面 {parsed.song} [{parsed.difficulty}]，但未能读取 Score。"
                    "\n请 @ARClass score 你的分数，例如：@ARClass score 09994111。"
                ),
                session=session,
                parsed=parsed,
            )

        score_result = self._query_score(session, target, parsed.score)
        if score_result is None:
            self._set_pending_manual(session, target, text, now)
            return ChallengeResponse(
                status="recent_text_needs_score",
                message=(
                    f"读取到分数 {parsed.score:08d}，但无法用当前目标谱面计算错数。"
                    f"\n当前目标：{self._format_song(target)}"
                    "\n请 @ARClass score 手动补分，或重新发送 /a recent text。"
                ),
                session=session,
                parsed=parsed,
            )
        return self._apply_ordered_score(
            user_id, session, score_result, now, now, parsed=parsed
        )

    def _handle_timed_recent_text(
        self,
        user_id: str,
        session: ChallengeSession,
        parsed: RecentTextResult,
        text: str,
        now: datetime,
    ) -> ChallengeResponse:
        illegal = self._validate_parsed_chart(parsed)
        if illegal:
            return ChallengeResponse(
                status="illegal_recent_text",
                message=f"{illegal}\n本段位可提交曲目：{self._format_target_list(session.targets)}\n请重新发送 Yurisaki 的 /a recent text。",
                session=session,
                parsed=parsed,
            )
        target = self._find_timed_target(
            session, parsed.song or "", parsed.difficulty or ""
        )
        if target is None:
            return ChallengeResponse(
                status="illegal_recent_text",
                message=(
                    f"recent text 谱面不在本段位列表中：识别为 {parsed.song} [{parsed.difficulty}]。"
                    f"\n本段位可提交曲目：{self._format_target_list(session.targets)}"
                    "\n请确认打的是本段位指定曲目，并重新发送 /a recent text。"
                ),
                session=session,
                parsed=parsed,
            )
        if parsed.score is None:
            self._set_pending_manual(session, target, text, now)
            return ChallengeResponse(
                status="recent_text_needs_score",
                message=(
                    f"已确认谱面 {parsed.song} [{parsed.difficulty}]，但未能读取 Score。"
                    "\n请 @ARClass score 你的分数，例如：@ARClass score 09994111。"
                ),
                session=session,
                parsed=parsed,
            )

        score_result = self._query_score(session, target, parsed.score)
        if score_result is None:
            self._set_pending_manual(session, target, text, now)
            return ChallengeResponse(
                status="recent_text_needs_score",
                message=(
                    f"读取到分数 {parsed.score:08d}，但无法用该谱面计算错数。"
                    f"\n确认谱面：{self._format_song(target)}"
                    "\n请 @ARClass score 手动补分，或重新发送 /a recent text。"
                ),
                session=session,
                parsed=parsed,
            )
        return self._apply_timed_score(session, score_result, now, parsed=parsed)

    def handle_manual_score(
        self,
        user_id: str,
        score: int,
        *,
        now: Optional[datetime] = None,
    ) -> ChallengeResponse:
        now = now or datetime.now()
        session = self.sessions.get(user_id)
        if session is None:
            return ChallengeResponse(
                status="not_active", message="当前没有进行中的挑战。"
            )
        if now > session.deadline:
            if session.challenge_type == "timed":
                return self._finish_timed(
                    user_id, session, now, "提交手动分数时限时已结束，自动结算。"
                )
            self.sessions.pop(user_id, None)
            return ChallengeResponse(
                status="timeout_failed",
                message="提交手动分数时已经超过 6 分钟，本次挑战失败。",
                session=session,
            )
        if (
            session.pending_manual_target is None
            or session.recent_text_received_at is None
        ):
            return ChallengeResponse(
                status="manual_score_rejected",
                message="本轮还没有确认过匹配目标的 recent text，不能手动填分。请先发送 Yurisaki 的 /a recent text。",
                session=session,
            )
        if not (MIN_SCORE <= score <= MAX_SCORE):
            return ChallengeResponse(
                status="manual_score_rejected",
                message=f"手动分数 {score} 不在可接受范围内，请检查分数。",
                session=session,
            )

        target = session.pending_manual_target
        score_result = self._query_score(session, target, score)
        if score_result is None:
            return ChallengeResponse(
                status="manual_score_rejected",
                message=f"手动分数 {score:08d} 无法用确认谱面计算错数，请检查分数。",
                session=session,
            )
        submitted_at = session.recent_text_received_at
        session.recent_text_received_at = None
        session.recent_text_raw = ""
        session.pending_manual_target = None
        if session.challenge_type == "timed":
            return self._apply_timed_score(session, score_result, submitted_at)
        return self._apply_ordered_score(
            user_id, session, score_result, submitted_at, now
        )

    def _validate_parsed_chart(self, parsed: RecentTextResult) -> Optional[str]:
        if not parsed.chart_raw:
            return "未能读取 recent text 的 Chart 行。"
        if parsed.song is None or parsed.difficulty is None:
            return f"未能确认 recent text 的谱面：{parsed.chart_raw or '(空)'}。"
        return None

    def _set_pending_manual(
        self,
        session: ChallengeSession,
        target: dict,
        text: str,
        now: datetime,
    ) -> None:
        session.pending_manual_target = target
        session.recent_text_received_at = now
        session.recent_text_raw = text

    def _query_score(
        self,
        session: ChallengeSession,
        target: dict,
        score: int,
    ) -> Optional[dict]:
        score_result = scoring.query(
            target["name"], score, difficulty=target["difficulty"]
        )
        if score_result is None:
            return None
        raw_faults = score_result["faults"]
        effective = scoring.effective_faults(
            score_result["notes"],
            raw_faults,
            score_result["max_pure"],
            strict_faults=session.strict_faults,
            strict_multiplier=session.strict_multiplier,
        )
        score_result = dict(score_result)
        score_result["raw_faults"] = raw_faults
        score_result["faults"] = effective
        return score_result

    def _apply_ordered_score(
        self,
        user_id: str,
        session: ChallengeSession,
        score_result: dict,
        submitted_at: datetime,
        now: datetime,
        *,
        parsed: Optional[RecentTextResult] = None,
    ) -> ChallengeResponse:
        hp_before = session.hp
        hp_after = session.hp
        hp_zeroed_this_round = False

        if session.clear_type == "hp":
            if session.failed_by_hp:
                session.hp = 0
                hp_before = 0
                hp_after = 0
            else:
                session.hp -= score_result["faults"]
                if session.hp <= 0:
                    session.hp = 0
                    session.failed_by_hp = True
                    hp_zeroed_this_round = True
                hp_after = session.hp
            if session.hp <= 0:
                session.failed_by_hp = True

        session.total_score += score_result["score"]
        session.records.append(
            RoundRecord(
                song=score_result["song"],
                difficulty=score_result["difficulty"],
                level=session.current_target["level"],
                notes=score_result["notes"],
                score=score_result["score"],
                faults=score_result["faults"],
                max_pure=score_result["max_pure"],
                hp_before=hp_before,
                hp_after=hp_after,
                submitted_at=submitted_at.isoformat(timespec="minutes"),
            )
        )
        session.recent_text_received_at = None
        session.recent_text_raw = ""
        session.pending_manual_target = None

        round_summary = self._format_round_summary(
            session, score_result, hp_before, hp_after
        )
        if session.clear_type == "hp" and hp_zeroed_this_round:
            if session.challenge_type == "infinite":
                return self._finish_infinite(user_id, session, now, round_summary)
            if session.continue_on_zero_hp:
                round_summary += "\n血量已归零，挑战失败，但可以继续游玩。"
            else:
                return self._fail_by_hp(user_id, session, round_summary)

        if session.challenge_type == "infinite":
            next_target, error = self._next_infinite_target(session)
            if error or next_target is None:
                return ChallengeResponse(
                    status="error",
                    message=error or "无法生成无限段下一首。",
                    session=session,
                    parsed=parsed,
                )
            session.targets.append(next_target)
        elif session.current_index + 1 >= session.total_rounds:
            return self._finish_ordered(user_id, session, now, round_summary)

        session.current_index += 1
        stage_message = None
        if session.challenge_type == "infinite":
            stage_message = self._apply_infinite_stage(session)
        healed = 0
        if session.clear_type == "hp" and not session.failed_by_hp:
            hp_before_heal = session.hp
            session.hp = min(self._hp_cap(session), session.hp + session.heal_per_round)
            healed = session.hp - hp_before_heal
        session.round_announced_at = now

        if session.clear_type == "score":
            prefix = (
                f"{round_summary}\n当前总分 {session.total_score}。"
                f"{self._format_score_average_lines(session)}"
            )
        elif session.failed_by_hp or session.heal_per_round == 0:
            prefix = (
                f"{round_summary}\n当前血量 {self._format_hp(session, session.hp)}。"
            )
        else:
            prefix = (
                f"{round_summary}\n回复 {healed} 血，"
                f"当前血量 {self._format_hp(session, session.hp)}。"
            )
        if stage_message:
            prefix = f"{prefix}\n{stage_message}"
        return ChallengeResponse(
            status="round_completed",
            message=self._format_session_message(session, prefix=prefix, now=now),
            session=session,
            parsed=parsed,
        )

    def _apply_timed_score(
        self,
        session: ChallengeSession,
        score_result: dict,
        submitted_at: datetime,
        *,
        parsed: Optional[RecentTextResult] = None,
    ) -> ChallengeResponse:
        key = _chart_key(score_result["song"], score_result["difficulty"])
        result = session.timed_results[key]
        result.submission_count += 1
        submitted_text = submitted_at.isoformat(timespec="minutes")
        score_updated = False
        faults_updated = False
        if score_result["score"] > result.best_score:
            result.best_score = score_result["score"]
            result.best_score_at = submitted_text
            score_updated = True
        if result.best_faults is None or score_result["faults"] < result.best_faults:
            result.best_faults = score_result["faults"]
            result.best_faults_score = score_result["score"]
            result.best_faults_at = submitted_text
            result.best_max_pure = score_result["max_pure"]
            faults_updated = True
        session.recent_text_received_at = None
        session.recent_text_raw = ""
        session.pending_manual_target = None

        submitted_count = sum(
            1 for item in session.timed_results.values() if item.submission_count > 0
        )
        remaining = max(0, int((session.deadline - submitted_at).total_seconds()))
        updates = []
        if score_updated:
            updates.append("最高分已更新")
        if faults_updated:
            updates.append("最低错数已更新")
        if not updates:
            updates.append("未刷新最佳记录")
        message = (
            f"已记录 {score_result['song']} [{score_result['difficulty']}] "
            f"{score_result['score']:08d}，{score_result['faults']} 错，{'，'.join(updates)}。"
            f"\n进度 {submitted_count}/{session.total_rounds}，剩余 {remaining} 秒。"
            f"{self._format_score_average_lines(session)}"
            f"\n当前最佳：\n{self._format_timed_progress(session)}"
        )
        return ChallengeResponse(
            status="timed_score_recorded",
            message=message,
            session=session,
            parsed=parsed,
        )

    def _fail_by_hp(
        self,
        user_id: str,
        session: ChallengeSession,
        round_summary: str,
    ) -> ChallengeResponse:
        self.sessions.pop(user_id, None)
        return ChallengeResponse(
            status="hp_failed",
            message=(f"{round_summary}\n{session.challenge_name} 挑战失败。"),
            session=session,
        )

    def _finish_ordered(
        self,
        user_id: str,
        session: ChallengeSession,
        now: datetime,
        round_summary: str,
    ) -> ChallengeResponse:
        self.sessions.pop(user_id, None)
        total_faults = sum(record.faults for record in session.records)
        if session.clear_type == "score":
            assert session.clear_score is not None
            passed = session.total_score >= session.clear_score
            clear_line = f"目标总分 {session.clear_score}。"
        else:
            passed = not session.failed_by_hp and session.hp > 0
            clear_line = f"剩余血量 {self._format_hp(session, session.hp)}。"
        return self._record_and_format_finish(
            user_id,
            session,
            now,
            passed,
            total_faults,
            f"{round_summary}\n{session.challenge_name} 挑战结束：{'通过' if passed else '失败'}。",
            f"{self._format_records(session.records)}\n总分 {session.total_score}，总错数 {total_faults}，{clear_line}",
        )

    def _finish_infinite(
        self,
        user_id: str,
        session: ChallengeSession,
        now: datetime,
        round_summary: str,
    ) -> ChallengeResponse:
        self.sessions.pop(user_id, None)
        total_faults = sum(record.faults for record in session.records)
        cleared_charts = self._infinite_cleared_charts(session)
        return self._record_and_format_finish(
            user_id,
            session,
            now,
            False,
            total_faults,
            f"{round_summary}\n{session.challenge_name} 无限段结束。",
            f"{self._format_records(session.records)}\n通关曲数 {cleared_charts}，总分 {session.total_score}，总错数 {total_faults}，最终血量 {self._format_hp(session, session.hp)}。",
        )

    def _finish_timed(
        self,
        user_id: str,
        session: ChallengeSession,
        now: datetime,
        reason: str,
    ) -> ChallengeResponse:
        self.sessions.pop(user_id, None)
        records = self._settled_timed_records(session, now)
        session.records = records
        session.total_score = sum(record.score for record in records)
        total_faults = sum(record.faults for record in records)
        if session.clear_type == "score":
            assert session.clear_score is not None
            passed = session.total_score >= session.clear_score
            clear_line = f"目标总分 {session.clear_score}。"
        else:
            session.hp = max(0, session.initial_hp - total_faults)
            passed = session.initial_hp - total_faults > 0
            clear_line = (
                f"初始血量 {session.initial_hp}，"
                f"最终血量 {self._format_hp(session, session.hp)}。"
            )
        return self._record_and_format_finish(
            user_id,
            session,
            now,
            passed,
            total_faults,
            f"{reason}\n{session.challenge_name} 挑战结束：{'通过' if passed else '失败'}。",
            f"{self._format_records(records)}\n总分 {session.total_score}，总错数 {total_faults}，{clear_line}",
        )

    def _record_and_format_finish(
        self,
        user_id: str,
        session: ChallengeSession,
        now: datetime,
        passed: bool,
        total_faults: int,
        title: str,
        details: str,
    ) -> ChallengeResponse:
        stats = self.stats_store.record_completed(
            user_id,
            session.challenge_name,
            session.total_score,
            passed,
            now,
            clear_type=session.clear_type,
            total_faults=total_faults,
            challenge_type=session.challenge_type,
            cleared_charts=self._recorded_cleared_charts(session, passed),
        )
        best = (
            stats["best_scores"][0]["score"]
            if stats.get("best_scores")
            else session.total_score
        )
        if session.challenge_type == "infinite":
            best_score = stats["best_scores"][0] if stats.get("best_scores") else {}
            suffix = (
                f"\n{session.challenge_name} 个人最佳通关 {int(best_score.get('cleared_charts', 0))} 首，"
                f"个人最高总分 {int(best_score.get('score', best))}。"
            )
        else:
            suffix = f"\n{session.challenge_name} 累计通过 {stats['pass_count']} 次，个人最高总分 {best}。"
        return ChallengeResponse(
            status="finished_passed" if passed else "finished_failed",
            message=f"{title}\n{details}{suffix}",
            session=session,
        )

    def _settled_timed_records(
        self, session: ChallengeSession, now: datetime
    ) -> list[RoundRecord]:
        records: list[RoundRecord] = []
        final_hp = session.hp
        for target in session.targets:
            result = session.timed_results[_chart_key(target)]
            faults = result.settled_faults
            score = result.best_score
            records.append(
                RoundRecord(
                    song=target["name"],
                    difficulty=target["difficulty"],
                    level=target["level"],
                    notes=target["notes"],
                    score=score,
                    faults=faults,
                    max_pure=result.best_max_pure,
                    hp_before=final_hp,
                    hp_after=final_hp,
                    submitted_at=result.best_score_at
                    or result.best_faults_at
                    or now.isoformat(timespec="minutes"),
                )
            )
        return records

    def _find_timed_target(
        self, session: ChallengeSession, song: str, difficulty: str
    ) -> Optional[dict]:
        return find_timed_target(session, song, difficulty)
