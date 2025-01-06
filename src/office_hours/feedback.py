from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..components import EmojiEmbed
from ..db import OfficeHoursSession, OfficeHoursSessionStatus
from ..views import CoordinateBotModal, CoordinateBotView

if TYPE_CHECKING:
    from ..bot import CoordinateBot


logger = logging.getLogger(__name__)


def _star_color(star_count: int) -> discord.Color:
    # Sliding scale of gold (dark = 1, light = 5)
    colors = {
        1: discord.Color(0xA67D3D),  # Dark gold (amber-like)
        2: discord.Color(0xBF953F),  # Slightly lighter gold
        3: discord.Color(0xD4AF37),  # Mid-tone gold
        4: discord.Color(0xEAC086),  # Light gold
        5: discord.Color(0xFAD02E),  # Very light and pretty gold
    }
    return colors[star_count]


class SessionFetcher:
    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    async def recent_session(self, student_id: int) -> OfficeHoursSession:
        async with self.bot.db_factory() as db:
            return await db.get_session(
                student_id,
                status=OfficeHoursSessionStatus.COMPLETED,
            )

    async def feedback_embed(self, star_count: int, student_id: int) -> EmojiEmbed:
        embed = EmojiEmbed(
            title="New Office Hours Feedback",
            color=_star_color(star_count),
            description="New feedback has arrived from a student who recently visited office hours. The feedback is shown below.",
        )
        session = await self.recent_session(student_id)
        try:
            staff_member = await self.bot.get_member(session.staff_id or 0)
        except discord.NotFound:
            staff_member = None
        embed.add_field(
            name="Star Count",
            emoji="⭐",
            value=f"{star_count} stars",
            inline=True,
        )
        embed.add_field(
            name="Leading Staff Member",
            emoji="👤",
            value=staff_member.mention if staff_member else "Not Found",
            inline=True,
        )
        embed.add_field(
            name="Time in Queue",
            emoji="⏳",
            value=f"{session.queue_time}",
            inline=True,
        )
        embed.add_field(
            name="Time with Staff",
            emoji="⏱️",
            value=f"{session.time_with_staff}",
        )
        return embed


class OtherResponseModal(CoordinateBotModal, SessionFetcher):

    improvement = discord.ui.TextInput(
        label="What could we do better?",
        style=discord.TextStyle.long,
    )

    def __init__(self, bot: CoordinateBot, star_count: int, other_options: list[str]):
        self.bot = bot
        self.star_count = star_count
        self.other_options = other_options
        super().__init__(title="Other Feedback")
        SessionFetcher.__init__(self, bot)

    async def on_submit(self, interaction: discord.Interaction):
        values = self.other_options
        values.append(f"Other response: {self.improvement.value}")
        embed = await self.feedback_embed(self.star_count, interaction.user.id)
        embed.add_field(
            name="Improvement Areas",
            emoji="🔍",
            value="\n".join([f"* {v}" for v in values]),
        )
        await self.bot.feedback_channel.send(embed=embed)
        await interaction.response.send_message(
            "Thank you for your feedback!",
            ephemeral=True,
        )


class ImprovementSelect(discord.ui.Select, SessionFetcher):
    def __init__(self, bot: CoordinateBot, star_count: int):
        self.bot = bot
        self.star_count = star_count
        options = [
            discord.SelectOption(
                label="Office hours are not held frequently enough",
                emoji="🕒",
            ),
            discord.SelectOption(
                label="Office hours are not held at convenient times",
                emoji="⏰",
            ),
            discord.SelectOption(
                label="The office hours system is difficult to use",
                emoji="🤨",
            ),
            discord.SelectOption(label="The wait times are too long", emoji="⏳"),
            discord.SelectOption(label="My staff member was not helpful", emoji="🤷"),
            discord.SelectOption(label="Other / Provide own response", emoji="📝"),
        ]
        super().__init__(
            placeholder="What are some challenges you see?",
            options=options,
            max_values=len(options) - 1,
        )
        SessionFetcher.__init__(self, bot)

    async def callback(self, interaction: discord.Interaction):
        for value in self.values:
            if value == "Other / Provide own response":
                passed_vals = self.values.copy()
                passed_vals.remove(value)
                return await interaction.response.send_modal(
                    OtherResponseModal(self.bot, self.star_count, passed_vals),
                )
        embed = await self.feedback_embed(self.star_count, interaction.user.id)
        embed.add_field(
            name="Improvement Areas",
            emoji="🔍",
            value="\n".join([f"* {v}" for v in self.values]),
        )
        await self.bot.feedback_channel.send(embed=embed)


class ThankYouSelect(discord.ui.Select, SessionFetcher):
    def __init__(self, bot: CoordinateBot, star_count: int):
        self.star_count = star_count
        self.bot = bot
        options = [
            discord.SelectOption(label="...having an awesome character!", emoji="😊"),
            discord.SelectOption(label="...seeing me quickly!", emoji="🏃"),
            discord.SelectOption(label="...being so helpful!", emoji="🤝"),
            discord.SelectOption(label="...being so patient!", emoji="🧘"),
            discord.SelectOption(label="...being so knowledgeable!", emoji="🧠"),
            discord.SelectOption(label="...acting kind towards me!", emoji="🥰"),
        ]
        super().__init__(
            placeholder="Thanks for...",
            options=options,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        full_statement = "Thank you " + self.values[0][3:]
        embed = discord.Embed(
            title="Thank you!",
            description=f"A student who recently visited you in office hours was asked to provide anonymous feedback, and rated their session with you 5 stars. They also included a little thank you note:\n\n> {full_statement}\n\nKeep up the fabulous work!",
            color=discord.Color.pink(),
        )
        session = await self.recent_session(interaction.user.id)
        if session is not None:
            staff_member = await self.bot.get_member(session.staff_id or 0)
            await staff_member.send(embed=embed)
        embed = await self.feedback_embed(self.star_count, interaction.user.id)
        embed.add_field(name="Thank You Message", emoji="💌", value=full_statement)
        await self.bot.feedback_channel.send(embed=embed)
        await interaction.response.send_message(
            "Thank you, your feedback was recorded! We appreciate you filling out this form - this helps us improve our setup and bring it to other educational environments!",
        )


class StarButton(discord.ui.Button):
    def __init__(self, bot: CoordinateBot, number_of_stars: int):
        self.star_count = number_of_stars
        self.bot = bot
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="⭐" * number_of_stars,
            custom_id=f"starfeedback:star_{number_of_stars}",
        )

    async def callback(self, interaction: discord.Interaction):
        view = CoordinateBotView()
        if self.star_count < 5:
            view.add_item(ImprovementSelect(self.bot, self.star_count))
            await interaction.response.send_message(
                "Thank you for your feedback! Sorry our system did not meet your expectations. Could you select a few areas you think we could improve at? Please select as many options as you see fit!",
                view=view,
            )
        else:
            view.add_item(ThankYouSelect(self.bot, self.star_count))
            await interaction.response.send_message(
                "Thank you for your feedback! We're glad you had a great experience. Would you be willing to send an anonymous thank-you to the staff member you worked with?",
                view=view,
            )


class StarView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)
        for i in range(1, 6):
            self.add_item(StarButton(bot, i))


class OfficeHoursFeedbackSender:
    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    async def send_feedback_request(self, member: discord.Member):
        embed = EmojiEmbed(
            title="How was your office hours experience?",
            description=f"Hi, {member.mention}! We hope that you enjoyed your recent office hours experience. If you could, we would appreciate you taking a moment to rate your experience with our office hours system.\n\n**Your identity will not be stored with your response in any way, and completing this form will not impact your grade, ability to attend office hours in the future, or the work eligibility of the staff member you worked with.**\n\nIf you would be so kind - first, could you rate your experience on a scale of 1-5 stars?",
            color=discord.Color.gold(),
        )
        try:
            await member.send(embed=embed, view=StarView(self.bot))
            logger.info(f"Sent feedback request to {member}")
        except discord.Forbidden:
            logger.info(
                f"Could not send feedback request to {member} - DMs are disabled.",
            )
