from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
import random
from typing import TYPE_CHECKING, ClassVar

import discord
import matplotlib
import numpy as np
from discord.ext import commands, tasks

from .utils import emoji_header

matplotlib.use("Agg")  # thread safety for discord.py

import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize

if TYPE_CHECKING:
    from .bot import CoordinateBot

logger = logging.getLogger(__name__)


async def most_questions_role(
    bot: CoordinateBot,
    update_to: str | None = None,
) -> discord.Role:
    most_questions_role = discord.utils.get(
        bot.active_guild.roles,
        name="Question King",
    )
    if not most_questions_role:
        most_questions_role = discord.utils.get(
            bot.active_guild.roles,
            name="Question Queen",
        )
    if not most_questions_role:
        most_questions_role = discord.utils.get(
            bot.active_guild.roles,
            name="Question Royalty",
        )
    assert isinstance(most_questions_role, discord.Role)

    if update_to:
        await most_questions_role.edit(name=f"Question {update_to}")
    return most_questions_role


class MostQuestions:
    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    async def questions_answered(
        self,
        start: datetime.datetime,
    ) -> dict[discord.Member, int]:
        """Return a dictionary containing the number of forum questions answered by each TA from start time to now."""
        questions_answered = {}
        for channel in self.bot.question_channels:
            possible_threads = [
                t
                for t in channel.threads
                if t.created_at and t.created_at > start - datetime.timedelta(days=1)
            ]
            for thread in possible_threads:
                # Go through each message in thread to see if TA/PM or Professor posted it
                logger.info(f"Searching through thread: {thread.name}")
                prev_message: discord.Message | None = None
                async for message in thread.history(after=start):
                    if (
                        isinstance(message.author, discord.Member)
                        and await self.bot.is_staff(message.author)
                        and (len(message.content) > 15 or len(message.attachments))
                    ):
                        if (
                            prev_message
                            and prev_message.author == message.author
                            and (message.created_at - prev_message.created_at).seconds
                            < 90
                        ):
                            logger.info(
                                f"Skipping message by {message.author} in {thread.name} because it was posted {(message.created_at - prev_message.created_at).seconds}s after the previous message.",
                            )
                            prev_message = message
                            continue
                        if message.author in questions_answered:
                            questions_answered[message.author] += 1
                        else:
                            questions_answered[message.author] = 1
                        prev_message = message

        return questions_answered

    async def generate_graph(self, activity: dict[discord.Member, int]) -> None:
        """Generate a graph containing the number of forum questions answered by each TA from start time to now."""
        # Sort the dictionary by the number of questions answered
        sorted_activity = sorted(activity.items(), key=lambda x: x[1])

        # Get the names of the TAs and the number of questions answered
        questions = [questions for _, questions in sorted_activity]
        names = []
        for member, _ in sorted_activity:
            async with self.bot.db_factory() as db:
                doc = await db.get_staff_member(member=member)
            if doc:
                names.append(doc.name)
            else:
                names.append(member.display_name)

        norm = Normalize(vmin=min(questions), vmax=max(questions))

        # Create a colormap
        original_cmap = plt.get_cmap("Wistia")
        start = 0.2  # Start from this point of original colormap
        stop = 1.0  # End at this point of original colormap

        colors = original_cmap(np.linspace(start, stop, 256))
        cmap = LinearSegmentedColormap.from_list("cut_Reds", colors)

        _, ax = plt.subplots()

        # Create the bar chart
        ax.barh(  # type: ignore
            names,
            questions,
            color=[cmap(norm(value)) for value in questions],
        )

        # Add a title and labels
        ax.set_title("Interactions of Staff Members in Question Channels")  # type: ignore
        ax.set_xlabel("Number of Posts")  # type: ignore
        ax.set_ylabel("Staff Member")  # type: ignore

        # Add colorbar
        sm = ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        plt.colorbar(sm, orientation="vertical", label="Number of Posts")

        # Save the graph
        plt.tight_layout()
        plt.savefig("questions_answered.png", dpi=300)

    async def generate_embed(
        self,
        start: datetime.datetime,
    ) -> tuple[discord.Embed, discord.File, discord.Member]:
        """Generate an embed containing the number of forum questions answered by each TA from start time to now. Also returns the member who answered the most questions for the week."""
        questions_answered = await self.questions_answered(start)
        days_since = (discord.utils.utcnow() - start).days
        top_five = sorted(questions_answered.items(), key=lambda x: x[1], reverse=True)[
            :5
        ]

        role = await most_questions_role(self.bot)
        embed = discord.Embed(
            title=f"🎇 Past {days_since} Days: Activity in Question Channels",
            color=discord.Color.gold(),
            description=f"Posts by staff members in the question channels over the past seven days. Thanks for engaging with students and fostering a supportive learning environment! Keep up the great work, everyone!\n\nCongrats to {top_five[0][0].mention} on being the {role.mention}!",
        )
        embed.add_field(
            name=emoji_header("🌠", "Spotlight Contributors"),
            value="\n".join(
                [
                    f":star2: **{member.mention}** ({questions} post{'s' if questions != 1 else ''})"
                    for (member, questions) in top_five
                ],
            ),
            inline=False,
        )
        await self.generate_graph(questions_answered)
        file = discord.File("questions_answered.png")
        embed.set_image(url="attachment://questions_answered.png")
        os.remove("questions_answered.png")
        return embed, file, top_five[0][0]


class QuestionsCog(commands.Cog):
    _tasks: ClassVar[list[asyncio.Task]] = []

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        # Get the next instance of monday at 12AM Eastern, or use current monday
        # if time has not passed
        now = datetime.datetime.now()
        monday = now + datetime.timedelta(days=7 - now.weekday())
        monday = datetime.datetime(
            year=monday.year,
            month=monday.month,
            day=monday.day,
        )
        # Schedule the task to run every Monday at 12AM Eastern with asyncio
        task = self.bot.loop.create_task(self.monday_mostquestions(monday))
        task.add_done_callback(self._tasks.remove)
        self._tasks.append(task)

        # Start tasks
        self.reminder_to_answer.start()
        self.stale.start()

    @tasks.loop(hours=1)
    async def stale(self):
        """
        For each unanswered thread, if the most recent message was from more than
        36 hours ago, consider marking the thread as Answered.
        """
        await self.bot.wait_until_ready()
        logger.info("Checking for stale threads.")
        for channel in self.bot.question_channels:
            unanswered_tag = discord.utils.get(
                channel.available_tags,
                name="Unanswered",
            )
            for thread in [
                t for t in channel.threads if unanswered_tag in t.applied_tags
            ]:
                # Get the most recent message in the thread
                last_message = await anext(t async for t in thread.history(limit=1))
                if (
                    discord.utils.utcnow() - last_message.created_at
                ).total_seconds() > 36 * 3600:
                    members = [await self.bot.get_member(m.id) for m in thread.members]
                    staff = [m for m in members if m and await self.bot.is_staff(m)]
                    thread_owner = await self.bot.get_member(thread.owner_id)
                    if thread_owner in staff:
                        staff.remove(thread_owner)
                    staff_mentions = (
                        f"{' '.join([m.mention for m in staff])} " if staff else ""
                    )
                    await thread.send(
                        f"Hi {thread_owner.mention} {staff_mentions}, it's been a while since anyone has responded to this thread. If this thread is resolved, please react to the message that helped you with a :white_check_mark: to mark it as Answered. If you still need help, please let us know!",
                    )

    @tasks.loop(hours=12)
    async def reminder_to_answer(self):
        # Introduce spontaneity to the reminder by waiting 0-2 hours
        seconds_to_wait = random.randint(5, 7200)
        logger.info(f"Waiting {seconds_to_wait} seconds before sending reminder.")
        await asyncio.sleep(seconds_to_wait)

        # Fetch all questions which need help. This is defined by:
        #   1. Question has the 'Unanswered' label
        #   2. No member has commented in the thread in the past 10 hours
        logger.info("Fetching questions which need help.")
        MESSAGES_NEEDED = 6
        ten_hours_ago = discord.utils.utcnow() - datetime.timedelta(hours=10)
        # Thread and datetime of last message
        threads: dict[discord.Thread, datetime.datetime | None] = {}
        for channel in self.bot.question_channels:
            unanswered_tag = discord.utils.get(
                channel.available_tags,
                name="Unanswered",
            )
            assert isinstance(unanswered_tag, discord.ForumTag)
            for thread in channel.threads:
                # Ensure that the thread is Unanswered
                unanswered = unanswered_tag in thread.applied_tags
                if not unanswered:
                    continue

                # Ensure that no one has posted in the last ten hours
                recent_messages = thread.history(oldest_first=False, limit=1)
                messages = [m async for m in recent_messages]
                if not messages or messages[0].created_at < ten_hours_ago:
                    threads[thread] = messages[0].created_at if messages else None
        if len(threads) < MESSAGES_NEEDED:
            logger.info(
                f"Did not send answering messages reminder because there were less than {MESSAGES_NEEDED} threads that needed help.",
            )
            return
        embed = discord.Embed(
            title="Reminder to Answer Questions",
            color=discord.Color.light_gray(),
            description=f"Hey everyone! There are currently {len(threads)} questions that need help. If you have time, please consider helping these students out. Thank you for your support and continued assistance.",
        )
        thread_strings = []
        for thread, last_message_time in threads.items():
            info = []
            if thread.created_at:
                info.append(
                    f"updated at {discord.utils.format_dt(last_message_time, style='R') if last_message_time else 'never'}",
                )
            info_string = ", ".join(info)
            new_string = f"* {thread.jump_url} ({info_string})"
            if sum([len(s) for s in thread_strings]) + len(new_string) < 1000:
                thread_strings.append(new_string)
        embed.add_field(
            name="Threads Needing Help",
            value="\n".join(thread_strings),
            inline=False,
        )
        await self.bot.staff_ch.send(
            embed=embed,
        )

    async def monday_mostquestions(self, dt: datetime.datetime):
        # Wait until datetime
        logger.info(f"Waiting until {dt} to send weekly activity report...")
        await asyncio.sleep((dt - datetime.datetime.now()).total_seconds())

        most_questions = MostQuestions(self.bot)
        staff_channel = self.bot.staff_ch
        embed_response = await most_questions.generate_embed(
            discord.utils.utcnow() - datetime.timedelta(days=7),
        )
        embed, file = embed_response[0], embed_response[1]
        await staff_channel.send(
            embed=embed,
            file=file,
            content="Happy Monday! Here's the weekly activity report for the question channels for the past week. Have a great start to your week, and thank you for your help with assisting students!",
        )
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(member=embed_response[2])

        # Give the new role!
        role = await most_questions_role(self.bot)
        for member in role.members:
            await member.remove_roles(role)
        logger.info(f"updating to {doc.royal_title}")
        await embed_response[2].add_roles(
            await most_questions_role(self.bot, update_to=doc.royal_title),
        )

        # Schedule the task to run every Monday at 12AM Eastern with asyncio
        logger.info(
            f"Rescheduling weekly activity report for {dt + datetime.timedelta(days=7)}...",
        )
        task = self.bot.loop.create_task(
            self.monday_mostquestions(dt + datetime.timedelta(days=7)),
        )
        task.add_done_callback(self._tasks.remove)
        self._tasks.append(task)

    @commands.command()
    @commands.is_owner()
    async def mostquestions(self, ctx: commands.Context):
        most_questions = MostQuestions(self.bot)
        embed_response = await most_questions.generate_embed(
            discord.utils.utcnow() - datetime.timedelta(days=7),
        )
        embed, file = embed_response[0], embed_response[1]
        await ctx.send(embed=embed, file=file)
        await ctx.message.delete()

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        thread_owner = thread.owner
        if not thread_owner:
            thread_owner = await self.bot.active_guild.fetch_member(thread.owner_id)
        if (
            thread.parent
            and thread.parent in self.bot.question_channels
            and isinstance(thread.parent, discord.ForumChannel)
            and not await self.bot.is_staff(
                thread_owner,
            )
        ):
            logger.info(
                f"New questions post in #{thread.parent.name} by {thread.owner}: tagging with unanswered tag!",
            )

            # Make sure that the student does not already have an unanswered thread
            for channel in self.bot.question_channels:
                relevant_tag = discord.utils.get(
                    channel.available_tags,
                    name="Unanswered",
                )
                for t in channel.threads:
                    if (
                        t != thread
                        and t.owner == thread_owner
                        and relevant_tag in t.applied_tags
                    ):
                        logger.info(
                            f"Student {thread_owner} already has an unanswered thread in #{channel.name}! Deleting new thread and sending message.",
                        )
                        deleted_embed = discord.Embed(
                            title="Thread Automatically Deleted",
                            color=discord.Color.brand_red(),
                            description=f"Hi! Unfortunately, your recent thread titled **{thread.name}** was automatically deleted because you already have one unanswered thread open: {t.jump_url}.\n\nPlease mark that thread as answered by adding a :white_check_mark: reaction to a post that solved your problem, or please wait for a response on that thread. Opening multiple threads at once clogs the queues of staff members attempting to answer questions, and we appreciate your patience with us answering multiple questions.",
                        )
                        with contextlib.suppress(discord.Forbidden):
                            await thread_owner.send(embed=deleted_embed)
                        return await thread.delete()

            relevant_tag = discord.utils.get(
                thread.parent.available_tags,
                name="Unanswered",
            )
            if not relevant_tag:
                raise ValueError(
                    "Expected to find an Unanswered tag in questions thread, but tag is missing.",
                )

            await thread.add_tags(
                relevant_tag,
                reason=f"{thread.owner} created a new thread in questions channel ({thread.parent}), thus adding Unanswered tag.",
            )

            await asyncio.sleep(2)  # wait one sec to avoid posting before author
            questions_embed = discord.Embed(
                title="Receiving a Quicker Response",
                color=discord.Color.gold(),
                description="Hi! Thanks for posting a new question. In order to help us answer your question as quickly as possible, please ensure the following is listed in your post:\n* The _specific_ issue you're encountering\n* Steps you've taken to resolve it\n* Your thoughts on the root cause of the issue\n\nPlease edit your post to include this information if it is not there already. You can edit your post by right clicking on your post and clicking **Edit Post**. Alternatively, you can provide this information in a followup message.\n\nOnce your question has been answered, you can add a :white_check_mark: reaction on the post that has the answer you were looking for. This will mark your thread as answered.",
            )
            DELAY_LENGTH = 60
            future_time = discord.utils.utcnow() + datetime.timedelta(
                seconds=DELAY_LENGTH,
            )
            await thread.send(
                f"To reduce spam, this message will be automatically deleted {discord.utils.format_dt(future_time, 'R')}.",
                embed=questions_embed,
                delete_after=DELAY_LENGTH,
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel_id = payload.channel_id
        potential_member = payload.member
        if not potential_member:
            potential_member = await self.bot.active_guild.fetch_member(payload.user_id)
        potential_thread = await self.bot.active_guild.fetch_channel(channel_id)
        if (
            (
                isinstance(potential_thread, discord.Thread)
                and potential_thread.parent in self.bot.question_channels
            )
            and (
                await self.bot.is_staff(potential_member)
                or potential_member == potential_thread.owner
            )
            and payload.emoji.name == "✅"
            and isinstance(potential_thread.parent, discord.ForumChannel)
        ):
            relevant_tag = discord.utils.get(
                potential_thread.parent.available_tags,
                name="Answered",
            )
            if not relevant_tag:
                raise ValueError(
                    "Expected to find an Answered tag in questions thread, but tag is missing.",
                )

            logger.info(
                f"New reaction inside #{potential_thread.parent} thread, tagging with Answered tag.",
            )
            tags_to_keep = {
                t for t in potential_thread.applied_tags if t.name != "Unanswered"
            }
            tags_to_keep.add(relevant_tag)
            await potential_thread.edit(
                applied_tags=list(tags_to_keep),
                reason=f"{potential_member} added {payload.emoji} to a message in {potential_thread}, adding Answered tag, and removing Unanswered tag if found.",
            )


async def setup(bot):
    await bot.add_cog(QuestionsCog(bot))
