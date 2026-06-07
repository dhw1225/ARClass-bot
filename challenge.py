"""
Configurable Arcaea challenge state machine.

This module is independent from any QQ bot framework. Wire framework events to
ChallengeManager.start(), handle_recent_text(), handle_manual_score(), finish(),
cancel(), and check_timeout().
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Optional

import scoring
from challenge_config import ChallengeConfigStore
from challenge_models import (
    ROUND_TIMEOUT,
    ChallengeDefinition,
    ChallengeResponse,
    ChallengeSession,
    RecentTextResult,
    RoundRecord,
    TimedChartResult,
)
from challenge_recent import (
    MAX_SCORE,
    MIN_SCORE,
    _chart_key,
    is_relaxed_unavailable_song_match,
    parse_recent_text,
    parse_unavailable_song_text,
)
from challenge_store import ChallengeStatsStore


class ChallengeManager:
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

    def help_message(self) -> str:
        return (
            "Arcaea 段位 bot 使用说明\n"
            "@ARClass /help：查看使用说明。\n"
            "@ARClass /cha <挑战名称>：开始指定段位。\n"
            "@ARClass /cha list：查看可用挑战列表。\n"
            "@ARClass /cha <挑战名称> help：查看段位规则说明。\n"
            "@ARClass status：查看当前挑战状态。\n"
            "@ARClass cancel：中止当前挑战，本次判定失败且不写入成绩。\n"
            "@ARClass finish：提前结算限时段位。\n"
            "@ARClass /query：查询自己的段位记录。\n"
            "@ARClass /rank <段位名>：查看指定段位排行榜。\n"
            "@ARClass score <分数>：仅在 ARClass 已确认 recent text 谱面但无法读分时手动补分。\n"
            "无限段位会持续随机出歌直到血量归零，排行榜按通关曲数排序。\n"
            "挑战中请让 Yurisaki 发送 /a recent text；ARClass 只接受可信 Yurisaki 账号且 @ 到挑战用户的 recent text。"
        )

    def challenge_list_message(self) -> str:
        definitions = self.challenge_store.definitions()
        if not definitions:
            return "当前没有可用挑战。"
        lines = ["可用挑战："]
        for index, definition in enumerate(definitions, 1):
            lines.append(
                f"{index}. {definition.name} "
                f"[{self._challenge_type_short_label(definition.type)}/{self._clear_type_label(definition.clear_type)}]"
            )
        lines.append("发送 @ARClass /cha <挑战名称> help 查看规则说明。")
        return "\n".join(lines)

    def query_user_message(self, user_id: str) -> str:
        stats_challenges = self.stats_store.get_user_challenges(user_id)
        challenge_names = self._ordered_query_challenge_names(stats_challenges)
        lines = [
            "ARClass 段位查询",
            f"QQ：{user_id}",
        ]
        if not challenge_names:
            lines.append("暂无段位记录。")
            return "\n".join(lines)

        lines.append(f"已有记录段位：{len(challenge_names)}")
        for index, challenge_name in enumerate(challenge_names, 1):
            lines.append("")
            lines.extend(
                self._format_query_challenge_lines(
                    index,
                    challenge_name,
                    stats_challenges.get(challenge_name),
                )
            )
        return "\n".join(lines)

    def _ordered_query_challenge_names(
        self, stats_challenges: dict[str, dict]
    ) -> list[str]:
        names = set(stats_challenges)
        ordered = [
            definition.name
            for definition in self.challenge_store.definitions()
            if definition.name in names
        ]
        known = set(ordered)
        ordered.extend(name for name in sorted(names) if name not in known)
        return ordered

    def _format_query_challenge_lines(
        self,
        index: int,
        challenge_name: str,
        stats: Optional[dict],
    ) -> list[str]:
        definition = self.challenge_store.get(challenge_name)
        if definition is not None:
            type_line = (
                f"{self._challenge_type_short_label(definition.type)}/"
                f"{self._clear_type_label(definition.clear_type)}"
            )
        else:
            clear_type = (stats or {}).get("clear_type", "unknown")
            challenge_type = (stats or {}).get("challenge_type", "unknown")
            type_line = (
                f"{self._challenge_type_short_label(str(challenge_type))}/"
                f"{self._clear_type_label(str(clear_type))}"
            )

        lines = [f"{index}. {challenge_name} [{type_line}]"]
        pass_count = int((stats or {}).get("pass_count", 0))
        best_scores = list((stats or {}).get("best_scores", []))
        best = best_scores[0] if best_scores else None
        challenge_type = (
            definition.type
            if definition is not None
            else str((stats or {}).get("challenge_type", ""))
        )
        if challenge_type == "infinite":
            record_count = len(best_scores)
            best_cleared = int((best or {}).get("cleared_charts", 0))
            lines.append(f"游玩记录：{record_count} 次，最佳通关曲数 {best_cleared} 首")
            if best is None:
                lines.append("个人最佳：暂无成绩")
            else:
                lines.append(
                    f"个人最佳：通关 {best_cleared} 首，总分 {int(best.get('score', 0))}，"
                    f"总错数 {int(best.get('total_faults', 0))}"
                )
        else:
            passed = pass_count > 0 or any(item.get("passed") for item in best_scores)
            lines.append(f"通关情况：{'已通过' if passed else '未通过'}，通过 {pass_count} 次")
            if best is None:
                lines.append("个人最高总分：暂无成绩")
            else:
                best_status = "通过" if best.get("passed") else "未通过"
                lines.append(
                    f"个人最高总分：{int(best.get('score', 0))}（{best_status}，总错数 {int(best.get('total_faults', 0))}）"
                )
        return lines

    def challenge_rank_user_ids(self, challenge_name: str) -> list[str]:
        records = self._challenge_rank_records(challenge_name)
        user_ids = [record["user_id"] for record in records]
        first_clear = self._challenge_first_clear_record(challenge_name.strip())
        if isinstance(first_clear, dict):
            first_clear_user_id = str(first_clear.get("user_id", "")).strip()
            if first_clear_user_id and first_clear_user_id not in user_ids:
                user_ids.append(first_clear_user_id)
        return user_ids

    def challenge_rank_message(
        self,
        challenge_name: str,
        display_names: Optional[dict[str, str]] = None,
    ) -> str:
        display_names = display_names or {}
        normalized_name = challenge_name.strip()
        records = self._challenge_rank_records(normalized_name)
        definition = self.challenge_store.get(normalized_name)
        title_name = definition.name if definition is not None else normalized_name
        lines = [f"ARClass 段位排行榜：{title_name}"]
        if definition is not None and definition.type == "infinite":
            lines.append(f"记录人数：{len(records)}")
        else:
            lines.append(f"通过人数：{len(self.stats_store.passed_user_ids(normalized_name))}")
            first_clear = self._challenge_first_clear_record(normalized_name)
            first_clear_line = self._format_first_clear_line(first_clear, display_names)
            if first_clear_line:
                lines.append(first_clear_line)
        if not records:
            if definition is None:
                return f"未知段位且暂无历史记录：{normalized_name}"
            return "\n".join([*lines, "暂无成绩记录。"])

        for index, record in enumerate(records, 1):
            best = record["best"]
            if definition is not None and definition.type == "infinite":
                lines.append(
                    f"{index}. {self._display_user(record['user_id'], display_names)}："
                    f"通关 {int(best.get('cleared_charts', 0))} 首，总分 {int(best.get('score', 0))}，"
                    f"总错数 {int(best.get('total_faults', 0))}，"
                    f"记录 {int(record.get('record_count', 0))} 次，"
                    f"{best.get('finished_at', '时间未知')}"
                )
            else:
                status = "通过" if best.get("passed") else "未通过"
                lines.append(
                    f"{index}. {self._display_user(record['user_id'], display_names)}："
                    f"{int(best.get('score', 0))}，总错数 {int(best.get('total_faults', 0))}，"
                    f"{status}，通过 {int(record.get('pass_count', 0))} 次，"
                    f"{best.get('finished_at', '时间未知')}"
                )
        return "\n".join(lines)

    def _challenge_rank_records(self, challenge_name: str) -> list[dict]:
        records = self.stats_store.get_challenge_user_records(challenge_name)
        definition = self.challenge_store.get(challenge_name)
        if definition is not None and definition.type == "infinite":
            records.sort(
                key=lambda record: (
                    -int(record["best"].get("cleared_charts", 0)),
                    -int(record["best"].get("score", 0)),
                    int(record["best"].get("total_faults", 0)),
                    str(record["best"].get("finished_at", "")),
                    record["user_id"],
                )
            )
        else:
            records.sort(
                key=lambda record: (
                    -int(record["best"].get("score", 0)),
                    int(record["best"].get("total_faults", 0)),
                    str(record["best"].get("finished_at", "")),
                    record["user_id"],
                )
            )
        return records

    @staticmethod
    def _display_user(user_id: str, display_names: dict[str, str]) -> str:
        name = display_names.get(user_id, "").strip()
        return name or user_id

    def _challenge_first_clear_record(
        self, challenge_name: str
    ) -> Optional[dict]:
        record = self.stats_store.first_clear_record(challenge_name)
        if record is not None:
            record["source"] = "stats"
        return record

    def _format_first_clear_line(
        self, first_clear: Optional[dict], display_names: dict[str, str]
    ) -> Optional[str]:
        if not isinstance(first_clear, dict):
            return None
        user_id = str(first_clear.get("user_id", "")).strip()
        if not user_id:
            return None

        details = []
        achieved_at = str(first_clear.get("achieved_at", "")).strip()
        if achieved_at:
            details.append(f"时间 {achieved_at}")
        details.append(f"总分 {int(first_clear.get('score', 0))}")
        details.append(f"总错数 {int(first_clear.get('total_faults', 0))}")
        return f"首通：{self._display_user(user_id, display_names)}（{'，'.join(details)}）"

    def active_session_user_ids(self) -> list[str]:
        return sorted(self.sessions)

    def active_sessions_message(
        self,
        display_names: Optional[dict[str, str]] = None,
        now: Optional[datetime] = None,
    ) -> str:
        display_names = display_names or {}
        now = now or datetime.now()
        lines = ["ARClass 当前进行中挑战"]
        if not self.sessions:
            lines.append("暂无进行中的挑战。")
            return "\n".join(lines)

        for index, user_id in enumerate(sorted(self.sessions), 1):
            session = self.sessions[user_id]
            remaining = max(0, int((session.deadline - now).total_seconds()))
            lines.append("")
            lines.append(f"{index}. {self._display_user(user_id, display_names)}")
            lines.append(f"段位：{session.challenge_name}")
            lines.append(
                f"类型：{self._challenge_type_short_label(session.challenge_type)}/"
                f"{self._clear_type_label(session.clear_type)}"
            )
            lines.append(f"进度：{self._format_active_progress(session)}")
            lines.append(self._format_active_state(session))
            lines.append(f"剩余：{remaining} 秒，截止 {session.deadline:%H:%M:%S}")
        return "\n".join(lines)

    def _format_active_progress(self, session: ChallengeSession) -> str:
        if session.challenge_type == "timed":
            submitted = sum(
                1
                for result in session.timed_results.values()
                if result.submission_count > 0
            )
            return f"{submitted}/{session.total_rounds}"
        if session.challenge_type == "infinite":
            return f"通关 {self._infinite_cleared_charts(session)} 首，当前第 {session.round_no} 首"
        return f"{session.current_index}/{session.total_rounds}"

    def _format_active_state(self, session: ChallengeSession) -> str:
        if session.challenge_type == "timed":
            submitted = sum(
                1
                for result in session.timed_results.values()
                if result.submission_count > 0
            )
            if session.clear_type == "score":
                return f"当前总分：{sum(result.best_score for result in session.timed_results.values())}，已提交 {submitted} 首"
            total_faults = sum(
                result.best_faults or 0 for result in session.timed_results.values()
            )
            hp = max(0, session.initial_hp - total_faults)
            return f"当前血量估算：{hp}/{session.initial_hp}，已提交 {submitted} 首"

        target = self._format_song(session.current_target)
        if session.clear_type == "score":
            return f"当前总分：{session.total_score}，当前目标：{target}"
        return f"当前血量：{self._format_hp(session, session.hp)}，当前目标：{target}"

    def challenge_help_message(self, challenge_name: str) -> str:
        definition = self.challenge_store.get(challenge_name)
        if definition is None:
            return self._unknown_challenge_message(challenge_name)

        lines = [
            f"{definition.name} 规则说明",
            f"类型：{definition.type}（{self._challenge_type_label(definition.type)}）",
            f"通关方式：{definition.clear_type}（{self._clear_type_label(definition.clear_type)}）",
        ]
        if definition.type in {"random", "infinite"}:
            assert definition.level_min is not None
            assert definition.level_max is not None
            if definition.type == "infinite":
                lines.append("轮数：无限，血量归零后结算")
            else:
                assert definition.rounds is not None
                lines.append(f"轮数：{definition.rounds}")
            lines.append(f"定数范围：{definition.level_min:g}-{definition.level_max:g}")
            lines.append(
                "若当前随机目标未解锁，可让 Yurisaki 查询 /a song 曲名；"
                "ARClass 识别未游玩回复后会切换本轮目标。"
            )
            if definition.type == "infinite" and definition.hp_stages:
                stage_text = "；".join(
                    f"通过 {stage['after_clears']} 首后：血量上限 {stage['max_hp']}，每轮回血 {stage['heal_per_round']}"
                    for stage in definition.hp_stages
                )
                lines.append(f"血量阶段：{stage_text}")
        else:
            if definition.type == "timed":
                assert definition.time_limit_minutes is not None
                lines.append(f"限时：{definition.time_limit_minutes:g} 分钟")
            lines.append(f"曲目（{len(definition.charts)} 首）：")
            for index, chart in enumerate(definition.charts, 1):
                lines.append(
                    f"{index}. {chart['name']} [{str(chart['difficulty']).upper()}]"
                )

        if definition.clear_type == "hp":
            lines.append(f"初始血量：{definition.initial_hp}")
            if definition.type in {"random", "fixed", "infinite"}:
                lines.append(f"每轮回血：{definition.heal_per_round}")
                zero_hp = (
                    "继续游玩但判定失败"
                    if definition.continue_on_zero_hp
                    else "立即失败"
                )
                lines.append(f"HP 清零：{zero_hp}")
            if definition.strict_faults:
                strict = self._format_strict_faults_rule(definition.strict_multiplier)
            else:
                strict = "关闭"
            lines.append(f"严格错数：{strict}")
        else:
            assert definition.clear_score is not None
            lines.append(f"目标总分：{definition.clear_score}")
            lines.append(
                f"过段所需平均分数：{self._required_average_score_for_definition(definition)}"
            )
        return "\n".join(lines)

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
        db = scoring.get_db()
        if definition.type in {"random", "infinite"}:
            assert definition.rounds is not None
            assert definition.level_min is not None
            assert definition.level_max is not None
            candidates = [
                song
                for song in db.songs
                if definition.level_min <= float(song["level"]) <= definition.level_max
            ]
            if definition.type == "infinite":
                if not candidates:
                    return [], (
                        f"{definition.name} 候选谱面不足："
                        f"当前区间 {definition.level_min:g}-{definition.level_max:g} 没有谱面。"
                    )
                return [self.rng.choice(candidates)], None
            if len(candidates) < definition.rounds:
                return [], (
                    f"{definition.name} 候选谱面不足：需要 {definition.rounds} 首，"
                    f"当前区间 {definition.level_min:g}-{definition.level_max:g} 只有 {len(candidates)} 首。"
                )
            return self.rng.sample(candidates, definition.rounds), None

        targets: list[dict] = []
        seen: set[str] = set()
        for chart in definition.charts:
            if not isinstance(chart, dict):
                return [], f"{definition.name} 曲目格式错误。"
            song_name = str(chart.get("name", "")).strip()
            difficulty = str(chart.get("difficulty", "")).strip().upper()
            target = db.get_by_name_and_difficulty(song_name, difficulty)
            if target is None:
                return [], f"{definition.name} 找不到谱面：{song_name} [{difficulty}]。"
            key = _chart_key(target)
            if definition.type == "timed" and key in seen:
                return (
                    [],
                    f"{definition.name} timed 曲目不能重复：{self._format_song(target)}。",
                )
            seen.add(key)
            targets.append(target)
        return targets, None

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
        definition = self.challenge_store.get(session.challenge_name)
        if definition is None or definition.type not in {"random", "infinite"}:
            return None
        assert definition.level_min is not None
        assert definition.level_max is not None

        excluded = set(session.random_excluded_chart_keys)
        excluded.add(unavailable_key)
        if session.challenge_type == "infinite":
            if session.current_index > 0:
                excluded.add(_chart_key(session.targets[session.current_index - 1]))
        else:
            excluded.update(_chart_key(target) for target in session.targets)
            excluded.update(
                _chart_key(record.song, record.difficulty) for record in session.records
            )
        candidates = [
            song
            for song in scoring.get_db().songs
            if definition.level_min <= float(song["level"]) <= definition.level_max
            and _chart_key(song) not in excluded
        ]
        if not candidates:
            return None
        return self.rng.choice(candidates)

    def _next_infinite_target(
        self, session: ChallengeSession
    ) -> tuple[Optional[dict], Optional[str]]:
        definition = self.challenge_store.get(session.challenge_name)
        if definition is None or definition.type != "infinite":
            return None, "当前段位不是无限段，无法生成下一首。"
        assert definition.level_min is not None
        assert definition.level_max is not None

        excluded = set(session.random_excluded_chart_keys)
        excluded.add(_chart_key(session.current_target))
        candidates = [
            song
            for song in scoring.get_db().songs
            if definition.level_min <= float(song["level"]) <= definition.level_max
            and _chart_key(song) not in excluded
        ]
        if not candidates:
            return None, (
                f"{session.challenge_name} 候选谱面不足："
                f"当前区间 {definition.level_min:g}-{definition.level_max:g} 没有可作为下一首的谱面。"
            )
        return self.rng.choice(candidates), None

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
            cleared_charts > 0,
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
        key = _chart_key(song, difficulty)
        for target in session.targets:
            if _chart_key(target) == key:
                return target
        return None

    def _format_round_summary(
        self,
        session: ChallengeSession,
        score_result: dict,
        hp_before: int,
        hp_after: int,
    ) -> str:
        fault_label = "严格错数" if session.strict_faults else "错"
        base = (
            f"{self._format_round_label(session)}完成："
            f"{score_result['song']} [{score_result['difficulty']}] "
            f"{score_result['score']:08d}，{score_result['faults']} {fault_label}"
        )
        if session.clear_type == "hp":
            return (
                f"{base}，血量 "
                f"{self._format_hp(session, hp_before)}->{self._format_hp(session, hp_after)}。"
            )
        return f"{base}。"

    def _format_session_message(
        self,
        session: ChallengeSession,
        *,
        prefix: str,
        now: Optional[datetime] = None,
    ) -> str:
        if session.challenge_type == "timed":
            now = now or datetime.now()
            remaining = max(0, int((session.deadline - now).total_seconds()))
            clear_line = (
                f"目标总分 {session.clear_score}"
                if session.clear_type == "score"
                else f"初始血量 {session.initial_hp}"
            )
            return (
                f"{prefix}\n"
                f"{session.challenge_name} 限时 {session.time_limit_minutes:g} 分钟，剩余 {remaining} 秒，{clear_line}。"
                f"\n指定曲目：{self._format_target_list(session.targets)}"
                f"{self._format_score_average_lines(session)}"
                "\n限时内可任意顺序、任意次数发送 Yurisaki 的 /a recent text；发送 @ARClass finish 可提前结算。"
            )
        return self._format_target_message(session, prefix=prefix)

    def _format_waiting_message(self, session: ChallengeSession, now: datetime) -> str:
        remaining = max(0, int((session.deadline - now).total_seconds()))
        if session.challenge_type == "timed":
            submitted_count = sum(
                1
                for item in session.timed_results.values()
                if item.submission_count > 0
            )
            return (
                f"{session.challenge_name} 限时段位进行中，剩余 {remaining} 秒。"
                f"\n进度 {submitted_count}/{session.total_rounds}。"
                f"{self._format_score_average_lines(session)}"
                f"\n当前最佳：\n{self._format_timed_progress(session)}"
            )
        target_line = f"{session.challenge_name} {self._format_round_label(session)}：{self._format_song(session.current_target)}"
        average_lines = self._format_score_average_lines(session)
        random_hint = self._format_random_unavailable_hint(session)
        if session.recent_text_received_at is not None:
            return f"{target_line}{average_lines}\n已收到本轮 recent text，等待手动分数，剩余 {remaining} 秒。{random_hint}"
        return f"{target_line}{average_lines}\n仍在等待本轮 recent text，剩余 {remaining} 秒。{random_hint}"

    def _format_target_message(self, session: ChallengeSession, *, prefix: str) -> str:
        return (
            f"{prefix}\n"
            f"{session.challenge_name} {self._format_round_label(session)}：{self._format_song(session.current_target)}"
            f"{self._format_score_average_lines(session)}"
            f"\n请在 {session.deadline:%H:%M} 前完成并发送 Yurisaki 的 /a recent text。"
            f"{self._format_random_unavailable_hint(session)}"
        )

    def _format_progress(self, session: ChallengeSession) -> str:
        if session.challenge_type == "timed":
            submitted = sum(
                1
                for item in session.timed_results.values()
                if item.submission_count > 0
            )
            return f"{submitted}/{session.total_rounds}"
        if session.challenge_type == "infinite":
            return f"通关 {self._infinite_cleared_charts(session)} 首"
        return f"{session.current_index}/{session.total_rounds}"

    def _unknown_challenge_message(self, challenge_name: str) -> str:
        names = "、".join(self.challenge_store.names()) or "（暂无）"
        return f"未知挑战：{challenge_name}。\n可用挑战：{names}"

    @staticmethod
    def _challenge_type_label(challenge_type: str) -> str:
        labels = {
            "random": "随机",
            "fixed": "固定顺序",
            "timed": "限时任意顺序",
            "infinite": "无限随机",
        }
        return labels.get(challenge_type, challenge_type)

    @staticmethod
    def _challenge_type_short_label(challenge_type: str) -> str:
        labels = {
            "random": "随机",
            "fixed": "固定",
            "timed": "限时",
            "infinite": "无限",
        }
        return labels.get(challenge_type, challenge_type)

    @staticmethod
    def _clear_type_label(clear_type: str) -> str:
        labels = {
            "hp": "血量",
            "score": "总分",
        }
        return labels.get(clear_type, clear_type)

    @staticmethod
    def _format_strict_faults_rule(strict_multiplier: int) -> str:
        return (
            "开启，"
            f"小p扣1、far扣{strict_multiplier + 1}、lost扣{2 * strict_multiplier + 1}"
        )

    @staticmethod
    def _ceil_div(total: int, count: int) -> int:
        return (total + count - 1) // count

    @staticmethod
    def _total_rounds(definition: ChallengeDefinition) -> int:
        if definition.type == "infinite":
            return 1
        total_rounds = (
            definition.rounds if definition.type == "random" else len(definition.charts)
        )
        assert total_rounds is not None
        return total_rounds

    @staticmethod
    def _recorded_cleared_charts(session: ChallengeSession, passed: bool) -> int:
        if session.challenge_type == "infinite":
            return ChallengeManager._infinite_cleared_charts(session)
        return len(session.records) if passed else 0

    @staticmethod
    def _hp_cap(session: ChallengeSession) -> int:
        return session.max_hp or session.initial_hp

    @staticmethod
    def _format_round_label(session: ChallengeSession) -> str:
        if session.challenge_type == "infinite":
            return f"第 {session.round_no} 首"
        return f"第 {session.round_no}/{session.total_rounds} 首"

    def _format_random_unavailable_hint(self, session: ChallengeSession) -> str:
        if session.challenge_type not in {"random", "infinite"}:
            return ""
        return (
            f"\n若目标未解锁，可让 Yurisaki 查询 /a song {session.current_target['name']}；"
            "ARClass 识别未游玩回复后会切换目标并重置计时。"
        )

    def _required_average_score_for_definition(
        self, definition: ChallengeDefinition
    ) -> int:
        assert definition.clear_score is not None
        return self._ceil_div(definition.clear_score, self._total_rounds(definition))

    @staticmethod
    def _format_song(song: dict) -> str:
        return f"{song['name']} [{song['difficulty']}] 定数 {song['level']}"

    @staticmethod
    def _format_hp(session: ChallengeSession, hp: int) -> str:
        return f"{hp}/{ChallengeManager._hp_cap(session)}"

    @staticmethod
    def _required_average_score(session: ChallengeSession) -> int:
        assert session.clear_score is not None
        return ChallengeManager._ceil_div(session.clear_score, session.total_rounds)

    def _score_average_values(
        self, session: ChallengeSession
    ) -> tuple[int, Optional[int]]:
        required_average = self._required_average_score(session)
        if session.challenge_type == "timed":
            scores = [
                result.best_score
                for result in session.timed_results.values()
                if result.best_score > 0
            ]
        else:
            scores = [record.score for record in session.records if record.score > 0]
        current_average = (
            (sum(scores) + len(scores) - 1) // len(scores) if scores else None
        )
        return required_average, current_average

    def _format_score_average_lines(self, session: ChallengeSession) -> str:
        if session.clear_type != "score":
            return ""

        required_average, current_average = self._score_average_values(session)
        lines = [f"过段所需平均分数 {required_average}。"]
        if current_average is not None:
            lines.append(f"当前已游玩曲目平均分数 {current_average}。")
        if session.challenge_type == "timed" and session.clear_score is not None:
            best_scores = [
                result.best_score for result in session.timed_results.values()
            ]
            if best_scores and all(score > 0 for score in best_scores):
                current_total = sum(best_scores)
                if current_total >= session.clear_score:
                    lines.append("当前最佳总分已经可以过段。")
                else:
                    lines.append(
                        f"当前最佳总分还差 {session.clear_score - current_total} 分。"
                    )
        return "\n" + "\n".join(lines)

    @staticmethod
    def _format_target_list(targets: list[dict]) -> str:
        return "、".join(
            f"{target['name']} [{target['difficulty']}]" for target in targets
        )

    @staticmethod
    def _format_records(records: list[RoundRecord]) -> str:
        lines = ["本次成绩："]
        for index, record in enumerate(records, 1):
            lines.append(
                f"{index}. {record.song} [{record.difficulty}] "
                f"{record.score:08d}，{record.faults} 错"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_timed_progress(session: ChallengeSession) -> str:
        lines = []
        for index, target in enumerate(session.targets, 1):
            result = session.timed_results[_chart_key(target)]
            if result.submission_count == 0:
                lines.append(
                    f"{index}. {target['name']} [{target['difficulty']}] 未提交"
                )
            else:
                lines.append(
                    f"{index}. {target['name']} [{target['difficulty']}] "
                    f"最高 {result.best_score:08d}，最低 {result.settled_faults} 错，提交 {result.submission_count} 次"
                )
        return "\n".join(lines)
