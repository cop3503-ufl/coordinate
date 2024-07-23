from __future__ import annotations

import datetime
import logging
import random
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from .bot import CoordinateBot


logger = logging.getLogger(__name__)


class Fun(commands.Cog):
    last_dad_joke: datetime.datetime | None

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.last_dad_joke = None
        self.change_bot_status.start()

    @tasks.loop(hours=1)
    async def change_bot_status(self):
        await self.bot.wait_until_ready()
        students = list(
            filter(
                lambda m: self.bot.student_role in m.roles,
                self.bot.active_guild.members,
            ),
        )
        activities = [
            discord.CustomActivity("welcome to the new semester!"),
            discord.CustomActivity(f"Watching all {len(students)} of you!"),
        ]
        activity = random.choice(activities)
        await self.bot.change_presence(activity=activity)
        logger.info(f"Changed the bot's status to '{activity.name}'.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Respond to an "I'm X" message with a dad joke
        JOKE_REGEX = r"\bI'?m\s+(\w+(?:\s+\w+){0,2})(?=[.,?!;\n]|$)"
        matches = re.search(JOKE_REGEX, message.content, re.IGNORECASE)
        if (
            matches
            and (
                self.last_dad_joke is None
                or (datetime.datetime.now() - self.last_dad_joke).total_seconds()
                > (60 * 60 * 18)
            )
            and random.random() < 0.2
            and message.author != self.bot.user
        ):
            name = matches.group(1)
            await message.channel.send(f"Hi {name}, I'm Dad!")
            self.last_dad_joke = datetime.datetime.now()

        if (
            "good morning" in message.content.lower()
            and message.author != self.bot.user
            and random.random() < 0.03
        ):
            first_name = message.author.display_name.split(" ")[0]
            await message.reply(f"Good morning, {first_name}!")
            await message.channel.send(
                "https://video.twimg.com/ext_tw_video/1323381277718913024/pu/vid/1018x720/A0fU_fBmIwqcKvmy.mp4?tag=10",
            )


async def setup(bot: CoordinateBot):
    await bot.add_cog(Fun(bot))
