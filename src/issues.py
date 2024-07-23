from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .components import emoji_button

if TYPE_CHECKING:
    from .bot import CoordinateBot


class FileIssue(discord.ui.Modal):
    issue_title = discord.ui.TextInput(label="Title", placeholder="Issue Title")
    issue_body = discord.ui.TextInput(
        label="Body",
        placeholder="Issue Body",
        style=discord.TextStyle.long,
        min_length=75,
    )

    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(title="Thank you for your help!")

    async def on_submit(self, interaction: discord.Interaction):
        issue = await self.bot.github.create_issue(
            self.issue_title.value,
            self.issue_body.value
            + f"\n\n_Filed by: {interaction.user.display_name} (ID: `{interaction.user.id}`)_",
        )
        await interaction.response.send_message(
            f"Thank you for filing an issue! You can view it here: {issue['html_url']}.",
            ephemeral=True,
        )


class GitHubIssueView(discord.ui.View):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        super().__init__(timeout=None)

    @emoji_button(
        emoji="🗳️",
        label="File a Ticket",
        style=discord.ButtonStyle.gray,
        custom_id="gissue:file",
    )
    async def file(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FileIssue(self.bot))
