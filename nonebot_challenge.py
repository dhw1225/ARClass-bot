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


manager = ChallengeManager()
TIMEOUT_POLL_SECONDS = 20
YURISAKI_BOT_IDS = {"3889054356"}
maintenance_mode = False


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
        await asyncio.sleep(TIMEOUT_POLL_SECONDS)


@get_driver().on_startup
async def _start_timeout_loop() -> None:
    asyncio.create_task(_timeout_loop())


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
