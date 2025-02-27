from __future__ import annotations

import calendar
import datetime
import functools
import logging
import random
from typing import TYPE_CHECKING

import discord

from ..components import emoji_button
from ..db import (
    AddOfficeHoursRequest,
    MoveOfficeHoursRequest,
    RemoveOfficeHoursRequest,
    TimeslotMethod,
)
from ..utils import emoji_header, parse_datetime
from ..views import Confirm, CoordinateBotModal, CoordinateBotView
from .approvals import OHApprovalView
from .components import StaffMemberSelect
from .views import StaffMemberView

if TYPE_CHECKING:
    from ..bot import CoordinateBot
    from ..db import StaffMember, Timeslot


logger = logging.getLogger(__name__)


def color_from_warnings(warnings: list[str]) -> discord.Color:
    if len(warnings) == 0:
        return discord.Color.green()
    elif len(warnings) == 1:
        return discord.Color.yellow()
    elif len(warnings) == 2:
        return discord.Color.orange()
    return discord.Color.red()


class OfficeHoursModal(CoordinateBotModal):
    def __init__(self, bot: CoordinateBot, title: str):
        self.bot = bot
        super().__init__(title=title)

    async def get_staff_member(
        self,
        interaction: discord.Interaction,
    ) -> StaffMember:
        inputs = [
            (n, v)
            for n, v in self.__dict__.items()
            if isinstance(v, discord.ui.TextInput)
        ]
        values = [f"{n} = '{i.value}'" for n, i in inputs]
        logger.info(
            f"Parsing responses from {self.__class__.__name__} for {interaction.user} ({interaction.user.display_name}): ({', '.join(values)})",
        )

        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(member=interaction.user)
        return staff_member


# An office hours modal that changes depending on the OH method
# will take a TimeslotMethod as a parameter of the constructor and display
# different TextInputs depending on the method
class MethodOHModal(OfficeHoursModal):
    meeting_url = None
    room = None

    def __init__(self, bot: CoordinateBot, title: str, method: TimeslotMethod):
        super().__init__(bot, title)
        self.method = method
        if method == TimeslotMethod.ZOOM or method == TimeslotMethod.TEAMS:
            self.meeting_url = discord.ui.TextInput(
                label="Meeting URL",
                placeholder="What URL will you use for office hours?",
            )
            self.add_item(self.meeting_url)
        elif method == TimeslotMethod.INPERSON:
            self.room = discord.ui.TextInput(
                label="Building + Room",
                placeholder="MALA 5200",
            )
            self.add_item(self.room)

    def get_room(self) -> str | None:
        return self.room.value if self.room else None

    def get_meeting_url(self) -> str | None:
        return self.meeting_url.value if self.meeting_url else None

    def get_method_info(self) -> str:
        info = self.method.display_name
        if self.meeting_url is not None and (
            self.method == TimeslotMethod.ZOOM or self.method == TimeslotMethod.TEAMS
        ):
            info += f" ({self.meeting_url.value})"
        elif self.room is not None and self.method == TimeslotMethod.INPERSON:
            info += f" ({self.room.value})"
        return info


class AddOfficeHoursModal(MethodOHModal):
    start_time = discord.ui.TextInput(
        label="Start Time",
        placeholder="1/7 8PM",
        required=True,
    )
    length = discord.ui.TextInput(
        label="Length (Hours, Decimals Okay)",
        placeholder="2",
        required=True,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why do you want to add more OH?",
        required=True,
        style=discord.TextStyle.long,
    )

    def __init__(
        self,
        bot: CoordinateBot,
        method: TimeslotMethod,
        *,
        user_id: int | None = None,
    ):
        super().__init__(bot, title="Add Office Hours", method=method)
        self.discord_uid = user_id

    async def on_submit(self, interaction: discord.Interaction):
        user = await super().get_staff_member(interaction)
        if self.discord_uid:
            async with self.bot.db_factory() as db:
                user = await db.get_staff_member(id=self.discord_uid)
        start = parse_datetime(
            self.start_time.value,
            allow_past=await self.bot.is_course_lead(interaction.user),
        )
        end = start + datetime.timedelta(hours=float(self.length.value))

        async with self.bot.db_factory() as db:
            schedule = await db.get_staff()

        # Check for warnings
        warnings = []
        # 1. Is there already 4 or more people at this time?
        count = 0
        for staff_member in [s for s in schedule if s.id != interaction.user.id]:
            for routine in staff_member.routines:
                for time in routine.timeslots:
                    if time.start == start:
                        count += 1
        if count >= 4:
            warnings.append(
                f"There are already **{count}** other office hour rooms opening at this time.",
            )
        # 2. Hours begin on a weekend
        start_weekday = start.date().weekday()
        if start_weekday in (calendar.SATURDAY, calendar.SUNDAY):
            warnings.append(
                f"These office hours begin on **{'Saturday' if start_weekday == 5 else 'Sunday'}**.",
            )

        # 3. Hours begin at a weird time
        if start.time() < datetime.time(8, 0) or start.time() > datetime.time(20, 00):
            warnings.append("These office hours begin at an odd time.")

        # 4. Date is outside of this semester
        current_month = datetime.datetime.now().month
        if (
            (current_month < 5 and not 1 <= start.month < 5)  # spring
            or (5 <= current_month <= 8 and not 5 <= start.month <= 8)  # summer
            or (8 <= current_month <= 12 and start.month < 8)  # fall
        ):
            logger.warn(
                f"In request by {interaction.user} to add OH, start month was {start.month}, when current month is {current_month}: warning for different semester",
            )
            warnings.append(
                "These office hours might occur during a different semester.",
            )

        # 5. Year is incorrect
        if start.year != datetime.datetime.now().year:
            warnings.append("These office hours might occur during a different year.")

        approval_embed = discord.Embed(
            title="Add OH Request",
            color=color_from_warnings(warnings),
            description="Details of the request are described below.",
        )
        approval_embed.add_field(
            name="__Starts__",
            value=f"{discord.utils.format_dt(start, 'F')}\n({discord.utils.format_dt(start, 'R')})",
            inline=False,
        )
        approval_embed.add_field(
            name="__Ends__",
            value=f"{discord.utils.format_dt(end, 'F')}\n({discord.utils.format_dt(end, 'R')})",
            inline=False,
        )
        approval_embed.add_field(
            name="Method",
            value=f"{self.get_method_info()}",
            inline=False,
        )
        approval_embed.add_field(name="Reason", value=self.reason.value, inline=False)
        # Get canvas avatar URL
        async with self.bot.db_factory() as db:
            schedule = await db.get_staff_member(member=interaction.user)
        thumbnail = await self.bot.canvas.get_thumbnail(schedule.name)
        if thumbnail:
            approval_embed.set_thumbnail(url=thumbnail)

        warning_string = "_No warnings were found for this request - all good!_"
        if warnings:
            warning_string = "\n".join([f"* {txt}" for txt in warnings])
        approval_embed.add_field(
            name="Potential Warnings",
            value=warning_string,
            inline=False,
        )
        approval_message = f"{interaction.user.mention} would like to add a new time slot to {user.pronouns} office hours schedule."

        confirm_view = Confirm(interaction.user)
        await interaction.response.send_message(
            "Please ensure that the adding office hours request looks appropriate.",
            embed=approval_embed,
            view=confirm_view,
            ephemeral=True,
        )
        await confirm_view.wait()

        if not confirm_view.value:
            await interaction.edit_original_response(
                content="The operation was cancelled.",
                view=None,
                embed=None,
            )
            return
        if not self.discord_uid:  # user is adding OH for themself
            message = await self.bot.office_hours_approvals_ch.send(
                approval_message,
                embed=approval_embed,
                view=OHApprovalView(self.bot),
            )

            async with self.bot.db_factory() as db:
                await db.create_oh_request(
                    message.id,
                    user,
                    self.reason.value,
                    AddOfficeHoursRequest(
                        start,
                        end,
                        self.method,
                        self.get_room(),
                        self.get_meeting_url(),
                    ),
                )

            await interaction.edit_original_response(
                content=f"Thank you. Your request has been created in {self.bot.office_hours_approvals_ch.mention}.",
                view=None,
                embed=None,
            )
        else:
            async with self.bot.db_factory() as db:
                await db.add_timeslot(
                    user,
                    start,
                    end,
                    method=self.method,
                    room=self.get_room(),
                    meeting_url=self.get_meeting_url(),
                )
            try:
                discord_user = self.bot.active_guild.get_member(self.discord_uid)
                if not discord_user:
                    discord_user = await self.bot.active_guild.fetch_member(
                        self.discord_uid,
                    )
            except discord.DiscordException:

                class _FakeUser:
                    def __init__(self):
                        self.mention = "`user not found`"

                discord_user = _FakeUser()
            await self.bot.office_hours_approvals_ch.send(
                f"{interaction.user.mention} added a new office hours slot for {discord_user.mention}. The request was immediately approved because {interaction.user.mention} is an administrator.",
                embed=approval_embed,
            )
            await interaction.edit_original_response(
                content="Thank you. The operation was completed.",
                view=None,
                embed=None,
            )


class MoveOfficeHoursModal(MethodOHModal):
    new_time = discord.ui.TextInput(
        label="New Time",
        placeholder="1/7 8PM",
        required=True,
    )
    length = discord.ui.TextInput(
        label="Length (Hours, Decimals Okay)",
        placeholder="2",
        required=True,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why do you want to move this office hours timeslot?",
        required=True,
        style=discord.TextStyle.long,
    )

    def __init__(
        self,
        bot: CoordinateBot,
        time_to_change: Timeslot,
        user: discord.User | discord.Member,
        method: TimeslotMethod,
        *,
        discord_uid: int | None = None,
    ):
        self.time = time_to_change
        self.discord_uid = discord_uid
        self.method = method
        super().__init__(bot, title="Move Office Hours", method=method)

    async def on_submit(self, interaction: discord.Interaction):
        user = await super().get_staff_member(interaction)
        if self.discord_uid:
            async with self.bot.db_factory() as db:
                user = await db.get_staff_member(id=self.discord_uid)

        # Determine new time
        start = parse_datetime(
            self.new_time.value,
            allow_past=await self.bot.is_course_lead(interaction.user),
        )
        delta = datetime.timedelta(hours=float(self.length.value))
        end = start + delta

        old_delta = self.time.end - self.time.start

        async with self.bot.db_factory() as db:
            schedule = await db.get_staff()

        # Check for warnings
        warnings = []
        # 1. Is there already 4 or more people at this time?
        count = 0
        for staff_member in [s for s in schedule if s.id != interaction.user.id]:
            for routine in staff_member.routines:
                for time in routine.timeslots:
                    if time.start == start:
                        count += 1
        if count >= 4:
            warnings.append(
                f"There are already **{count}** other office hour rooms opening at this time.",
            )

        # 2. Hours begin on a weekend
        start_weekday = start.date().weekday()
        if start_weekday in (calendar.SATURDAY, calendar.SUNDAY):
            warnings.append(
                f"These office hours begin on **{'Saturday' if start_weekday == 5 else 'Sunday'}**.",
            )

        # 3. Hours begin at a weird time
        if start.time() < datetime.time(8, 0) or start.time() > datetime.time(20, 00):
            warnings.append("These office hours begin at an odd time.")

        # 4. Date is outside of this semester
        current_month = datetime.datetime.now().month
        if (
            (current_month < 5 and not 1 <= start.month < 5)  # spring
            or (5 <= current_month <= 8 and not 5 <= start.month <= 8)  # summer
            or (8 <= current_month <= 12 and start.month < 8)  # fall
        ):
            logger.warn(
                f"In request by {interaction.user} to move OH, start month was {start.month}, when current month is {current_month}: warning for different semester",
            )
            warnings.append(
                "These office hours might occur during a different semester.",
            )

        # 5. Year is incorrect
        if start.year != datetime.datetime.now().year:
            warnings.append("These office hours might occur during a different year.")

        approval_embed = discord.Embed(
            title="Move OH Request",
            color=color_from_warnings(warnings),
            description="Details of the request are described below.",
        )

        previous_length = (
            f"{old_delta.seconds//3600} hours, {old_delta.seconds//60 % 60} minutes"
        )
        new_length = f"{delta.seconds//3600} hours, {delta.seconds//60 % 60} minutes"
        approval_embed.add_field(
            name=emoji_header(":hourglass:", "Previously Started"),
            value=f"{discord.utils.format_dt(self.time.start, 'F')}\n({discord.utils.format_dt(self.time.start, 'R')})\n(`{previous_length}`)",
            inline=False,
        )
        approval_embed.add_field(
            name=emoji_header(":arrow_right:", "Requested Move To"),
            value=f"{discord.utils.format_dt(start, 'F')}\n({discord.utils.format_dt(start, 'R')})\n(`{new_length}`)",
            inline=False,
        )
        approval_embed.add_field(
            name=emoji_header(":gear:", "Method"),
            value=self.get_method_info(),
            inline=False,
        )
        approval_embed.add_field(
            name=emoji_header(":pencil:", "Reason"),
            value=self.reason.value,
            inline=False,
        )
        async with self.bot.db_factory() as db:
            schedule = await db.get_staff_member(member=interaction.user)
        if schedule:
            canvas_course = await self.bot.canvas.get_course()
            canvas_users = await self.bot.canvas.get_users(
                canvas_course,
                schedule.name,
                include=["avatar_url"],
            )
            if canvas_users:
                approval_embed.set_thumbnail(url=canvas_users[0]["avatar_url"])

        warning_string = "_No warnings were found for this request - all good!_"
        if warnings:
            warning_string = "\n".join([f"* {txt}" for txt in warnings])
        approval_embed.add_field(
            name=emoji_header(":warning:", "Potential Warnings"),
            value=warning_string,
            inline=False,
        )

        confirm_view = Confirm(interaction.user)
        await interaction.response.send_message(
            f"Please ensure that you would like to move these office hours for {user.name}.",
            embed=approval_embed,
            view=confirm_view,
            ephemeral=True,
        )
        await confirm_view.wait()
        if not confirm_view.value:
            await interaction.edit_original_response(
                content="The operation was cancelled.",
                view=None,
                embed=None,
            )
            return

        if not self.discord_uid:
            approval_message = f"{interaction.user.mention} would like to move a time slot in {user.pronouns} office hours schedule."
            message = await self.bot.office_hours_approvals_ch.send(
                approval_message,
                embed=approval_embed,
                view=OHApprovalView(self.bot),
            )

            async with self.bot.db_factory() as db:
                self.time = await db.merge(self.time)
                user = await db.merge(user)
                await db.create_oh_request(
                    message.id,
                    user,
                    self.reason.value,
                    MoveOfficeHoursRequest(
                        self.time,
                        start,
                        end,
                        self.method,
                        self.get_room(),
                        self.get_meeting_url(),
                    ),
                )

            await interaction.edit_original_response(
                content=f"Thank you. Your request has been created in {self.bot.office_hours_approvals_ch.mention}.",
                view=None,
                embed=None,
            )
        else:
            async with self.bot.db_factory() as db:
                await db.move_timeslot(
                    self.time,
                    start,
                    end,
                    self.method,
                    self.get_room(),
                    self.get_meeting_url(),
                )
            try:
                discord_user = self.bot.active_guild.get_member(self.discord_uid)
                if not discord_user:
                    discord_user = await self.bot.active_guild.fetch_member(
                        self.discord_uid,
                    )
            except discord.DiscordException:

                class _FakeUser:
                    def __init__(self):
                        self.mention = "`user not found`"

                discord_user = _FakeUser()
            await self.bot.office_hours_approvals_ch.send(
                f"{interaction.user.mention} moved an office hours slot for {discord_user.mention}. The request was immediately approved because {interaction.user.mention} is an administrator.",
                embed=approval_embed,
            )
            await interaction.edit_original_response(
                content="Thank you. The operation was completed.",
                view=None,
                embed=None,
            )


class RemoveOfficeHoursModal(OfficeHoursModal):
    removal_reasons = [
        "Going to build legos with Professor Fox",
        "Need to pick up pizza at Gumby's",
        "I'm planning to get sick on this day",
        "Need to attend my goldfish's open-heart surgery",
        "Scheduled to set the world record for longest nap on this day",
        "Competing in the International Sock Pairing Championship.",
        "Interview with NASA on this day to explain memes to aliens",
        "Going to be working on Rubber Duck NFT/crypto side hustle",
        "Working on my uber-cool sun-powered car",
        "Going to be working on my new startup, 'TikTok for Dogs'",
    ]
    reason = discord.ui.TextInput(
        label="Reason",
        required=True,
        style=discord.TextStyle.long,
    )

    def __init__(
        self,
        bot: CoordinateBot,
        selected_time: Timeslot,
        user: discord.User | discord.Member,
        discord_uid: int | None = None,
    ):
        self.bot = bot
        self.selected_time = selected_time
        self.discord_uid = discord_uid
        self.reason.placeholder = random.choice(self.removal_reasons)
        super().__init__(self.bot, title="Remove Request")

    async def on_submit(self, interaction: discord.Interaction):
        approval_embed = discord.Embed(
            title="Remove OH Request",
            color=color_from_warnings([]),
            description="Details of the request are described below.",
        )
        time_to_remove = self.selected_time
        approval_embed.add_field(
            name="__Starts__",
            value=f"{discord.utils.format_dt(time_to_remove.start, 'F')}\n({discord.utils.format_dt(time_to_remove.start, 'R')})",
            inline=True,
        )
        approval_embed.add_field(
            name="__Ends__",
            value=f"{discord.utils.format_dt(time_to_remove.end, 'F')}\n({discord.utils.format_dt(time_to_remove.end, 'R')})",
            inline=True,
        )
        approval_embed.add_field(
            name="__Reason__",
            value=self.reason.value,
            inline=False,
        )
        async with self.bot.db_factory() as db:
            schedule = await db.get_staff_member(member=interaction.user)
        if schedule:
            canvas_course = await self.bot.canvas.get_course()
            canvas_users = await self.bot.canvas.get_users(
                canvas_course,
                schedule.name,
                include=["avatar_url"],
            )
            if canvas_users:
                approval_embed.set_thumbnail(url=canvas_users[0]["avatar_url"])

        async with self.bot.db_factory() as db:
            user = await db.get_staff_member(
                id=self.discord_uid or interaction.user.id,
            )

        confirm_view = Confirm(interaction.user)
        await interaction.response.send_message(
            "Please ensure that you would like to remove these office hours.",
            view=confirm_view,
            embed=approval_embed,
            ephemeral=True,
        )
        await confirm_view.wait()
        if not confirm_view.value:
            await interaction.edit_original_response(
                content="The operation was cancelled.",
                view=None,
                embed=None,
            )

        if not self.discord_uid:
            approval_message = f"{interaction.user.mention} would like to remove {user.pronouns} office hours beginning at {discord.utils.format_dt(self.selected_time.start, 'F')}."
            message = await self.bot.office_hours_approvals_ch.send(
                approval_message,
                embed=approval_embed,
                view=OHApprovalView(self.bot),
            )

            async with self.bot.db_factory() as db:
                self.selected_time = await db.merge(self.selected_time)
                user = await db.merge(user)
                await db.create_oh_request(
                    message.id,
                    user,
                    self.reason.value,
                    RemoveOfficeHoursRequest(self.selected_time),
                )

            await interaction.edit_original_response(
                content=f"Thank you. Your request has been created in {self.bot.office_hours_approvals_ch.mention}.",
                view=None,
                embed=None,
            )
        else:
            async with self.bot.db_factory() as db:
                await db.remove_timeslot(self.selected_time)
            try:
                discord_user = self.bot.active_guild.get_member(
                    self.discord_uid,
                )
                if not discord_user:
                    discord_user = await self.bot.active_guild.fetch_member(
                        self.discord_uid,
                    )
            except discord.DiscordException:

                class _FakeUser:
                    def __init__(self):
                        self.mention = "`user not found`"

                discord_user = _FakeUser()
            await self.bot.office_hours_approvals_ch.send(
                f"{interaction.user.mention} removed a new office hours slot for {discord_user.mention}. The request was immediately approved because {interaction.user.mention} is an administrator.",
                embed=approval_embed,
            )
            await interaction.edit_original_response(
                content="Thank you. The operation was completed.",
                view=None,
                embed=None,
            )


class DelayOfficeHoursModal(OfficeHoursModal):
    length = discord.ui.TextInput(
        label="Delay Length (Minutes)",
        placeholder="10",
        required=True,
    )

    def __init__(self, bot: CoordinateBot, time_to_change: Timeslot):
        self.time = time_to_change
        super().__init__(bot, title="Delay Office Hours")

    async def on_submit(self, interaction: discord.Interaction):
        user = await super().get_staff_member(interaction)

        if int(self.length.value) > 60:
            await interaction.response.send_message(
                'You cannot delay your office hours by more than 60 minutes. Instead, please use a "Move" request, but note that this type of request will need approval from a professor.',
                ephemeral=True,
            )
            return

        if int(self.length.value) < 0:
            await interaction.response.send_message(
                "You cannot delay your office hours by a negative amount of time.",
                ephemeral=True,
            )
            return

        new_start = self.time.start + datetime.timedelta(minutes=int(self.length.value))
        new_end = self.time.end + datetime.timedelta(minutes=int(self.length.value))

        # Associate selected time with discrete value
        async with self.bot.db_factory() as db:
            await db.move_timeslot(self.time, new_start, new_end)

        approval_message = f"{interaction.user.mention} delayed {user.pronouns} office hours beginning at {discord.utils.format_dt(self.time.start, 'F')} by {self.length.value} minutes. This request was automatically approved because the delay length was less than 60 minutes. (begins {discord.utils.format_dt(new_start, 'R')})"
        await self.bot.office_hours_approvals_ch.send(approval_message)

        await interaction.response.send_message(
            f"The office hours starting at {discord.utils.format_dt(self.time.start, 'F')} now begin at {discord.utils.format_dt(new_start, 'F')}!",
            ephemeral=True,
        )


class OfficeHoursPickerDropdown(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        times: list[Timeslot],
        *,
        discord_uid: int | None = None,
    ):
        self.bot = bot
        options = []
        times.sort(key=lambda x: x.start)
        self.times = times
        self.discord_uid = discord_uid
        for _, time in enumerate(times):
            options.append(time.select_option)
        super().__init__(
            placeholder="First, choose a timeslot...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def get_chosen_timeslot(self) -> Timeslot:
        # Associate selected time with discrete value
        return next(time for time in self.times if time.id == int(self.values[0]))


class RemoveOfficeHoursPickerDropdown(OfficeHoursPickerDropdown):
    async def callback(self, interaction: discord.Interaction):
        selected_time = await self.get_chosen_timeslot()
        await interaction.response.send_modal(
            RemoveOfficeHoursModal(
                self.bot,
                selected_time,
                interaction.user,
                discord_uid=self.discord_uid,
            ),
        )


class MoveOfficeHoursPickerDropdown(OfficeHoursPickerDropdown):
    async def callback(self, interaction: discord.Interaction):
        selected_time = await self.get_chosen_timeslot()
        view = OHMethodSelectView(
            functools.partial(
                MoveOfficeHoursModal,
                bot=self.bot,
                time_to_change=selected_time,
                user=interaction.user,
                discord_uid=self.discord_uid,
            ),
        )
        await interaction.response.send_message(
            "What method should be used for these office hours?",
            view=view,
            ephemeral=True,
        )


class DelayOfficeHoursPickerDropdown(OfficeHoursPickerDropdown):
    async def callback(self, interaction: discord.Interaction):
        selected_time = await self.get_chosen_timeslot()
        await interaction.response.send_modal(
            DelayOfficeHoursModal(self.bot, selected_time),
        )


class OfficeHoursPickerView(CoordinateBotView):
    def __init__(
        self,
        bot: CoordinateBot,
        times: list[Timeslot],
        picker_cls: type[OfficeHoursPickerDropdown],
        *,
        discord_uid: int | None = None,
    ):
        super().__init__()
        self.add_item(
            picker_cls(bot, times, discord_uid=discord_uid),
        )


# allow a student or admin to select which method they will be hosting office hours with
class OHMethodSelectView(CoordinateBotView):
    def __init__(self, next_modal_partial: functools.partial[MethodOHModal]):
        super().__init__()
        # next_modal constructor with all arguments except method already embedded in partial
        self.next_modal_partial = next_modal_partial

    @discord.ui.select(
        placeholder="Choose a method...",
        options=[method.to_option() for method in TimeslotMethod],
    )
    async def method(self, interaction: discord.Interaction, select: discord.ui.Select):
        display_name_to_enum = {
            method.display_name: method for method in TimeslotMethod
        }
        await interaction.response.send_modal(
            self.next_modal_partial(
                method=display_name_to_enum[select.values[0]],
            ),
        )


class OfficeHoursUpdateView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        super().__init__(timeout=None)
        self.bot = bot

    @emoji_button(
        emoji="➕",  # noqa
        label="Add",
        style=discord.ButtonStyle.green,
        custom_id="oh_update:add",
    )
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button):
        assert isinstance(interaction.user, discord.Member)  # guild-only interaction
        if await self.bot.is_course_lead(interaction.user):
            async with self.bot.db_factory() as db:
                schedule = list(await db.get_staff())
            schedule.sort(key=lambda doc: doc.name)
            view = StaffMemberView(self.bot, schedule, StaffMemberSelectAdd)
            await interaction.response.send_message(
                "Please select which staff member you would like to add a new single time slot for.",
                view=view,
                ephemeral=True,
            )
        else:
            view = OHMethodSelectView(
                functools.partial(
                    AddOfficeHoursModal,
                    bot=self.bot,
                ),
            )
            await interaction.response.send_message(
                "What method would you like to use for these office hours?",
                view=view,
                ephemeral=True,
            )

    @emoji_button(
        emoji="🔀",
        label="Edit/Move",
        style=discord.ButtonStyle.gray,
        custom_id="oh_update:move",
    )
    async def move(self, interaction: discord.Interaction, _: discord.ui.Button):
        assert isinstance(interaction.user, discord.Member)  # guild-only interaction
        if await self.bot.is_course_lead(interaction.user):
            async with self.bot.db_factory() as db:
                schedule = list(await db.get_staff())
            schedule.sort(key=lambda doc: doc.name)
            view = StaffMemberView(self.bot, schedule, StaffMemberSelectMove)
            await interaction.response.send_message(
                "Please select which staff member you would like to move a single time slot for.",
                view=view,
                ephemeral=True,
            )
        else:
            async with self.bot.db_factory() as db:
                staff_member = await db.get_staff_member(id=interaction.user.id)
            await interaction.response.send_message(
                "Please choose the appropriate office hours section to move below.",
                view=OfficeHoursPickerView(
                    self.bot,
                    staff_member.upcoming_timeslots()[:25],
                    MoveOfficeHoursPickerDropdown,
                ),
                ephemeral=True,
            )

    @emoji_button(
        label="Remove",
        style=discord.ButtonStyle.red,
        custom_id="oh_update:remove",
        emoji="🗑️",
    )
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button):
        assert isinstance(interaction.user, discord.Member)  # guild-only interaction
        if await self.bot.is_course_lead(interaction.user):
            async with self.bot.db_factory() as db:
                schedule = list(await db.get_staff())
            schedule.sort(key=lambda doc: doc.name)
            view = StaffMemberView(self.bot, schedule, StaffMemberSelectRemove)
            await interaction.response.send_message(
                "Please select which staff member you would like to remove a single time slot for.",
                view=view,
                ephemeral=True,
            )
        else:
            async with self.bot.db_factory() as db:
                staff_member = await db.get_staff_member(id=interaction.user.id)
            await interaction.response.send_message(
                "Please choose the appropriate office hours section to remove below.",
                view=OfficeHoursPickerView(
                    self.bot,
                    staff_member.upcoming_timeslots()[:25],
                    RemoveOfficeHoursPickerDropdown,
                ),
                ephemeral=True,
            )

    @emoji_button(
        emoji="⏳",
        label="Delay",
        style=discord.ButtonStyle.red,
        custom_id="oh_update:delay",
    )
    async def delay(self, interaction: discord.Interaction, _: discord.ui.Button):
        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(id=interaction.user.id)
        if not staff_member.upcoming_timeslots():
            return await interaction.response.send_message(
                "You do not have any future office hour timeslots scheduled currently.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "Please choose the appropriate office hours section to delay below.",
            view=OfficeHoursPickerView(
                self.bot,
                staff_member.upcoming_timeslots()[:25],
                DelayOfficeHoursPickerDropdown,
            ),
            ephemeral=True,
        )


class StaffMemberSelectAdd(StaffMemberSelect):
    async def callback(self, interaction: discord.Interaction):
        async with self.bot.db_factory() as db:
            staff_obj = await db.get_staff_member(name=self.values[0])
        if staff_obj is not None:
            view = OHMethodSelectView(
                functools.partial(
                    AddOfficeHoursModal,
                    bot=self.bot,
                    user_id=staff_obj.id,
                ),
            )
            await interaction.response.send_message(
                "What method should the PM use for these office hours?",
                view=view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "This user does not exist.",
                ephemeral=True,
            )


class StaffMemberSelectMove(StaffMemberSelect):
    async def callback(self, interaction: discord.Interaction):
        async with self.bot.db_factory() as db:
            staff_obj = await db.get_staff_member(name=self.values[0])
        if staff_obj is None:
            return await interaction.response.send_message(
                "No schedule exists for this user.",
            )
        await interaction.response.send_message(
            "Please choose the appropriate office hours section to move below.",
            view=OfficeHoursPickerView(
                self.bot,
                staff_obj.upcoming_timeslots()[:25],
                MoveOfficeHoursPickerDropdown,
                discord_uid=staff_obj.id,
            ),
            ephemeral=True,
        )


class StaffMemberSelectRemove(StaffMemberSelect):
    async def callback(self, interaction: discord.Interaction):
        async with self.bot.db_factory() as db:
            staff_obj = await db.get_staff_member(name=self.values[0])
            doc = await db.get_staff_member(id=staff_obj.id)
        await interaction.response.send_message(
            "Please choose the appropriate office hours section to remove below.",
            view=OfficeHoursPickerView(
                self.bot,
                doc.upcoming_timeslots()[:25],
                RemoveOfficeHoursPickerDropdown,
                discord_uid=staff_obj.id,
            ),
            ephemeral=True,
        )
