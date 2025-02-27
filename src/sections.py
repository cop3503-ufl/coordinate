from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .canvas import User
from .utils import chunks
from .views import CoordinateBotView

if TYPE_CHECKING:
    from .bot import CoordinateBot

log = logging.getLogger(__name__)


class AssignSectionView(CoordinateBotView):
    def __init__(self, bot: CoordinateBot):
        super().__init__(timeout=None)
        self.bot = bot

    def embed(self, staff_mapping: dict | None = None) -> discord.Embed:
        embed = discord.Embed(
            title="Assigned Sections Preview",
            description="Please select a TA to assign sections.",
            color=discord.Color.blue(),
        )
        if staff_mapping:
            for ta, sections in staff_mapping.items():
                channels_text = "\n".join(
                    [
                        f"* {section}-{ta.replace(' ', '-').lower()}"
                        for section in sections
                    ],
                )
                embed.add_field(
                    name=ta,
                    value=channels_text or "No sections assigned",
                    inline=False,
                )
        return embed

    async def create_assignment_view(self):
        staff_mapping = {}
        course_info = self.bot.get_course_info()
        course = await self.bot.canvas.get_course(course_info.canvas_course_code)
        staff_list = await self.bot.canvas.get_users(
            course,
            "",
            enrollment_type=["ta", "teacher"],
        )
        sections = await self.bot.canvas.get_sections(course_info.canvas_course_code)
        section_list = [section["sis_section_id"][-5:] for section in sections[1:-1]]

        view = CoordinateBotView()
        staff_chunks = list(chunks(staff_list, 25))
        for chunk in staff_chunks:
            first_initial = chunk[0]["name"].split()[-1][0].upper()
            last_initial = chunk[-1]["name"].split()[-1][0].upper()
            view.add_item(
                TASelectionView(
                    self.bot,
                    chunk,
                    section_list,
                    f"{first_initial}-{last_initial}",
                    staff_mapping,
                ),
            )
        return view

    @discord.ui.button(
        label="Assign Sections",
        style=discord.ButtonStyle.green,
        custom_id="assign_sections:assign",
    )
    async def assign_sections(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer(ephemeral=True)
        view = await self.create_assignment_view()
        embed = self.embed()
        assert interaction.message is not None
        await interaction.followup.send(
            content="Please select a TA to assign sections.",
            view=view,
            embed=embed,
            ephemeral=True,
        )


class TASelectionView(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        staff_list: list[User],
        section_list: list[str],
        last_name_range: str,
        staff_dict: dict[str, list[str]],
    ):
        self.bot = bot
        self.staff_mapping = staff_dict
        self.section_list = section_list

        super().__init__(
            placeholder=f"Select a staff member... (last names {last_name_range})",
            min_values=1,
            max_values=1,
        )

        for user in staff_list:
            self.add_option(label=user["name"], value=str(user["name"]), emoji="🧑‍🏫")

    async def callback(self, interaction: discord.Interaction):
        staff_name = self.values[0]

        view = CoordinateBotView()

        staff_selection_disabled = self.view.children[0]  # type: ignore
        staff_selection_disabled.disabled = True
        staff_selection_disabled.placeholder = f"Selected TA: {staff_name}"
        staff_selection_disabled.options = self.options
        for option in staff_selection_disabled.options:
            option.default = option.value == staff_name

        view.add_item(staff_selection_disabled)
        view.add_item(SectionsSelect(self.bot, staff_name, self.section_list))

        await interaction.response.edit_message(
            content=f"Please select up to 3 sections for **{staff_name}**.",
            view=view,
        )


class SectionsSelect(discord.ui.Select):
    def __init__(
        self,
        bot: CoordinateBot,
        staff_name: str,
        section_list: list[str],
        staff_mapping: dict[str, list[str]] = {},
    ):
        self.bot = bot
        self.staff_name = staff_name
        self.section_list = section_list
        self.staff_mapping = staff_mapping
        super().__init__(
            placeholder="Select up to 3 sections",
            min_values=1,
            max_values=3,
        )

        assigned_sections = {sec for secs in staff_mapping.values() for sec in secs}
        for section in section_list:
            if section not in assigned_sections:
                self.add_option(label=section, value=section)

    async def callback(self, interaction: discord.Interaction):
        selected_sections = self.values
        self.staff_mapping[self.staff_name] = selected_sections

        async with self.bot.db_factory() as db:
            await db.add_staff_section(self.staff_name, selected_sections)

        embed = discord.Embed(
            title="Assigned Sections Preview",
            description="Here are the updated sections that have been assigned:",
            color=discord.Color.blue(),
        )

        for ta, sections in self.staff_mapping.items():
            channels_text = "\n".join(
                [f"* {section}-{ta.replace(' ', '-').lower()}" for section in sections],
            )
            embed.add_field(
                name=ta,
                value=channels_text or "No sections assigned",
                inline=False,
            )

        assigned_sections_text = ", ".join(
            [
                f"{section}-{self.staff_name.replace(' ', '-').lower()}"
                for section in selected_sections
            ],
        )
        message_content = f"**{self.staff_name}** has been assigned to section(s): {assigned_sections_text}."

        assign_section_view = AssignSectionView(self.bot)
        new_view = await assign_section_view.create_assignment_view()
        embed = assign_section_view.embed(self.staff_mapping)
        new_view.add_item(CreateChannelButton(self.bot, self.staff_mapping))
        self.disabled = False

        await interaction.response.edit_message(
            content=message_content,
            embed=embed,
            view=new_view,
        )


class CreateChannelButton(discord.ui.Button):
    def __init__(self, bot: CoordinateBot, staff_mapping: dict[str, list[str]] = {}):
        super().__init__(style=discord.ButtonStyle.green, label="Create Channels")
        self.bot = bot
        self.staff_mapping = staff_mapping
        self.section_color = discord.Color.from_str("#e4a6de")

    def new_section_embed(self, staff_name: str) -> discord.Embed:
        first_name = staff_name.split()[0]
        embed = discord.Embed(
            title="Welcome to your section channel!",
            color=self.section_color,
            description=f"""Welcome to your section channel! This is the section channel taught by **{staff_name}**.

            This is a place for you to collaborate with your classmates and your section leader. You can use this channel to ask questions, share resources, and work together on assignments.

            {first_name} will always share any important information through Canvas, too, so let this serve as a casual place to get in touch with your section leader and "section-mates". If you have any questions, feel free to ask {first_name} or another staff member. Have fun!
            """,
        )
        embed.set_image(
            url="https://media.discordapp.net/attachments/1091422466448040047/1202399131260162109/image.png?ex=6630d9b4&is=662f8834&hm=3ffd167f9965b9c20fbc7387e3bc418291e9f23f4b88ae78875085ab8fcde207&=&format=webp&quality=lossless&width=1884&height=1076",
        )
        return embed

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mapping = self.staff_mapping

        if not mapping:
            await interaction.followup.send(
                "No sections have been assigned yet.",
                ephemeral=True,
            )
            return

        if not interaction.guild:
            raise app_commands.NoPrivateMessage

        category_name = "Class Sections"
        category = discord.utils.get(
            interaction.guild.categories,
            name=category_name,
        ) or await interaction.guild.create_category(category_name)

        existing_channels = {chan.name.lower(): chan for chan in category.channels}
        for staff_member, sections in mapping.items():
            staff_snake_name = staff_member.replace(" ", "-").lower()
            for section in sections:
                channel_name = f"{section}-{staff_snake_name}"
                role_name = f"{section}: {staff_member}"

                if channel_name in existing_channels:
                    continue

                # Create section role
                role = discord.utils.get(
                    interaction.guild.roles,
                    name=role_name,
                )
                if not role:
                    role = await interaction.guild.create_role(
                        name=role_name,
                        color=self.section_color,
                    )

                # Create section channel
                overwrites: dict[
                    discord.Role | discord.Member | discord.Object,
                    discord.PermissionOverwrite,
                ] = {
                    interaction.guild.default_role: discord.PermissionOverwrite(
                        read_messages=False,
                    ),
                    self.bot.bot_role: discord.PermissionOverwrite(
                        read_messages=True,
                    ),
                    role: discord.PermissionOverwrite(read_messages=True),
                }
                channel = await interaction.guild.create_text_channel(
                    channel_name,
                    category=category,
                    overwrites=overwrites,
                )
                await channel.send(
                    role.mention,
                    embed=self.new_section_embed(staff_member),
                )

        await interaction.followup.send(
            "Channel creation process completed.",
            ephemeral=True,
        )
