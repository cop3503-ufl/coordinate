from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import logging
import re
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.db import LlamaResponse

from .env import LLAMA_SOURCE_FOLDER
from .llama import LlamaMessage, LlamaModel, LlamaRequestContext
from .views import Confirm, CoordinateBotModal, CoordinateBotView

if TYPE_CHECKING:
    from .bot import CoordinateBot
    from .db import DocumentEmbedding


logger = logging.getLogger(__name__)


class Parser:
    def parse_markdown(self, markdown: str) -> list[str]:
        from langchain.text_splitter import MarkdownHeaderTextSplitter

        headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
        ]

        markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
        )
        md_header_splits = markdown_splitter.split_text(markdown)
        return [d.page_content for d in md_header_splits]

    def ensure_length(self, docs: list[str]) -> list[str]:
        """
        For any documents > 2048 tokens, split into smaller documents.
        """
        final_docs = []
        for doc in docs:
            # Check if the document needs to be split.
            if len(doc) > 2048:
                # Split the document into chunks of up to 2048 tokens.
                for i in range(0, len(doc), 2048):
                    final_docs.append(doc[i : i + 2048])
            else:
                # If no split is needed, just add the original document.
                final_docs.append(doc)
        return final_docs


class SourcedDocumentsButton(discord.ui.Button):
    def __init__(
        self,
        bot: CoordinateBot,
        similar: list[tuple[DocumentEmbedding, float]],
    ):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Sourced Documents",
            row=2,
        )
        self.bot = bot
        self.similar = similar

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        content = ""
        max_length_per_doc = int(2000 / (len(self.similar) + 1))
        for doc, score in self.similar:
            text = re.sub(r"\n", "\n", doc.text[:max_length_per_doc])
            if len(text) < len(doc.text):
                text += "..."
            content += (
                f"From **{doc.source}** (dist: `{score:.3f}`):\n```md\n{text}```\n"
            )
        await interaction.response.send_message(content.strip()[:2000], ephemeral=True)


class ReasonForRejectionDropdown(discord.ui.Select):
    def __init__(
        self,
        options: list[discord.SelectOption],
        bot: CoordinateBot,
        original_interaction: discord.Interaction,
        llama_response: LlamaResponse,
    ):
        self.llama_response = llama_response

        super().__init__(
            placeholder="Why was this response not helpful?",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot
        self.original_interaction = original_interaction

    async def callback(self, interaction: discord.Interaction):
        reason = self.values[0]
        if reason == "Other":
            modal = OtherReason(self.bot, self.llama_response)
            await interaction.response.send_modal(modal)
            return reason
        async with self.bot.db_factory() as db:
            self.llama_response.reason = reason
            db.add(self.llama_response)
            await db.commit()

        await interaction.response.send_message(
            "Thank you for your feedback! We will use it to improve our system. The response has been recorded.",
            ephemeral=True,
        )
        await self.original_interaction.delete_original_response()
        return reason


class ResponseDeclineSelectView(CoordinateBotView):

    message: discord.Message

    def __init__(
        self,
        bot: CoordinateBot,
        options: list[discord.SelectOption],
        original_interaction: discord.Interaction,
        llama: LlamaResponse,
    ):
        self.bot = bot
        self.llama_response = llama
        dropdown = ReasonForRejectionDropdown(
            options=options,
            bot=self.bot,
            original_interaction=original_interaction,
            llama_response=llama,
        )
        super().__init__()
        self.add_item(dropdown)

    async def on_timeout(self) -> None:
        async with self.bot.db_factory() as db:
            self.llama_response.reason = "Timed out"
            db.add(self.llama_response)
            await db.commit()
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True  # type: ignore
                child.placeholder = "Timed out..."
        await self.message.edit(view=self)


class OtherReason(CoordinateBotModal):
    reason = discord.ui.TextInput(
        label="Reason for Rejection",
        placeholder="Enter your reason here...",
        style=discord.TextStyle.long,
        min_length=1,
        max_length=100,
        required=True,
    )

    def __init__(self, bot: CoordinateBot, llama_response: LlamaResponse):
        self.bot = bot
        self.llama_response = llama_response
        super().__init__(title="Other Reason")

    async def on_submit(self, interaction: discord.Interaction):
        async with self.bot.db_factory() as db:
            self.llama_response.reason = self.reason.value
            db.add(self.llama_response)
            await db.commit()


class GPT(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot
        self.check_context.start()
        self.parser = Parser()
        self.thread_dict: dict[int, set[int]] = {}

    @tasks.loop(minutes=30, reconnect=False)
    async def check_context(self) -> None:
        """
        Ensure that the list of source documents has not changed.
        """
        await self.bot.wait_until_ready()
        if not LLAMA_SOURCE_FOLDER:
            return  # LLAMA not enabled
        logger.info("Checking for new source documents...")
        folders = await self.bot.canvas.resolve_path(LLAMA_SOURCE_FOLDER)
        actual_folder = folders[-1]
        files = await self.bot.canvas.get_files_in_folder(actual_folder["id"])
        for file in files:
            async with self.bot.db_factory() as db:
                time_added = await db.get_time_added(file["display_name"])
            updated_at = datetime.datetime.fromisoformat(file["updated_at"])
            if time_added and (updated_at < time_added):
                continue  # File has been updated
            logger.info(f"Generating embeddings for {file['display_name']}...")
            content = await self.bot.canvas.get_file_content(file["url"])
            docs = self.parser.parse_markdown(content)
            docs = self.parser.ensure_length(docs)
            for doc in docs:
                embedding = await self.bot.llama.generate_embeddings(doc)
                async with self.bot.db_factory() as db:
                    await db.add_embedding(doc, file["display_name"], embedding)

    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command(
        name="summon",
        description="Send student question into LLaMA. Generate response and allow caster to Accept or Reject output.",
    )
    @app_commands.describe(
        model="The model to choose.",
        extra_context="Optional: Provide additional context.",
    )
    async def summon(
        self,
        interaction: discord.Interaction,
        model: Literal["llama2_70b", "codellama_13b", "mixtral_8x7b"],
        extra_context: str | None = None,
    ):
        if isinstance(
            interaction.channel,
            discord.ForumChannel | discord.CategoryChannel,
        ):
            await interaction.response.send_message(
                "This command cannot be used in a forum.",
                ephemeral=True,
            )
            return

        time_now = discord.utils.utcnow()
        loading_message = f"{self.bot.loading_emoji} Finding relevant context documents and embedding query... (started {discord.utils.format_dt(time_now, style='R')})"
        await interaction.response.send_message(
            loading_message,
            ephemeral=True,
        )

        thread = None
        prev_messages: list[LlamaMessage] = []
        if isinstance(interaction.channel, discord.Thread):
            thread = interaction.channel
            async for message in interaction.channel.history(
                oldest_first=True,
            ):
                if message.is_system():
                    continue
                prev_messages.append(LlamaMessage.from_message(message))

            if len(prev_messages) == 0:
                await interaction.response.send_message(
                    "There are no messages in this thread.",
                )

        message_content = "\n".join([m.content for m in prev_messages])
        if extra_context:
            message_content += "\n" + extra_context
        embed = await self.bot.llama.generate_embeddings("\n".join(message_content))

        async with self.bot.db_factory() as db:
            similar = list(await db.find_similar_documents(embed, 10))
        similar_docs, similar_scores = zip(*similar)

        current_response = None
        updated_event = asyncio.Event()

        def update_text_count_cb(updated_text: str) -> None:
            nonlocal current_response
            current_response = updated_text
            updated_event.set()

        msg = await interaction.original_response()

        async def update_message_loop():
            with contextlib.suppress(asyncio.CancelledError):
                while True:
                    updated_event.clear()
                    if shortened_text := current_response:
                        if len(current_response) > 2000:
                            shortened_text = current_response[: 1900 - 3] + "..."
                        await msg.edit(content=shortened_text)
                    await updated_event.wait()

        context = LlamaRequestContext(
            similar_docs,
            prev_messages,
            update_text_count_cb,
            thread,
        )
        response_task_id = f"llama_response_{interaction.id}"
        update_msg_task_id = f"update_message_{interaction.id}"
        response_task = await self.bot.tasks.create_task_and_wait(
            self.bot.llama.get_response(
                context,
                LlamaModel(model),
            ),
            name=response_task_id,
        )
        update_msg_task = await self.bot.tasks.create_task_and_wait(
            update_message_loop(),
            name=update_msg_task_id,
        )

        aws: list[asyncio.Task] = [
            response_task,
            update_msg_task,
        ]
        _, pending = await asyncio.wait(aws, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        response = response_task.result()

        info_components: list[discord.ui.Item] = [
            SourcedDocumentsButton(self.bot, similar),
        ]
        confirm = Confirm(interaction.user, additional_components=info_components)

        if len(response) > 2000:
            # Send in file instead
            with io.BytesIO(response.content.encode()) as file:
                confirm_msg = await interaction.edit_original_response(
                    content="The content of the file will be sent in a message.",
                    attachments=[discord.File(file, filename="response.txt")],
                    view=confirm,
                )
                confirm.message = confirm_msg
        else:
            confirm_msg = await msg.edit(
                content=response.content,
                view=confirm,
            )
            confirm.message = confirm_msg

        await confirm.wait()
        if confirm.value is None:
            # Timeout occurred.
            return

        if not interaction.channel:
            return

        llama = LlamaResponse(
            date=datetime.datetime.now().astimezone(),
            id=None,
            channel_id=interaction.channel.id,
            staff_id=interaction.user.id,
            prompt=prev_messages[0].content,
            response=response.content,
            accepted=bool(confirm.value),
            reason=None,
        )

        if confirm.value is True and interaction.channel:
            if len(response) > 2000:
                # Split up into separate messages by paragraphs
                paragraphs = response.content.split("\n\n")
                first_message = ""
                while paragraphs and len(first_message + paragraphs[0]) < 2000:
                    first_message += paragraphs.pop(0) + "\n\n"
                sent_message = await interaction.channel.send(first_message.strip())
                second_message = "\n\n".join(paragraphs)
                if second_message:
                    await interaction.channel.send(second_message.strip())
            else:
                sent_message = await interaction.channel.send(response.content)
            await interaction.delete_original_response()

            if interaction.channel and interaction.user:
                async with self.bot.db_factory() as db:
                    llama.id = sent_message.id
                    db.add(llama)
                    await db.commit()
        else:
            options = [
                discord.SelectOption(
                    label="Contains false/incorrect information",
                    emoji="❌",
                ),
                discord.SelectOption(
                    label="Not relevant to the question",
                    emoji="🔀",
                ),
                discord.SelectOption(
                    label="Gave too much code",
                    emoji="💾",
                ),
                discord.SelectOption(
                    label="Referencing non-existent code",
                    emoji="👻",
                ),
                discord.SelectOption(
                    label="Other",
                    emoji="❓",
                ),
            ]
            view = ResponseDeclineSelectView(self.bot, options, interaction, llama)
            decline_msg = await interaction.edit_original_response(
                view=view,
            )
            view.message = decline_msg
            self.thread_dict.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        thread = message.channel
        if not isinstance(
            thread,
            discord.Thread,
        ):
            return

        async with self.bot.db_factory() as db:
            llama_response = await db.get_llama_response_for_thread(thread)
            if not llama_response or not llama_response.id:
                return

            response = await db.get_llama_response(llama_response.id)
            staff_id = response.staff_id if response else None
            if not staff_id:
                return
            replier = message.author
            guild = self.bot.active_guild
            staff_member = await self.bot.get_member(staff_id)
            if staff_member.id not in self.thread_dict:
                self.thread_dict[staff_id] = set()
            else:
                return

            member = await guild.fetch_member(replier.id)
            if not member:
                return

            if self.bot.student_role in member.roles and staff_member:
                view = CoordinateBotView()
                view.add_item(
                    discord.ui.Button(
                        label="Jump to Thread",
                        url=message.jump_url,
                        style=discord.ButtonStyle.link,
                    ),
                )
                recent_messages: list[str] = []
                async for msg in message.channel.history(limit=3):
                    recent_messages.append(
                        f"**{msg.author.display_name}**: {msg.clean_content[:50]}{'...' if len(msg.clean_content) > 50 else ''}",
                    )

                recent_messages.reverse()
                thread_name = (
                    message.channel.name
                    if isinstance(message.channel, discord.Thread)
                    else "Unknown"
                )
                embed = discord.Embed(
                    title="🛎️ Someone replied to your LLaMA response!",
                    description=f"**{replier.mention}** has replied to your message in: \n**{thread_name}**.\n\n**Reply:** {message.clean_content}\n\nRecent messages:\n"
                    + "\n".join(recent_messages),
                    color=discord.Color.orange(),
                )
                if replier.display_avatar:
                    embed.set_thumbnail(url=replier.display_avatar.url)
                await staff_member.send(embed=embed, view=view)


async def setup(bot: CoordinateBot):
    await bot.add_cog(GPT(bot))
