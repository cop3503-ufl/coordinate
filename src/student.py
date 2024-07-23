from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .canvas import Assignment, Course, Submission, User
from .codio import Assignment as CodioAssignment
from .env import CANVAS_URL
from .extensions import (
    ExtendableAssignment,
    ExtendableCanvasAssignment,
    ExtendableGradescopeAssignment,
)
from .office_hours.staff import StudentIDModal
from .registration import LaunchButton
from .semesters import Semester, semester_given_date
from .views import Confirm, CoordinateBotModal, CoordinateBotView

if TYPE_CHECKING:
    from .bot import CoordinateBot


class StudentAssignmentSelect(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        student: User,
        assignments: list[Assignment],
    ):
        self.bot = bot
        self.student = student
        self.canvas = bot.canvas
        self.assignments = assignments
        options = []
        for i, assignment in enumerate(assignments):
            emoji = "💻" if assignment.get("name", "").startswith("Lab") else "📝"
            options.append(
                discord.SelectOption(
                    label=assignment.get("name", ""),
                    value=str(i),
                    emoji=emoji,
                ),
            )
        super().__init__(
            placeholder="Choose a submission to view...",
            options=options,
            max_values=1,
        )

    def get_quiz_embed(
        self,
        assignment: Assignment,
        submission: Submission,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=assignment.get("name", ""),
            color=discord.Color.gold(),
            description="Information about the student's submission is shown.",
        )
        # Parse times into datetime
        if "due_at" in assignment and isinstance(assignment["due_at"], str):
            due_at = datetime.datetime.strptime(
                assignment["due_at"],
                "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=datetime.timezone.utc)
            embed.add_field(
                name="Due Date",
                value=discord.utils.format_dt(due_at, style="F"),
            )
        if "submitted_at" in submission and isinstance(submission["submitted_at"], str):
            submitted_at = datetime.datetime.strptime(
                submission["submitted_at"],
                "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=datetime.timezone.utc)
            embed.add_field(
                name="Submitted At",
                value=discord.utils.format_dt(submitted_at, style="F"),
            )

        if (
            "score" in submission
            and submission["score"] is not None
            and "points_possible" in assignment
        ):
            embed.add_field(
                name="Most Recent Score",
                value=f"{submission['score']}/{assignment['points_possible']} points",
                inline=False,
            )
        embed.set_thumbnail(url=self.student["avatar_url"])
        return embed

    def get_quiz_items(
        self,
        assignment: Assignment,
        submission: Submission,
    ) -> list[discord.ui.Button]:
        submission_url = f"{CANVAS_URL}/courses/{assignment['course_id']}/quizzes/{assignment['quiz_id']}/history?user_id={self.student['id']}"
        first_name = self.student["name"].split()[0]
        items = [
            discord.ui.Button(
                url=assignment["html_url"],
                label="View Assignment",
                style=discord.ButtonStyle.link,
            ),
        ]
        if submission["submitted_at"] is not None:
            items.append(
                discord.ui.Button(
                    url=submission_url,
                    label=f"View {first_name}'s Submissions",
                    style=discord.ButtonStyle.link,
                ),
            )
        else:
            items.append(
                discord.ui.Button(
                    label="No Submission Exists",
                    style=discord.ButtonStyle.red,
                    disabled=True,
                ),
            )

        return items

    async def codio_from_canvas(self, assignment: Assignment) -> CodioAssignment:
        canvas_name = assignment["name"]
        codio_assignment = await self.bot.codio.get_assignment_named(canvas_name)
        return codio_assignment

    async def callback(self, interaction: discord.Interaction):
        chosen_index = int(self.values[0])
        chosen_assignment = self.assignments[chosen_index]
        if chosen_assignment["is_quiz_assignment"]:
            submission = await self.canvas.get_submission(
                chosen_assignment["course_id"],
                chosen_assignment,
                self.student["id"],
            )
            quiz_embed = self.get_quiz_embed(chosen_assignment, submission)
            quiz_items = self.get_quiz_items(chosen_assignment, submission)
            view = discord.ui.View()
            for item in quiz_items:
                view.add_item(item)
            await interaction.response.send_message(
                embed=quiz_embed,
                view=view,
                ephemeral=True,
            )
        else:
            try:
                assignment = await self.codio_from_canvas(chosen_assignment)
            except ValueError:
                await interaction.response.send_message(
                    "This assignment is not available on Codio.",
                    ephemeral=True,
                )
                return
            codio_student = await self.bot.codio.get_student(self.student["name"])
            if codio_student:
                embed = discord.Embed(
                    title=chosen_assignment["name"],
                    color=discord.Color.gold(),
                    description="Information about the student's submission is shown.",
                )
                student_progress = await self.bot.codio.get_progress_for_student(
                    assignment["id"],
                    codio_student["id"],
                )
                if student_progress:
                    seconds_spent = student_progress.get("seconds_spent", 0)
                    hours_spent = seconds_spent // 3600
                    minutes_spent = (seconds_spent % 3600) // 60
                    completion_emoji = "❌"
                    if student_progress.get("status") == "COMPLETED":
                        completion_emoji = "✅"
                    elif student_progress.get("status") == "IN_PROGRESS":
                        completion_emoji = "⏳"
                    grade = "Submission not started, no grade assigned."
                    if "grade" in student_progress:
                        grade = f"{student_progress['grade']}%"
                    embed.add_field(
                        name="Time Spent",
                        value=f"{hours_spent} hours, {minutes_spent} minutes",
                        inline=False,
                    )
                    embed.add_field(
                        name="Completion Status",
                        value=f"{completion_emoji} `{student_progress.get('status', 'NOT_STARTED')}`",
                        inline=True,
                    )
                    embed.add_field(name="Grade", value=grade, inline=True)
                embed.set_thumbnail(url=self.student["avatar_url"])
                view = discord.ui.View()
                url = self.bot.codio.assignment_preview_url(
                    codio_student["login"],
                    assignment["name"],
                )
                view.add_item(
                    discord.ui.Button(
                        url=url,
                        label="View Student Submission",
                        style=discord.ButtonStyle.link,
                    ),
                )
                await interaction.response.send_message(
                    embed=embed,
                    view=view,
                    ephemeral=True,
                )


class EmailModal(CoordinateBotModal):
    email = discord.ui.TextInput(
        label="Gradescope Email",
        placeholder="your.name@institution.edu",
    )

    def __init__(self):
        super().__init__(title="Enter Gradescope Email Address")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()


class AssignmentSelect(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        student_id: int,
        assignments: list[ExtendableAssignment],
    ):
        self.bot = bot
        self.assignments = assignments
        self.student_id = student_id
        options = []
        assignments.sort(
            key=lambda assignment: assignment.due_at
            or (datetime.datetime.max - datetime.timedelta(days=1)).astimezone(),
        )
        # Remove duplicates based on name
        assignments = list({a.name: a for a in assignments}.values())
        for assignment in assignments:
            due_at = assignment.due_at
            # Skip [Start Here] assignments
            if "Quiz" in assignment.name and "Start Here" not in assignment.name:
                continue
            if due_at is None:
                continue
            if due_at < datetime.datetime.now().astimezone() - datetime.timedelta(
                days=1,
            ):
                continue
            options.append(
                discord.SelectOption(
                    label=assignment.name,
                    value=str(assignment.id),
                    emoji="👩‍💻" if "quiz" not in assignment.name.lower() else "📝",
                    description=assignment.due_str,
                ),
            )
        super().__init__(
            placeholder="Choose an assignment to extend...",
            options=options[:5],
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        assignment = next(a for a in self.assignments if str(a.id) == selected)
        if not assignment.due_at:  # will never happen
            return
        assignments_to_extend = [
            a for a in self.assignments if a.name == assignment.name
        ]
        asked_for_email = False
        for a in assignments_to_extend:
            if isinstance(a, ExtendableGradescopeAssignment):
                asked_for_email = True
                modal = EmailModal()
                await interaction.response.send_modal(modal)
                await modal.wait()
                a.student_email = modal.email.value
        if not asked_for_email:
            await interaction.response.defer()
        new_date = assignment.due_at + datetime.timedelta(days=1)
        if "Quiz" in assignment.name:
            quiz_number = int(re.findall(r"Quiz \[(\d+)\]", assignment.name)[0])
            assignments_to_extend = [
                a for a in self.assignments if f"Quiz [{quiz_number}]" in a.name
            ]
        formatted_assignments = "\n".join(
            [
                f"* **{a.name}** (through {a.provider_name})"
                for a in assignments_to_extend
            ],
        )

        # Ask the user to confirm extension
        confirm_embed = discord.Embed(
            title="Confirm Use of Late Pass",
            description=f"Are you sure you want to use a late pass on the following assignment(s)?\n{formatted_assignments}\n\nOnce you use this late pass, you will not be able to use it again!\n\n**Current Due Date:**{discord.utils.format_dt(assignment.due_at, 'F')} ({discord.utils.format_dt(assignment.due_at, 'R')})\n**New Due Date:**{discord.utils.format_dt(new_date, 'F')} ({discord.utils.format_dt(new_date, 'R')})",
            color=discord.Color.dark_red(),
        )
        confirm = Confirm(interaction.user)
        await interaction.followup.send(
            embed=confirm_embed,
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            return await interaction.edit_original_response(
                content="No worries! At this time, you chose not to use your late pass, meaning that you can use it on a future assignment.",
                embed=None,
                view=None,
            )

        # Apply extension
        if "Quiz" in assignment.name:
            quiz_number = int(re.findall(r"Quiz \[(\d+)\]", assignment.name)[0])
            assignments_to_extend = [
                a for a in self.assignments if f"Quiz [{quiz_number}]" in a.name
            ]
        for ass in assignments_to_extend:
            await ass.extend(new_date)

        # Send confirmation
        async with self.bot.db_factory() as db:
            await db.register_late_pass(self.student_id, selected, assignment.name)
        await self.bot.student_requests_ch.send(
            f"**{interaction.user.display_name}** used their late pass on **{assignment.name}**. It is now due {discord.utils.format_dt(new_date, 'R')}.",
        )
        await interaction.followup.send(
            content=f"Your late pass has been applied to the selected assignments. They are now due {discord.utils.format_dt(new_date, 'R')}. You will not be able to use your late pass again.",
            ephemeral=True,
        )


class LatePassView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim Late Pass",
        style=discord.ButtonStyle.red,
        custom_id="late_pass:claim",
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Check if late pass has been used

        # 2. Ask them to choose a future assignment to use late pass on (dropdown extends)
        course = await self.bot.canvas.get_course()
        view = CoordinateBotView()
        ufid_modal = StudentIDModal()
        view.add_item(LaunchButton(self.bot, ufid_modal))
        await interaction.response.send_message(
            "In order to verify your status of a late pass, you will be asked for **your UFID**. Please note that **these values are not stored anywhere**, and are only provided to **Canvas** to verify your enrollment. If you understand this information and would like to proceed, please use the button below.",
            view=view,
            ephemeral=True,
        )

        # Get the user's UFID
        await ufid_modal.wait()
        ufid = ufid_modal.student_id.value
        interaction = (
            ufid_modal.responded_interaction
            if ufid_modal.responded_interaction
            else interaction
        )
        await interaction.response.send_message(
            f"{self.bot.loading_emoji} Fetching assignments from providers...",
            ephemeral=True,
        )
        users = await self.bot.canvas.get_users(course, ufid)
        if not users:
            await interaction.edit_original_response(
                content="Sorry, I couldn't find a student with that UFID. Please try again.",
                view=None,
            )
            return

        async with self.bot.db_factory() as db:
            late_pass = await db.get_late_pass(users[0]["id"])
            if late_pass:
                await interaction.edit_original_response(
                    content="Sorry, you have already used your late pass. You cannot use it again.",
                    view=None,
                )
                return

        course_info = self.bot.get_course_info()
        with_overrides = await self.bot.canvas.get_assignments_with_overrides(
            course_info.canvas_course_code,
        )
        assignments: list[ExtendableAssignment] = [
            ExtendableCanvasAssignment.from_assignment_override(
                a,
                self.bot,
                users[0]["id"],
            )
            for a in with_overrides
            if a["published"]
        ]
        gradescope_assignments = await self.bot.gradescope.get_assignments()
        assignments.extend(
            [
                ExtendableGradescopeAssignment.from_assignment(
                    a,
                    None,
                )
                for a in gradescope_assignments
            ],
        )
        view.clear_items()
        view.add_item(
            AssignmentSelect(
                self.bot,
                users[0]["id"],
                assignments,
            ),
        )
        await interaction.edit_original_response(
            content="Hi there! Ready to use your late pass? First, choose an assignment to extend!",
            view=view,
        )


class StudentCog(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="Student Info",
            callback=self.user_info,  # type: ignore
        )
        self.cmd_menu = app_commands.Command(
            name="student",
            callback=self.user_info,
            description="Looks up information about a given student.",
        )
        self.bot.tree.add_command(self.ctx_menu)
        self.bot.tree.add_command(self.cmd_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def generate_embed(self, member: discord.Member, user: User) -> discord.Embed:
        embed = discord.Embed(
            title=f"{member.display_name}",
            color=discord.Color.gold(),
            description="This is the Canvas page for this user.",
        )

        course = self.bot.get_course_info()
        enrollments = user["enrollments"]
        if enrollments:
            enrollment = enrollments[0]
            sis_id = enrollment.get("sis_section_id") or "?????"
            section_id = sis_id[-5:]
            section = course.sections.get(int(section_id))
            if section:
                formatted_time = section.start_time.strftime("%I:%M %p")
                embed.add_field(
                    name="Section",
                    value=f"`{section_id}` (Thursday {formatted_time} in {section.room}, led by {section.leader})",
                )
            else:
                embed.add_field(
                    name="Section",
                    value=f"❓ Unknown (raw value: `{section_id}`)",
                )
        embed.set_thumbnail(url=user["avatar_url"])

        return embed

    def get_semester(self) -> Semester:
        semester = semester_given_date(datetime.datetime.now())
        assert isinstance(semester, Semester)
        return semester

    async def get_course(self) -> Course:
        course = self.bot.get_course_info()
        assert isinstance(course.canvas_course_code, int)
        course = await self.bot.canvas.get_course(course.canvas_course_code)
        return course

    async def generate_assignment_selector(self, user: User) -> StudentAssignmentSelect:
        assignments = await self.get_recent_assignments()
        return StudentAssignmentSelect(self.bot, user, assignments)

    async def get_recent_assignments(self) -> list[Assignment]:
        course = await self.get_course()
        assignments = await self.bot.canvas.get_assignments(course, order_by="due_at")
        assignments.reverse()
        return [
            a
            for a in assignments
            if a.get("name", "").startswith("Lab")
            or a.get("name", "").startswith("Quiz")
        ][:25]

    @app_commands.checks.has_any_role("TA/PM", "Professor")
    async def user_info(self, interaction: discord.Interaction, member: discord.Member):
        if await self.bot.is_staff(member):
            return await interaction.response.send_message(
                "Sorry, you cannot spy on other staff members or professors with this command. :eyes:",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"{self.bot.loading_emoji} Searching for student information for `{member.display_name}`...",
            ephemeral=True,
        )

        course = await self.get_course()
        users = await self.bot.canvas.get_users(
            course,
            search_term=member.display_name,
            include=["avatar_url", "enrollments"],
        )
        for user in users:
            embed = await self.generate_embed(member, user)
            view = discord.ui.View()
            enrollments = user["enrollments"]
            if enrollments is not None:
                grades = enrollments[0].get("grades")
                if grades is not None:
                    grades_url = grades.get("html_url")
                    view.add_item(
                        discord.ui.Button(
                            url=grades_url,
                            label="Grades",
                            style=discord.ButtonStyle.link,
                        ),
                    )
            view.add_item(await self.generate_assignment_selector(user))
            await interaction.edit_original_response(
                content=f"Student information found for `{member.display_name}`.",
                embed=embed,
                view=view,
            )

    @commands.command()
    @commands.has_any_role("TA/PM", "Professor")
    async def studentcode(self, ctx: commands.Context):
        message = "We kindly request that you do not post code you have written in public channels to maintain academic integrity in the course. Sharing code publicly could inadvertently lead to plagiarism, which puts both you and your fellow students at risk.\n\nPlease only send very small portions of your developed code in public channels. If you need help with your specific solution, please visit office hours, where you can receive personal feedback from our amazing course staff!"

        # If in forums post, delete original forums post and ping the student in the
        # forum thread to let them know that posting code is not acceptable.
        if isinstance(ctx.channel, discord.Thread):
            starter_message = ctx.channel.starter_message
            if not starter_message:
                starter_message = await ctx.channel.fetch_message(ctx.channel.id)

            await starter_message.delete()
            await ctx.reply(f"{starter_message.author.mention}: {message}")

        # If in a reply, find original message, delete it, and send same message
        elif (
            ctx.message.type == discord.MessageType.reply
            and ctx.message.reference
            and ctx.message.reference.message_id
        ):
            starter_message = await ctx.channel.fetch_message(
                ctx.message.reference.message_id,
            )
            await starter_message.delete()
            await ctx.reply(f"{starter_message.author.mention}: {message}")

        else:
            await ctx.reply(
                "Please use this command in a reply to a student's message, or in a questions thread to delete the original message.",
                delete_after=10,
            )

    @commands.command()
    @commands.has_role("Admin")
    async def preplatepass(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Request Late Assignment Pass",
            color=discord.Color.brand_red(),
            description="Claim your late pass here to use in an assignment! Just remember, you only get one late pass per semester, so use it wisely.\n\nYour late pass will grant you the ability to submit an assignment up to 24 hours after it was originally due with no penalty.\n\nYou must claim your late pass for an upcoming assignment, you cannot rectroactively claim it for an assignment that is past due.",
        )
        embed.set_image(
            url="https://cdn.discordapp.com/attachments/1096504978086043660/1196923034158772324/image.png?ex=65b96471&is=65a6ef71&hm=3c808c022916747e3eeb7c033c6a70abab3c1c997a7e2c7fd969354e28ee63b6&",
        )
        await ctx.send(embed=embed, view=LatePassView(self.bot))


async def setup(bot: CoordinateBot):
    await bot.add_cog(StudentCog(bot))
