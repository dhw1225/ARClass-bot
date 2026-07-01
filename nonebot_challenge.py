"""
NoneBot2 / OneBot v11 adapter for challenge.py.

The challenge rules stay in ChallengeManager. This adapter only maps QQ events
to manager calls, identifies Yurisaki recent text replies, and sends response
text.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from nonebot import get_bots, get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from nonebot.typing import T_State

from challenge import ChallengeManager, ChallengeResponse
from guess_game import GuessCatalog, GuessGameManager, parse_guess_command
from guess_image import render_guess_history


manager = ChallengeManager()
TIMEOUT_POLL_SECONDS = 10
YURISAKI_BOT_IDS = {"3889054356"}
GUESS_RESERVED_ANSWERS = {"status", "cancel", "reset", "finish"}
maintenance_mode = False
guess_manager = GuessGameManager(GuessCatalog.load())


@dataclass(frozen=True)
class ReplyContext:
    message_type: str
    user_id: str
    group_id: Optional[str] = None


reply_contexts: dict[str, ReplyContext] = {}


def _user_id(event: MessageEvent) -> str:
    return str(event.get_user_id())


def _context_from_event(event: MessageEvent) -> ReplyContext:
    message_type = getattr(event, "message_type", "private")
    group_id = getattr(event, "group_id", None)
    return ReplyContext(
        message_type=str(message_type),
        user_id=_user_id(event),
        group_id=str(group_id) if group_id is not None else None,
    )


def _remember_context(event: MessageEvent) -> None:
    reply_contexts[_user_id(event)] = _context_from_event(event)


def _remember_user_context(user_id: str, event: MessageEvent) -> None:
    context = _context_from_event(event)
    reply_contexts[user_id] = ReplyContext(
        message_type=context.message_type,
        user_id=user_id,
        group_id=context.group_id,
    )


def _context_for_user(event: MessageEvent, user_id: str) -> ReplyContext:
    context = _context_from_event(event)
    return ReplyContext(
        message_type=context.message_type,
        user_id=user_id,
        group_id=context.group_id,
    )


def _plain_text(event: MessageEvent) -> str:
    return "".join(
        str(segment.data.get("text", ""))
        for segment in event.get_message()
        if segment.type == "text"
    ).strip()


def _normalized_text(event: MessageEvent) -> str:
    return re.sub(r"\s+", "", _plain_text(event)).casefold()


def _is_group_message(event: MessageEvent) -> bool:
    return getattr(event, "message_type", "") == "group" and getattr(
        event, "group_id", None
    ) is not None


def _is_help_text(text: str) -> bool:
    return re.fullmatch(r"\s*/help\s*", text, flags=re.IGNORECASE) is not None


def _is_admin_help_text(text: str) -> bool:
    return re.fullmatch(r"\s*/help\s+admin\s*", text, flags=re.IGNORECASE) is not None


def _is_challenge_list_text(text: str) -> bool:
    return re.fullmatch(r"\s*/cha\s+list\s*", text, flags=re.IGNORECASE) is not None


def _is_query_text(text: str) -> bool:
    return re.fullmatch(r"\s*/query\s*", text, flags=re.IGNORECASE) is not None


def _rank_target_from_text(text: str) -> Optional[str]:
    match = re.fullmatch(r"\s*/rank\s+(.+?)\s*", text, flags=re.IGNORECASE)
    if not match:
        return None
    target = match.group(1).strip()
    return target or None


def _challenge_help_name_from_text(text: str) -> Optional[str]:
    match = re.fullmatch(r"\s*/cha\s+(.+)\s+help\s*", text, flags=re.IGNORECASE)
    if not match:
        return None
    challenge_name = match.group(1).strip()
    return challenge_name or None


def _message_for_context(context: ReplyContext, message: str) -> str | Message:
    if context.message_type == "group" and context.group_id is not None:
        return MessageSegment.at(int(context.user_id)) + MessageSegment.text(f"\n{message}")
    return message


async def _send_to_context(bot: Bot, context: ReplyContext, message: str) -> None:
    if context.message_type == "group" and context.group_id is not None:
        await bot.send_group_msg(
            group_id=int(context.group_id),
            message=_message_for_context(context, message),
        )
        return
    await bot.send_private_msg(user_id=int(context.user_id), message=message)


async def _finish_to_user(matcher, event: MessageEvent, user_id: str, message: str) -> None:
    context = _context_for_user(event, user_id)
    try:
        await matcher.finish(_message_for_context(context, message))
    except ActionFailed as exc:
        logger.warning(
            "Failed to send challenge response: "
            f"user_id={user_id}, group_id={context.group_id}, "
            f"retcode={getattr(exc, 'retcode', None)}, "
            f"message={getattr(exc, 'message', exc)}"
        )
        raise FinishedException from None


async def _send_event_to_user(bot: Bot, event: MessageEvent, user_id: str, message: str) -> None:
    context = _context_for_user(event, user_id)
    if context.message_type == "group" and context.group_id is not None:
        await bot.send(event, _message_for_context(context, message))
        return
    await bot.send(event, message)


async def _send_forward_or_text(bot: Bot, event: MessageEvent, message: str) -> bool:
    context = _context_from_event(event)
    if context.message_type != "group" or context.group_id is None:
        await bot.send(event, message)
        return True

    bot_user_id = int(getattr(bot, "self_id", 0) or context.user_id)
    node = MessageSegment.node_custom(
        user_id=bot_user_id,
        nickname="ARClass",
        content=message,
    )
    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=int(context.group_id),
            messages=[node],
        )
    except ActionFailed:
        return False
    return True


async def _group_display_names(
    bot: Bot, event: MessageEvent, user_ids: list[str]
) -> dict[str, str]:
    context = _context_from_event(event)
    if context.message_type != "group" or context.group_id is None:
        return {}

    names: dict[str, str] = {}
    for user_id in user_ids:
        try:
            info = await bot.call_api(
                "get_group_member_info",
                group_id=int(context.group_id),
                user_id=int(user_id),
                no_cache=False,
            )
        except ActionFailed:
            continue
        card = str(info.get("card", "")).strip()
        nickname = str(info.get("nickname", "")).strip()
        if card or nickname:
            names[user_id] = card or nickname
    return names


def _active_user_ids() -> list[str]:
    return list(manager.sessions.keys())


def _admin_help_message() -> str:
    return (
        "ARClass 管理员指令\n"
        "/set maintain：进入维护模式，不再允许启动新挑战；已有挑战可继续。\n"
        "/set resume：退出维护模式，恢复启动新挑战。\n"
        "/active：查看当前所有进行中的挑战。\n"
        "/挑战超时检查：手动执行一次超时检查。"
    )


def _forget_user(user_id: str) -> None:
    reply_contexts.pop(user_id, None)


def _is_terminal_response(response: ChallengeResponse) -> bool:
    return response.status.startswith("finished") or response.status in {
        "timeout_failed",
        "cancelled_failed",
        "hp_failed",
    }


async def _check_user_timeout(bot: Bot, user_id: str) -> Optional[ChallengeResponse]:
    response = manager.check_timeout(user_id)
    if not _is_terminal_response(response):
        return None

    context = reply_contexts.get(user_id)
    _forget_user(user_id)
    if context is not None:
        try:
            await _send_to_context(bot, context, response.message)
        except ActionFailed:
            pass
    return response


async def _check_event_timeout(bot: Bot, event: MessageEvent) -> bool:
    user_id = _user_id(event)
    if user_id not in manager.sessions:
        return False

    response = manager.check_timeout(user_id)
    if not _is_terminal_response(response):
        return False

    _forget_user(user_id)
    await _send_event_to_user(bot, event, user_id, response.message)
    return True


async def _timeout_loop() -> None:
    await asyncio.sleep(TIMEOUT_POLL_SECONDS)
    while True:
        bots = list(get_bots().values())
        if bots:
            bot = bots[0]
            for user_id in _active_user_ids():
                await _check_user_timeout(bot, user_id)
            for group_id, answer in await guess_manager.collect_expired():
                try:
                    await bot.send_group_msg(
                        group_id=int(group_id),
                        message=f"猜曲游戏因 10 分钟无有效猜测而结束，正确答案是：{answer.title}",
                    )
                except ActionFailed as exc:
                    logger.warning(
                        "Failed to send guess timeout: "
                        f"group_id={group_id}, retcode={getattr(exc, 'retcode', None)}"
                    )
        await asyncio.sleep(TIMEOUT_POLL_SECONDS)


@get_driver().on_startup
async def _start_timeout_loop() -> None:
    asyncio.create_task(_timeout_loop())


async def _is_guess_command(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not _is_group_message(event) or not event.is_tome():
        return False
    action = parse_guess_command(_plain_text(event))
    if action is None:
        return False
    state["guess_action"] = action
    return True


guess_command = on_message(
    Rule(_is_guess_command),
    priority=8,
    block=True,
)


def _guess_user_is_admin(event: MessageEvent) -> bool:
    user_id = _user_id(event)
    if user_id in {str(value) for value in get_driver().config.superusers}:
        return True
    sender = getattr(event, "sender", None)
    return str(getattr(sender, "role", "")) in {"admin", "owner"}


@guess_command.handle()
async def _handle_guess_command(event: MessageEvent, state: T_State) -> None:
    group_id = str(getattr(event, "group_id"))
    user_id = _user_id(event)
    if state["guess_action"] == "start":
        result = await guess_manager.start(group_id, user_id)
        if result.status == "already_active":
            message = "本群已有一局猜曲游戏正在进行。"
        else:
            prefix = ""
            if result.status == "started_after_expiry" and result.answer is not None:
                prefix = f"上一局已超时，正确答案是：{result.answer.title}\n"
            message = prefix + (
                "猜曲游戏已开始！请 @ARClass 并发送曲名或常用别名进行猜测。\n"
                "每局最多 15 次有效猜测，10 分钟无有效猜测将自动结束。"
            )
    else:
        result = await guess_manager.stop(
            group_id, user_id, is_admin=_guess_user_is_admin(event)
        )
        if result.status == "no_game":
            message = "本群当前没有进行中的猜曲游戏。"
        elif result.status == "stop_forbidden":
            message = "只有游戏发起者、群管理员或机器人管理员可以结束本局。"
        else:
            assert result.answer is not None
            message = f"猜曲游戏已结束，正确答案是：{result.answer.title}"
    await _finish_to_user(guess_command, event, user_id, message)


async def _is_guess_answer(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not _is_group_message(event) or not event.is_tome():
        return False
    group_id = str(getattr(event, "group_id"))
    if group_id not in guess_manager.sessions:
        return False
    text = _plain_text(event).strip()
    if (
        not text
        or text.startswith("/")
        or text.casefold() in GUESS_RESERVED_ANSWERS
    ):
        return False
    state["guess_text"] = text
    return True


guess_answer = on_message(
    Rule(_is_guess_answer),
    priority=30,
    block=True,
)


@guess_answer.handle()
async def _handle_guess_answer(bot: Bot, event: MessageEvent, state: T_State) -> None:
    group_id = str(getattr(event, "group_id"))
    user_id = _user_id(event)
    result = await guess_manager.guess(group_id, str(state["guess_text"]))
    if result.status == "expired":
        assert result.answer is not None
        await _finish_to_user(
            guess_answer,
            event,
            user_id,
            f"上一局已因超时结束，正确答案是：{result.answer.title}",
        )
    if result.status == "not_found":
        await _finish_to_user(guess_answer, event, user_id, "找不到这首曲目，请检查曲名或别名。")
    if result.status == "ambiguous":
        await _finish_to_user(
            guess_answer,
            event,
            user_id,
            "该名称对应多首曲目，请输入更完整的名称：\n" + "\n".join(result.candidates),
        )
    if result.status == "candidates":
        await _finish_to_user(
            guess_answer,
            event,
            user_id,
            "未能唯一确定曲目，你可能想猜：\n" + "\n".join(result.candidates),
        )
    if result.status == "duplicate":
        assert result.guessed is not None
        await _finish_to_user(
            guess_answer,
            event,
            user_id,
            f"{result.guessed.title} 已经猜过了，本次不计轮次。",
        )
    if result.status not in {"incorrect", "correct", "round_limit"}:
        return
    assert result.answer is not None
    try:
        image = await asyncio.to_thread(render_guess_history, result.history, result.answer)
        message = MessageSegment.image(file=image)
        if result.status == "correct":
            message += MessageSegment.at(int(user_id)) + MessageSegment.text(
                f"\n猜测成功！共使用 {result.rounds} 轮。"
            )
        elif result.status == "round_limit":
            message += MessageSegment.text(
                f"\n已达到 15 轮上限，正确答案是：{result.answer.title}"
            )
        await bot.send(event, message)
    except (ActionFailed, OSError) as exc:
        logger.warning(
            "Failed to render/send guess history: "
            f"group_id={group_id}, user_id={user_id}, rounds={result.rounds}, "
            f"error={type(exc).__name__}"
        )
        failure_message = "猜测已记录，但历史图片发送失败，请稍后再试。"
        if result.status == "correct":
            failure_message = f"猜测成功！共使用 {result.rounds} 轮，但历史图片发送失败。"
        elif result.status == "round_limit":
            failure_message = f"历史图片发送失败；已达到 15 轮上限，正确答案是：{result.answer.title}"
        await _finish_to_user(
            guess_answer,
            event,
            user_id,
            failure_message,
        )
    await guess_answer.finish()


def _challenge_name_from_event(event: MessageEvent) -> Optional[str]:
    text = _plain_text(event)
    if _is_challenge_list_text(text) or _challenge_help_name_from_text(text):
        return None
    match = re.fullmatch(r"\s*/cha\s+(.+?)\s*", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


async def _is_help_command(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _is_help_text(_plain_text(event))


help_command = on_message(
    Rule(_is_help_command),
    priority=9,
    block=True,
)


@help_command.handle()
async def _handle_help(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    message = manager.help_message()
    if await _send_forward_or_text(bot, event, message):
        await help_command.finish()
    await _finish_to_user(help_command, event, _user_id(event), message)


async def _is_admin_help_command(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _is_admin_help_text(_plain_text(event))


admin_help_command = on_message(
    Rule(_is_admin_help_command),
    permission=SUPERUSER,
    priority=8,
    block=True,
)


@admin_help_command.handle()
async def _handle_admin_help(bot: Bot, event: MessageEvent) -> None:
    await _finish_to_user(admin_help_command, event, _user_id(event), _admin_help_message())


async def _is_challenge_list(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _is_challenge_list_text(_plain_text(event))


challenge_list = on_message(
    Rule(_is_challenge_list),
    priority=9,
    block=True,
)


@challenge_list.handle()
async def _handle_challenge_list(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    message = manager.challenge_list_message()
    if await _send_forward_or_text(bot, event, message):
        await challenge_list.finish()
    await _finish_to_user(challenge_list, event, _user_id(event), message)


async def _is_query_command(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _is_query_text(_plain_text(event))


query_command = on_message(
    Rule(_is_query_command),
    priority=9,
    block=True,
)


@query_command.handle()
async def _handle_query(bot: Bot, event: MessageEvent) -> None:
    user_id = _user_id(event)
    message = manager.query_user_message(user_id)
    if await _send_forward_or_text(bot, event, message):
        await query_command.finish()
    await _finish_to_user(query_command, event, user_id, message)


async def _is_rank_command(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not event.is_tome():
        return False
    target = _rank_target_from_text(_plain_text(event))
    if target is None:
        return False
    state["rank_target"] = target
    return True


rank_command = on_message(
    Rule(_is_rank_command),
    priority=9,
    block=True,
)


@rank_command.handle()
async def _handle_rank(bot: Bot, event: MessageEvent, state: T_State) -> None:
    target = str(state["rank_target"]).strip()
    user_ids = manager.challenge_rank_user_ids(target)
    display_names = await _group_display_names(bot, event, user_ids)
    message = manager.challenge_rank_message(target, display_names)

    if await _send_forward_or_text(bot, event, message):
        await rank_command.finish()
    await _finish_to_user(rank_command, event, _user_id(event), message)


async def _is_challenge_help(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not event.is_tome():
        return False
    challenge_name = _challenge_help_name_from_text(_plain_text(event))
    if challenge_name is None:
        return False
    state["challenge_help_name"] = challenge_name
    return True


challenge_help = on_message(
    Rule(_is_challenge_help),
    priority=9,
    block=True,
)


@challenge_help.handle()
async def _handle_challenge_help(bot: Bot, event: MessageEvent, state: T_State) -> None:
    if await _check_event_timeout(bot, event):
        return

    await _finish_to_user(
        challenge_help,
        event,
        _user_id(event),
        manager.challenge_help_message(str(state["challenge_help_name"])),
    )


async def _is_start_challenge(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not event.is_tome():
        return False
    challenge_name = _challenge_name_from_event(event)
    if not challenge_name:
        return False
    state["challenge_name"] = challenge_name
    return True


start_challenge = on_message(
    Rule(_is_start_challenge),
    priority=10,
    block=True,
)


@start_challenge.handle()
async def _handle_start(bot: Bot, event: MessageEvent, state: T_State) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    if maintenance_mode:
        await _finish_to_user(
            start_challenge,
            event,
            user_id,
            "ARClass 即将维护，暂不允许启动新挑战；已有挑战可以继续完成。",
        )
        return

    response = manager.start(user_id, str(state["challenge_name"]))
    if response.status in {"started", "already_active"}:
        _remember_context(event)
    await _finish_to_user(start_challenge, event, user_id, response.message)


async def _is_cancel_challenge(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _normalized_text(event) == "cancel"


cancel_challenge = on_message(
    Rule(_is_cancel_challenge),
    priority=10,
    block=True,
)


@cancel_challenge.handle()
async def _handle_cancel(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    response = manager.cancel(user_id)
    _forget_user(user_id)
    await _finish_to_user(cancel_challenge, event, user_id, response.message)


async def _is_reset_challenge(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _normalized_text(event) == "reset"


reset_challenge = on_message(
    Rule(_is_reset_challenge),
    priority=10,
    block=True,
)


@reset_challenge.handle()
async def _handle_reset(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    response = manager.reset(user_id)
    if response.message:
        _remember_context(event)
        await _finish_to_user(reset_challenge, event, user_id, response.message)


async def _is_status_challenge(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _normalized_text(event) == "status"


status_challenge = on_message(
    Rule(_is_status_challenge),
    priority=10,
    block=True,
)


@status_challenge.handle()
async def _handle_status(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    response = manager.check_timeout(user_id)
    if response.status == "waiting":
        _remember_context(event)
    await _finish_to_user(status_challenge, event, user_id, response.message)


def _manual_score_from_event(event: MessageEvent) -> Optional[int]:
    match = re.fullmatch(r"score(\d{1,8})", _normalized_text(event))
    if not match:
        return None
    score = int(match.group(1))
    if 0 <= score <= 10_010_000:
        return score
    return None


async def _is_manual_score(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not event.is_tome():
        return False
    user_id = _user_id(event)
    if user_id not in manager.sessions:
        return False
    score = _manual_score_from_event(event)
    if score is None:
        return False
    state["manual_score"] = score
    return True


manual_score = on_message(
    Rule(_is_manual_score),
    priority=10,
    block=True,
)


@manual_score.handle()
async def _handle_manual_score(bot: Bot, event: MessageEvent, state: T_State) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    _remember_context(event)
    response = manager.handle_manual_score(
        user_id,
        int(state["manual_score"]),
    )
    if _is_terminal_response(response):
        _forget_user(user_id)
    await _finish_to_user(manual_score, event, user_id, response.message)


async def _is_finish_challenge(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return event.is_tome() and _normalized_text(event) == "finish"


finish_challenge = on_message(
    Rule(_is_finish_challenge),
    priority=10,
    block=True,
)


@finish_challenge.handle()
async def _handle_finish(bot: Bot, event: MessageEvent) -> None:
    if await _check_event_timeout(bot, event):
        return

    user_id = _user_id(event)
    response = manager.finish(user_id)
    if _is_terminal_response(response):
        _forget_user(user_id)
    else:
        _remember_context(event)
    await _finish_to_user(finish_challenge, event, user_id, response.message)


def _mentioned_active_user_ids(event: MessageEvent) -> list[str]:
    user_ids: list[str] = []
    for segment in event.get_message():
        if segment.type != "at":
            continue
        qq = str(segment.data.get("qq", ""))
        if qq in manager.sessions:
            user_ids.append(qq)
    return user_ids


def _recent_text_owner_user_id(event: MessageEvent) -> Optional[str]:
    mentioned_user_ids = _mentioned_active_user_ids(event)
    if len(mentioned_user_ids) == 1:
        return mentioned_user_ids[0]
    return None


def _looks_like_recent_text(text: str) -> bool:
    return (
        "[arcaea recent]" in text.casefold()
        and re.search(r"(?im)^\s*Chart\s*:", text) is not None
        and re.search(r"(?im)^\s*Score\s*:", text) is not None
    )


def _looks_like_unavailable_song_text(text: str) -> bool:
    return "[arcaea score]" in text.casefold() and "暂未游玩该曲目" in text


def _is_yurisaki_bot(event: MessageEvent) -> bool:
    return _user_id(event) in YURISAKI_BOT_IDS


async def _has_active_recent_text(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not _is_yurisaki_bot(event):
        return False
    text = _plain_text(event)
    if not _looks_like_recent_text(text):
        return False
    user_id = _recent_text_owner_user_id(event)
    if user_id is None:
        return False
    state["challenge_user_id"] = user_id
    state["recent_text"] = text
    return True


recent_text_handler = on_message(
    Rule(_has_active_recent_text),
    priority=20,
    block=True,
)


@recent_text_handler.handle()
async def _handle_recent_text(bot: Bot, event: MessageEvent, state: T_State) -> None:
    user_id = str(state["challenge_user_id"])
    _remember_user_context(user_id, event)
    if await _check_user_timeout(bot, user_id):
        return

    response = manager.handle_recent_text(user_id, str(state["recent_text"]))
    if _is_terminal_response(response):
        _forget_user(user_id)
    await _finish_to_user(recent_text_handler, event, user_id, response.message)


async def _has_active_unavailable_song_text(event: Event, state: T_State) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    if not _is_yurisaki_bot(event):
        return False
    text = _plain_text(event)
    if not _looks_like_unavailable_song_text(text):
        return False
    user_id = _recent_text_owner_user_id(event)
    if user_id is None:
        return False
    state["challenge_user_id"] = user_id
    state["unavailable_song_text"] = text
    return True


unavailable_song_handler = on_message(
    Rule(_has_active_unavailable_song_text),
    priority=20,
    block=True,
)


@unavailable_song_handler.handle()
async def _handle_unavailable_song_text(
    bot: Bot, event: MessageEvent, state: T_State
) -> None:
    user_id = str(state["challenge_user_id"])
    _remember_user_context(user_id, event)
    if await _check_user_timeout(bot, user_id):
        return

    response = manager.handle_unavailable_song_text(
        user_id, str(state["unavailable_song_text"])
    )
    if _is_terminal_response(response):
        _forget_user(user_id)
    if not response.message:
        return
    await _finish_to_user(unavailable_song_handler, event, user_id, response.message)


admin_set = on_command(
    "set",
    permission=SUPERUSER,
    priority=10,
    block=True,
)


@admin_set.handle()
async def _handle_admin_set(bot: Bot, event: MessageEvent, args=CommandArg()) -> None:
    global maintenance_mode

    command = args.extract_plain_text().strip().casefold()
    if command == "maintain":
        maintenance_mode = True
        await admin_set.finish("已进入维护模式，不再允许启动新挑战；已有挑战可以继续。")
    if command == "resume":
        maintenance_mode = False
        await admin_set.finish("已退出维护模式，可以启动新挑战。")
    await admin_set.finish("用法：/set maintain 或 /set resume。")


admin_active = on_command(
    "active",
    permission=SUPERUSER,
    priority=10,
    block=True,
)


@admin_active.handle()
async def _handle_admin_active(bot: Bot, event: MessageEvent) -> None:
    user_ids = manager.active_session_user_ids()
    display_names = await _group_display_names(bot, event, user_ids)
    message = manager.active_sessions_message(display_names)
    if await _send_forward_or_text(bot, event, message):
        await admin_active.finish()
    await _finish_to_user(admin_active, event, _user_id(event), message)


admin_timeout_check = on_command(
    "挑战超时检查",
    aliases={"arcaea超时检查"},
    permission=SUPERUSER,
    priority=10,
    block=True,
)


@admin_timeout_check.handle()
async def _handle_admin_timeout_check(bot: Bot, event: MessageEvent, args=CommandArg()) -> None:
    checked = 0
    ended = 0
    for user_id in _active_user_ids():
        checked += 1
        if await _check_user_timeout(bot, user_id):
            ended += 1

    suffix = args.extract_plain_text().strip()
    prefix = f"{suffix}\n" if suffix else ""
    await admin_timeout_check.finish(f"{prefix}已检查 {checked} 个挑战，因超时完成或失败 {ended} 个。")
