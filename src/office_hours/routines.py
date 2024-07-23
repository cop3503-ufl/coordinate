from __future__ import annotations

import datetime
import functools
import re
import time as time_pkg
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

from ..components import emoji_button
from ..constants import REMOVE_ROUTINE_EMOJI
from ..db import (
    AddRoutineOfficeHoursRequest,
    RemoveRoutineOfficeHoursRequest,
    Routine,
    TimeslotMethod,
)
from ..semesters import Semester, semester_given_date
from ..utils import parse_time
from ..views import Confirm, CoordinateBotView
from .approvals import OHApprovalView
from .components import StaffMemberSelect
from .timeslots import MethodOHModal, OHMethodSelectView
from .views import StaffMemberView

if TYPE_CHECKING:
    from ..bot import CoordinateBot


class StaffMemberSelectAddRoutine(StaffMemberSelect):
    async def callback(self, interaction: discord.Interaction):
        view = OHMethodSelectView(
            functools.partial(
                StaffMemberAddRoutineModal,
                bot=self.bot,
                name=self.values[0],
                instant_approval=True,
            ),
        )
        await interaction.response.send_message(
            "What method should the staff member use for the routine?",
            view=view,
            ephemeral=True,
        )


class StaffMemberSelectRemoveRoutine(StaffMemberSelect):
    async def callback(self, interaction: discord.Interaction):
        async with self.bot.db_factory() as db:
            staff_member = await db.get_staff_member(name=self.values[0])
        if not staff_member.routines:
            return await interaction.response.send_message(
                "Sorry, this staff member has not configured routines yet!",
                ephemeral=True,
            )
        view = discord.ui.View()
        try:
            view.add_item(
                RoutinePickerDropdown(
                    self.bot,
                    staff_member.routines,
                    staff_member.name,
                    instant_approval=True,
                ),
            )
        except IndexError:
            return await interaction.response.send_message(
                "Sorry, this staff member does not have upcoming office hour routines for this semester!",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"Please choose the routine you would like to remove for {self.values[0]}.",
            view=view,
            ephemeral=True,
        )


class StaffMemberAddRoutineModal(MethodOHModal):
    day_of_week = discord.ui.TextInput(
        label="Day of Week",
        placeholder='Use the entire weekday name (ex, "Monday" or "Wednesday")',
        required=True,
    )
    start_time = discord.ui.TextInput(
        label="Start Time",
        placeholder='Use HH:MM AM/PM (ex, "11:30AM" or "5:30 PM")',
        required=True,
    )
    length = discord.ui.TextInput(
        label="Length (Hours, Decimals Okay)",
        placeholder="How long should the routine last?",
        required=True,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why do you want to add this new routine?",
        required=True,
        style=discord.TextStyle.long,
    )

    def __init__(
        self,
        bot: CoordinateBot,
        method: TimeslotMethod,
        *,
        name: str,
        instant_approval: bool,
    ):
        self.bot = bot
        self.method = method
        self.name = name
        self.instant_approval = instant_approval  # Whether the request is instantly approved (skips reports channel)
        super().__init__(bot, title=f"Add Routine for {name}", method=method)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Parse info
        current_semester = semester_given_date(
            datetime.datetime.now(),
            next_semester=True,
        )
        if current_semester is None:
            return await interaction.response.send_message(
                "The bot has an experienced an error due to lack of relevant semesters. Please contact a bot developer.",
                ephemeral=True,
            )
        assert isinstance(current_semester, Semester)
        parsed_time = parse_time(self.start_time.value)

        # Parse weekday
        parsed_weekday = time_pkg.strptime(self.day_of_week.value, "%A").tm_wday

        # Parse length
        parsed_length = float(self.length.value)

        # Get first date
        start_date = max(current_semester.start, datetime.date.today())
        start_weekday = start_date.weekday()
        diff = parsed_weekday - start_weekday
        if diff < 0:
            diff += 7
        first_day = start_date + datetime.timedelta(days=diff)

        # Generate list of times
        async with self.bot.db_factory() as db:
            doc = await db.get_staff_member(name=self.name)
        routine = Routine(
            weekday=first_day.weekday(),
            time=parsed_time,
            staff=None,
            length=parsed_length,
            method=self.method,
            room=self.get_room(),
            meeting_url=self.get_meeting_url(),
        )
        times, excluded_times = routine.generate_timeslots()

        # Ask for confirmation
        formatted_times = "\n".join(
            f"{discord.utils.format_dt(time.start, 'F')}-{discord.utils.format_dt(time.end, 'F')}"
            for time in times
        )
        formatted_excluded = "\n".join(
            f"{discord.utils.format_dt(time.start, 'F')}-{discord.utils.format_dt(time.end, 'F')}"
            for time in excluded_times
        )
        formatted_excluded = f"_The following time slots will not be used for office hours because of university holidays, breaks, events, etc._\n{formatted_excluded}"
        time_embed = discord.Embed(
            title=f"Generated Routine for {self.name}",
            color=discord.Color.green(),
            description="The list of generated times is shown below.",
        )
        time_embed.add_field(
            name="Name",
            value=f"Each {self.day_of_week.value.title()} at {self.start_time.value.upper()}",
            inline=False,
        )
        time_embed.add_field(
            name="Method",
            value=f"{self.get_method_info()}",
            inline=False,
        )
        time_embed.add_field(name="Semester", value=current_semester.name, inline=True)
        time_embed.add_field(
            name="Total Amount of Hours",
            value=round(
                sum(
                    [(time.end - time.start).total_seconds() / 3600 for time in times],
                ),
                2,
            ),
            inline=True,
        )
        time_embed.add_field(
            name="Generated Times",
            value=formatted_times,
            inline=False,
        )
        if excluded_times:
            time_embed.add_field(
                name="Excluded Times (No OH)",
                value=formatted_excluded,
                inline=False,
            )
        time_embed.add_field(
            name="Reason",
            value=self.reason.value,
            inline=False,
        )

        confirm_view = Confirm(interaction.user)
        await interaction.response.send_message(
            "Please ensure that the list of times shown below is correct.",
            embed=time_embed,
            view=confirm_view,
            ephemeral=True,
        )
        await confirm_view.wait()
        if confirm_view.value:
            if self.instant_approval:
                routine.staff = doc
                for time in times:
                    time.staff = doc
                async with self.bot.db_factory() as db:
                    await db.add_routine(routine, times)
                await interaction.edit_original_response(
                    content="The routine has been added to the staff member's schedule.",
                    embed=None,
                    view=None,
                )
            else:
                # Send message
                message = await self.bot.office_hours_approvals_ch.send(
                    f"{interaction.user.mention} would like to add a **new weekly routine** to {doc.pronouns} office hours schedule.",
                    embed=time_embed,
                    view=OHApprovalView(self.bot),
                )

                async with self.bot.db_factory() as db:
                    await db.create_oh_request(
                        message.id,
                        doc,
                        self.reason.value,
                        AddRoutineOfficeHoursRequest(
                            routine.weekday,
                            routine.time,
                            routine.length,
                            start_date,
                            current_semester.end,
                            routine.method,
                            routine.room,
                            routine.meeting_url,
                        ),
                    )

                await interaction.edit_original_response(
                    content="The request has been generated! Have a great day!",
                    embed=None,
                    view=None,
                )
        else:
            await interaction.edit_original_response(
                content="The routine was not confirmed. _sad face_",
                embed=None,
                view=None,
            )


class RoutinePickerDropdown(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        routines: list[Routine],
        name: str,  # name of staff member
        instant_approval: bool = False,
    ):
        self.bot = bot
        self.name = name
        self.instant_approval = instant_approval
        options = []

        routines.sort(key=lambda x: x.timeslots[0].start)
        self.mapped_routines: dict[str, Routine] = {}
        # Determine start date/time
        for routine in routines:
            formats = []
            for timeslot in routine.timeslots:
                if timeslot.end > discord.utils.utcnow():
                    formatted_time = timeslot.start.astimezone(
                        ZoneInfo("US/Eastern"),
                    ).strftime("%A at %-I:%M %p")
                    formats.append(formatted_time)
            if formats:
                actual_format = max(formats, key=formats.count)
                self.mapped_routines[actual_format] = routine

        for i, format in enumerate(self.mapped_routines.keys()):
            options.append(
                discord.SelectOption(
                    label=f"Routine #{i+1}",
                    description=format,
                    emoji="🗓",
                ),
            )

        super().__init__(
            placeholder="Choose the routine to remove...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        # Associate selected time with discrete value
        selected_time = re.findall(r"\#\d+", self.values[0])
        selected_time = int(selected_time[0][1:]) - 1
        selected_format = list(self.mapped_routines.keys())[selected_time]
        selected_routine = list(self.mapped_routines.values())[selected_time]

        if not self.instant_approval:
            approval_message = f"{interaction.user.mention} would like to remove their routine occurring on {selected_format}."
            message = await self.bot.office_hours_approvals_ch.send(
                approval_message,
                view=OHApprovalView(self.bot),
            )

            async with self.bot.db_factory() as db:
                staff_member = await db.get_staff_member(id=interaction.user.id)
                routine = await db.merge(selected_routine)
                await db.create_oh_request(
                    message.id,
                    staff_member,
                    "I want this routine gone, please!",
                    RemoveRoutineOfficeHoursRequest(routine),
                )

            return await interaction.response.send_message(
                f"Thank you. Your request has been created in {self.bot.office_hours_approvals_ch.mention}.",
                ephemeral=True,
            )

        # Make request immediately
        async with self.bot.db_factory() as db:
            routine = await db.merge(selected_routine)
            await db.remove_routine(
                routine,
            )
        return await interaction.response.send_message(
            "The routine has been removed.",
            ephemeral=True,
        )


class OfficeHoursRoutineUpdateView(CoordinateBotView):
    """
    Bot panel view used by both admins and staff members to update individual
    routines of staff member.
    """

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @emoji_button(  # type: ignore
        emoji="🔁",
        label="Add Routine",
        style=discord.ButtonStyle.green,
        custom_id="routine:add",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[OfficeHoursRoutineUpdateView],
    ):
        """
        Add Routine button in the panel. Should start the interaction process
        for adding a brand new routine for a staff member. Admins will need a list
        of staff member to choose from, staff members will get their modal to
        start their request.
        """
        # Select staff member to add routine for if the professor is calling this
        assert isinstance(interaction.user, discord.Member)  # guild-only interaction
        if await self.bot.is_course_lead(interaction.user):
            # Send dropdown containing staff member names
            async with self.bot.db_factory() as db:
                schedule = list(await db.get_staff())
            schedule.sort(key=lambda doc: doc.name)
            view = StaffMemberView(self.bot, schedule, StaffMemberSelectAddRoutine)
            await interaction.response.send_message(
                "Please select which staff member you would like to modify the routines of.",
                view=view,
                ephemeral=True,
            )
        else:
            assert isinstance(interaction.user, discord.Member)
            async with self.bot.db_factory() as db:
                doc = await db.get_staff_member(member=interaction.user)
            view = OHMethodSelectView(
                functools.partial(
                    StaffMemberAddRoutineModal,
                    bot=self.bot,
                    name=doc.name,
                    instant_approval=False,
                ),
            )
            await interaction.response.send_message(
                "What method would you like to use for this routine?",
                view=view,
                ephemeral=True,
            )

    @emoji_button(  # type: ignore
        emoji=REMOVE_ROUTINE_EMOJI,
        label="Remove Routine",
        style=discord.ButtonStyle.red,
        custom_id="routine:remove",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[OfficeHoursRoutineUpdateView],
    ):
        # Goal: send list of routines, remove routine selected
        # Select staff member to add routine for if the professor is calling this
        assert isinstance(interaction.user, discord.Member)  # guild-only interaction
        if await self.bot.is_course_lead(interaction.user):
            async with self.bot.db_factory() as db:
                schedule = list(await db.get_staff())
            schedule.sort(key=lambda doc: doc.name)
            view = StaffMemberView(self.bot, schedule, StaffMemberSelectRemoveRoutine)
            await interaction.response.send_message(
                "Please select which staff member you would like to remove a routine for.",
                view=view,
                ephemeral=True,
            )
        else:
            async with self.bot.db_factory() as db:
                staff_member = await db.get_staff_member(id=interaction.user.id)
            if not staff_member.routines:
                return await interaction.response.send_message(
                    "Sorry, it doesn't look like you have configured routines yet!",
                    ephemeral=True,
                )
            view = discord.ui.View()
            try:
                view.add_item(
                    RoutinePickerDropdown(
                        self.bot,
                        staff_member.routines,
                        staff_member.name,
                    ),
                )
            except IndexError:
                return await interaction.response.send_message(
                    "Sorry, you don't have any routines planned in the future for this semester!",
                    ephemeral=True,
                )
            await interaction.response.send_message(
                "Please choose the routine you would like to remove.",
                view=view,
                ephemeral=True,
            )
