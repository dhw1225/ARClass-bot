"""User-facing challenge message formatting."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from challenge_labels import (
    ceil_div,
    challenge_type_label,
    challenge_type_short_label,
    clear_type_label,
    format_hp,
    format_round_label,
    format_song,
    format_strict_faults_rule,
    hp_cap,
    infinite_cleared_charts,
    recorded_cleared_charts,
    total_rounds,
)
from challenge_models import ChallengeDefinition, ChallengeSession, RoundRecord
from challenge_recent import _chart_key
from challenge_targets import format_target_list


class ChallengeViewsMixin:
    def help_message(self) -> str:
        return (
            "Arcaea 段位 bot 使用说明\n"
            "@ARClass /help：查看使用说明。\n"
            "@ARClass /cha <挑战名称>：开始指定段位。\n"
            "@ARClass /cha list：查看可用挑战列表。\n"
            "@ARClass /cha <挑战名称> help：查看段位规则说明。\n"
            "@ARClass status：查看当前挑战状态。\n"
            "@ARClass cancel：中止当前挑战，本次判定失败且不写入成绩。\n"
            "@ARClass reset：随机段位第一首未游玩时重新抽取第一首。\n"
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
        return challenge_type_label(challenge_type)

    @staticmethod
    def _challenge_type_short_label(challenge_type: str) -> str:
        return challenge_type_short_label(challenge_type)

    @staticmethod
    def _clear_type_label(clear_type: str) -> str:
        return clear_type_label(clear_type)

    @staticmethod
    def _format_strict_faults_rule(strict_multiplier: int) -> str:
        return format_strict_faults_rule(strict_multiplier)

    @staticmethod
    def _ceil_div(total: int, count: int) -> int:
        return ceil_div(total, count)

    @staticmethod
    def _total_rounds(definition: ChallengeDefinition) -> int:
        return total_rounds(definition)

    @staticmethod
    def _recorded_cleared_charts(session: ChallengeSession, passed: bool) -> int:
        return recorded_cleared_charts(session, passed)

    @staticmethod
    def _hp_cap(session: ChallengeSession) -> int:
        return hp_cap(session)

    @staticmethod
    def _format_round_label(session: ChallengeSession) -> str:
        return format_round_label(session)

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
        return format_song(song)

    @staticmethod
    def _format_hp(session: ChallengeSession, hp: int) -> str:
        return format_hp(session, hp)

    @staticmethod
    def _required_average_score(session: ChallengeSession) -> int:
        assert session.clear_score is not None
        return ceil_div(session.clear_score, session.total_rounds)

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
        return format_target_list(targets)

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
