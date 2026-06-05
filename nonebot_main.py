"""
NoneBot2 entry point for the Arcaea challenge bot.

Run this file after installing the dependencies in requirements-nonebot.txt and
configuring a OneBot v11 implementation such as go-cqhttp or Lagrange.OneBot.
"""

from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_plugin("nonebot_challenge")


if __name__ == "__main__":
    nonebot.run()
