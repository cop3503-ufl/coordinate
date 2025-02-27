from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .components import EmojiEmbed
from .semesters import semester_given_date
from .utils import DateConverter

if TYPE_CHECKING:
    from .bot import CoordinateBot


class StaffCog(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="Staff Member Reactions",
            callback=self.saw_message,
        )
        self.bot.tree.add_command(self.ctx_menu)

    @commands.command(name="pr")
    @commands.is_owner()
    async def send_payroll_reminder(
        self,
        ctx: commands.Context,
        deadline: DateConverter,
        final: DateConverter | None = None,
    ):
        """
        Payroll is due at midnight on Tuesdays, and staff members should estimate
        their hours for Wednesday and Thursday. So, the payroll should be the
        previous previous Friday to next Thursday.
        """
        await ctx.message.delete()
        today = datetime.datetime.today()
        days_behind = (today.weekday() - 4) % 7
        previous_friday = today - datetime.timedelta(days=days_behind)
        previous_friday = previous_friday.replace(hour=0, minute=0, second=0)
        previous_previous_friday = previous_friday - datetime.timedelta(days=7)
        next_thursday = previous_friday + datetime.timedelta(days=6)
        next_thursday = next_thursday.replace(hour=23, minute=59, second=59)
        next_tuesday = next_thursday - datetime.timedelta(days=2)
        if deadline:
            next_tuesday = deadline
            assert isinstance(next_tuesday, datetime.date)
            next_tuesday = datetime.datetime.combine(
                next_tuesday,
                datetime.time(23, 59),
            )
        if final:
            next_thursday = final
            assert isinstance(next_thursday, datetime.date)
            next_thursday = datetime.datetime.combine(
                next_thursday,
                datetime.time(23, 59),
            )
            previous_friday = next_thursday - datetime.timedelta(days=6)
            previous_friday = previous_friday.replace(hour=0, minute=0, second=0)
            previous_previous_friday = previous_friday - datetime.timedelta(days=7)
        embed = EmojiEmbed(
            title="🔔 Reminder to Log Hours",
            color=discord.Color.purple(),
            description="Remember to log your hours on time to avoid delays with processing payroll and getting your time approved.",
        )
        embed.add_field(
            emoji="📆",
            name="Payroll Period",
            value=f"Start: {discord.utils.format_dt(previous_previous_friday, 'F')}\nEnd: {discord.utils.format_dt(next_thursday, 'F')}",
            inline=False,
        )
        embed.add_field(
            emoji="⏰",
            name="Log Deadline",
            value=f"{discord.utils.format_dt(next_tuesday, 'F')} ({discord.utils.format_dt(next_tuesday, 'R')})",
            inline=False,
        )
        embed.set_image(
            url="https://media.discordapp.net/attachments/1091422466448040047/1166034121756250152/image.png?ex=654904e6&is=65368fe6&hm=45e916b23d086423a45a45485e7992ba33bee59e5dd68a175618a7df3b1ae3a4&=&width=1852&height=1058",
        )
        await self.bot.staff_ch.send(embed=embed)

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def saw_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        ta_member_ids = set()
        async with self.bot.db_factory() as db:
            schedule = await db.get_staff()
        semester = semester_given_date(datetime.datetime.now(), next_semester=True)
        if semester is None:
            await interaction.response.send_message(
                "No semester is currently active.",
                ephemeral=True,
            )
            return
        for staff_member in schedule:
            # If staff member has at least one office hours this semester, they are active
            for routine in staff_member.routines:
                for timeslot in routine.timeslots:
                    start_date = timeslot.start.date()
                    if semester.start < start_date < semester.end:
                        ta_member_ids.add(staff_member.id)
                        break

        # Get rid of author ID
        if message.author.id in ta_member_ids:
            ta_member_ids.remove(message.author.id)

        embed = EmojiEmbed(
            title="Which staff members saw my message?",
            description="The following staff members saw your message:",
            color=discord.Color.teal(),
        )
        reacted_with = ""
        for reaction in message.reactions:
            users = []
            async for user in reaction.users():
                if user.id in ta_member_ids:
                    ta_member_ids.remove(user.id)
                users.append(user.mention)
            user_string = "\n".join([f" * {u}" for u in users])
            reacted_with += f"* Reacted with {reaction.emoji}: \n{user_string}\n"
        embed.add_field(
            emoji="👀",
            name="Saw Your Message",
            value=reacted_with,
            inline=False,
        )

        # For staff members that did not respond, determine who can see channel
        in_channel = []
        out_of_channel = []
        not_in_server = []
        for ta_member_id in ta_member_ids:
            ta_member_id = int(ta_member_id)
            ta_member = self.bot.active_guild.get_member(ta_member_id)
            if ta_member is None:
                try:
                    ta_member = await self.bot.active_guild.fetch_member(ta_member_id)
                except discord.NotFound:
                    async with self.bot.db_factory() as db:
                        staff_doc = await db.get_staff_member(id=ta_member_id)
                    if staff_doc:
                        not_in_server.append(staff_doc.name)
                        continue
            channel = interaction.channel
            if (
                isinstance(channel, discord.TextChannel | discord.Thread)
                and ta_member in channel.members
            ):
                in_channel.append(ta_member)
            else:
                out_of_channel.append(ta_member)

        reacted_with = ""
        if in_channel:
            user_string = "\n".join([f" * {member.mention}" for member in in_channel])
            reacted_with += f"* Did not react: \n{user_string}\n"
        if out_of_channel:
            user_string = "\n".join(
                [f" * {member.mention}" for member in out_of_channel],
            )
            reacted_with += f"* Not in this channel: \n{user_string}\n"
        if not_in_server:
            user_string = "\n".join([f" * {member}" for member in not_in_server])
            reacted_with += f"* Not in this server: \n{user_string}\n"

        embed.add_field(
            emoji="🙈",
            name="Didn't See Your Message",
            value=reacted_with,
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)


async def setup(bot: CoordinateBot):
    await bot.add_cog(StaffCog(bot))
