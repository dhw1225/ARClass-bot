from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed


nonebot.init()

from nonebot_challenge import (  # noqa: E402
    _handle_guess_answer,
    _guess_user_is_admin,
    _is_cancel_challenge,
    _is_finish_challenge,
    _is_guess_answer,
    _is_guess_command,
    _is_reset_challenge,
    _is_status_challenge,
    guess_answer,
    guess_manager,
)


def group_event(
    text: str, *, to_me: bool = True, role: str = "member", mention: bool = True
):
    segments = [MessageSegment.text(text)]
    if mention:
        segments.insert(0, MessageSegment.at(999))
    message = Message(segments)
    return GroupMessageEvent(
        time=0,
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=123,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message=text,
        font=0,
        sender={"role": role},
        to_me=to_me,
        group_id=456,
    )


class NoneBotGuessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        guess_manager.sessions.clear()
        guess_manager.last_answers.clear()
        guess_manager._locks.clear()

    async def test_command_is_group_only_and_uses_nonebot_to_me(self):
        state = {}
        self.assertTrue(await _is_guess_command(group_event("/guess"), state))
        self.assertEqual(state["guess_action"], "start")
        self.assertFalse(await _is_guess_command(group_event("/guess", to_me=False), {}))
        self.assertTrue(await _is_guess_command(group_event("/guess", mention=False), {}))
        self.assertFalse(await _is_guess_command(group_event("/guesser"), {}))

    def test_stop_permission_recognizes_group_roles_and_superuser(self):
        self.assertFalse(_guess_user_is_admin(group_event("/guess stop")))
        self.assertTrue(_guess_user_is_admin(group_event("/guess stop", role="admin")))
        self.assertTrue(_guess_user_is_admin(group_event("/guess stop", role="owner")))
        driver = SimpleNamespace(config=SimpleNamespace(superusers={"123"}))
        with patch("nonebot_challenge.get_driver", return_value=driver):
            self.assertTrue(_guess_user_is_admin(group_event("/guess stop")))

    async def test_answer_only_matches_active_game_and_ignores_commands(self):
        self.assertFalse(await _is_guess_answer(group_event("Testify"), {}))
        await guess_manager.start("456", "123")
        state = {}
        self.assertTrue(await _is_guess_answer(group_event("Testify"), state))
        self.assertEqual(state["guess_text"], "Testify")
        self.assertFalse(await _is_guess_answer(group_event("/roll"), {}))
        self.assertTrue(await _is_guess_answer(group_event("Testify", mention=False), {}))

    async def test_challenge_operations_never_become_guess_answers(self):
        await guess_manager.start("456", "123")
        operations = {
            "status": _is_status_challenge,
            "cancel": _is_cancel_challenge,
            "reset": _is_reset_challenge,
            "finish": _is_finish_challenge,
        }
        for text, predicate in operations.items():
            with self.subTest(text=text):
                event = group_event(text)
                self.assertTrue(await predicate(event))
                self.assertFalse(await _is_guess_answer(event, {}))
        self.assertEqual(guess_manager.sessions["456"].history, [])

    async def test_correct_answer_sends_image_and_text_together(self):
        event = group_event("placeholder")
        await guess_manager.start("456", "123")
        answer = guess_manager.sessions["456"].answer.title
        bot = AsyncMock()
        with (
            patch("nonebot_challenge.asyncio.to_thread", AsyncMock(return_value=b"png")),
            patch.object(guess_answer, "finish", AsyncMock()),
        ):
            await _handle_guess_answer(bot, event, {"guess_text": answer})
        bot.send.assert_awaited_once()
        message = bot.send.await_args.args[1]
        self.assertTrue(any(segment.type == "image" for segment in message))
        self.assertTrue(
            any("猜测成功" in str(segment.data.get("text", "")) for segment in message)
        )

    async def test_send_failure_uses_only_short_notice(self):
        event = group_event("placeholder")
        await guess_manager.start("456", "123")
        answer = guess_manager.sessions["456"].answer.title
        bot = AsyncMock()
        bot.send.side_effect = ActionFailed(retcode=1200, message="risk")
        short_reply = AsyncMock()
        with (
            patch("nonebot_challenge.asyncio.to_thread", AsyncMock(return_value=b"png")),
            patch("nonebot_challenge._finish_to_user", short_reply),
            patch.object(guess_answer, "finish", AsyncMock()),
        ):
            await _handle_guess_answer(bot, event, {"guess_text": answer})
        text = short_reply.await_args.args[-1]
        self.assertIn("图片发送失败", text)
        self.assertNotIn(answer, text)


if __name__ == "__main__":
    unittest.main()
