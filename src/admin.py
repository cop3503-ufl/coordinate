from __future__ import annotations

import io
import textwrap
import time
import traceback
from collections.abc import Iterable, Sequence
from contextlib import redirect_stdout, suppress
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from sqlalchemy import Row, text

from .bot import AssignSectionView, RegistrationView

if TYPE_CHECKING:
    from .bot import CoordinateBot


# Code for lots of this file from: Rapptz/RoboDanny
class plural:
    def __init__(self, value: int):
        self.value: int = value

    def __format__(self, format_spec: str) -> str:
        v = self.value
        singular, sep, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"


def human_join(seq: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    size = len(seq)
    if size == 0:
        return ""

    if size == 1:
        return seq[0]

    if size == 2:
        return f"{seq[0]} {final} {seq[1]}"

    return delim.join(seq[:-1]) + f" {final} {seq[-1]}"


class TabularData:
    def __init__(self):
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]):
        self._columns = columns
        self._widths = [len(c) + 2 for c in columns]

    def add_row(self, row: Iterable[Any]) -> None:
        rows = [str(r) for r in row]
        self._rows.append(rows)
        for index, element in enumerate(rows):
            width = len(element) + 2
            if width > self._widths[index]:
                self._widths[index] = width

    def add_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.add_row(row)

    def render(self) -> str:
        """Renders a table in rST format.

        Example:

        +-------+-----+
        | Name  | Age |
        +-------+-----+
        | Alice | 24  |
        |  Bob  | 19  |
        +-------+-----+
        """

        sep = "+".join("-" * w for w in self._widths)
        sep = f"+{sep}+"

        to_draw = [sep]

        def get_entry(d):
            elem = "|".join(f"{e:^{self._widths[i]}}" for i, e in enumerate(d))
            return f"|{elem}|"

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_entry(row))

        to_draw.append(sep)
        return "\n".join(to_draw)


class Admin(commands.Cog):
    def __init__(self, bot: CoordinateBot):
        self.bot = bot

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])

        # remove `foo`
        return content.strip("` \n")

    @commands.command(hidden=True, name="eval")
    @commands.is_owner()
    async def _eval(self, ctx: commands.Context, *, body: str):
        """Evaluates a code"""

        env = {
            "bot": self.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f"```py\n{e.__class__.__name__}: {e}\n```")

        func = env["func"]
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception:
            value = stdout.getvalue()
            await ctx.send(f"```py\n{value}{traceback.format_exc()}\n```")
        else:
            value = stdout.getvalue()
            with suppress(Exception):
                await ctx.message.add_reaction("\u2705")

            if ret is None:
                if value:
                    await ctx.send(f"```py\n{value}\n```")
            else:
                await ctx.send(f"```py\n{value}{ret}\n```")

    @commands.group(hidden=True, invoke_without_command=True)
    @commands.has_role("Admin")
    async def sql(self, ctx: commands.Context, *, query: str):
        """Run some SQL."""
        # the imports are here because I imagine some people would want to use
        # this cog as a base for their other cog, and since this one is kinda
        # odd and unnecessary for most people, I will make it easy to remove
        # for those people.
        query = self.cleanup_code(query)

        try:
            start = time.perf_counter()
            async with self.bot.db_factory() as db:
                results = await db.execute(text(query))
            dt = (time.perf_counter() - start) * 1000.0
        except Exception as e:
            return await ctx.send(f"```py\n{e}\n```")

        vals = results.all()
        rows = len(vals)
        if isinstance(results, str) or rows == 0:
            return await ctx.send(f"`{dt:.2f}ms: {results}`")

        headers = list(results.keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(vals)
        render = table.render()

        fmt = f"```\n{render}\n```\n*Returned {plural(rows):row} in {dt:.2f}ms*"
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(fp, "results.txt"))
        else:
            await ctx.send(fmt)

    async def send_sql_results(
        self,
        ctx: commands.Context,
        records: Sequence[Row[Any]],
    ):
        headers = list(records[0]._mapping.keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(records)
        render = table.render()

        fmt = f"```\n{render}\n```"
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode("utf-8"))
            await ctx.send("Too many results...", file=discord.File(fp, "results.txt"))
        else:
            await ctx.send(fmt)

    @sql.command(name="update", hidden=True)
    @commands.has_role("Admin")
    async def sql_update(self, ctx: commands.Context, *, query: str):
        """Run an update query."""
        query = self.cleanup_code(query)

        try:
            async with self.bot.db_factory() as db:
                await db.execute(text(query))
        except Exception as e:
            return await ctx.send(f"```py\n{e}\n```")

        await ctx.send("Query executed successfully.")

    @sql.command(name="schema", hidden=True)
    @commands.has_role("Admin")
    async def sql_schema(self, ctx: commands.Context, *, table_name: str):
        """Runs a query describing the table schema."""
        query = """SELECT column_name, data_type, column_default, is_nullable
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE table_name = :table_name
                """

        async with self.bot.db_factory() as db:
            results = await db.execute(text(query), {"table_name": table_name})
        records = results.all()

        if len(records) == 0:
            await ctx.send("Could not find a table with that name")
            return

        await self.send_sql_results(ctx, records)

    @sql.command(name="tables", hidden=True)
    @commands.has_role("Admin")
    async def sql_tables(self, ctx: commands.Context):
        """Lists all SQL tables in the database."""

        query = """SELECT table_name
                   FROM information_schema.tables
                   WHERE table_schema='public' AND table_type='BASE TABLE'
                """

        async with self.bot.db_factory() as db:
            results = await db.execute(text(query))

        records = results.all()
        if len(records) == 0:
            await ctx.send("Could not find any tables")
            return

        await self.send_sql_results(ctx, records)

    @sql.command(name="sizes", hidden=True)
    @commands.has_role("Admin")
    async def sql_sizes(self, ctx: commands.Context):
        """Display how much space the database is taking up."""

        # Credit: https://wiki.postgresql.org/wiki/Disk_Usage
        query = """
            SELECT nspname || '.' || relname AS "relation",
                pg_size_pretty(pg_relation_size(C.oid)) AS "size"
              FROM pg_class C
              LEFT JOIN pg_namespace N ON (N.oid = C.relnamespace)
              WHERE nspname NOT IN ('pg_catalog', 'information_schema')
              ORDER BY pg_relation_size(C.oid) DESC
              LIMIT 20;
        """

        async with self.bot.db_factory() as db:
            results = await db.execute(text(query))

        records = results.all()
        if len(records) == 0:
            await ctx.send("Could not find any tables")
            return

        await self.send_sql_results(ctx, records)

    @commands.command()
    @commands.is_owner()
    async def reply(self, ctx: commands.Context, message: discord.Message, text: str):
        msg = await message.reply(text)
        await ctx.reply(f"Done, posted: {msg.jump_url}!")

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context):
        message = await ctx.reply(f"{self.bot.loading_emoji} Syncing...")
        await self.bot.tree.sync()
        await message.edit(content="✅ Done!")

    @commands.command()
    @commands.is_owner()
    async def send(self, ctx: commands.Context, user: discord.User, message: str):
        try:
            await user.send(message)
            await ctx.reply(f"Done, sent to {user}!")
        except discord.DiscordException:
            await ctx.reply(f"Could not send message to {user}!")

    @commands.command()
    @commands.is_owner()
    async def react(
        self,
        ctx: commands.Context,
        message: discord.Message,
        emoji: discord.PartialEmoji | str,
    ):
        if message.reactions:
            emoji_reaction = [r for r in message.reactions if r.emoji == emoji and r.me]
            if emoji_reaction and self.bot.user:
                await message.remove_reaction(emoji, self.bot.user)
                return await ctx.reply(f"Removed the {emoji} reaction!")
        await message.add_reaction(emoji)
        return await ctx.reply(f"Added the {emoji} reaction!")

    @commands.command()
    @commands.is_owner()
    async def prepare(
        self,
        ctx: commands.Context,
        course_code: str,
        official: str,
        professor: str,
    ):
        try:
            embed = discord.Embed(
                title=f"Welcome to {course_code}!",
                color=discord.Color.gold(),
                description=f"Welcome to **{course_code}: {official}** with Professor {professor} for the upcoming semester!\n\nYou can use this Discord server to ask questions about the class, attend office hours hosted by peer mentors, and get help with assignments. The first step however, is to ensure that you are a current member of the course.\n\n**To gain access to the server, please click the button below to verify your course registration.**",
            )
            await ctx.send(view=RegistrationView(ctx.bot), embed=embed)
        except Exception:
            traceback.print_exc()

    @commands.command()
    @commands.is_owner()
    async def prepareagain(self, ctx: commands.Context[CoordinateBot], course_code: str):
        try:
            embed = discord.Embed(
                title=f"Retaking {course_code}?",
                color=discord.Color.brand_green(),
                description=f"If you are retaking {course_code}, and would like access to the active channels for this semester, please use the button below to verify your enrollment in the current course.\n\nAll alumni of the course are welcome to talk in {ctx.bot.random_ch.mention}.",
            )
            await ctx.send(view=RegistrationView(ctx.bot), embed=embed)
            await ctx.message.delete()
        except Exception:
            traceback.print_exc()

    @commands.command()
    @commands.has_role("Admin")
    async def prepsections(self, ctx: commands.Context[CoordinateBot]):
        try:
            embed = discord.Embed(
                title="Manage Course Sections",
                description="""Use the button below to organize and manage section channels and roles. These channels provide a dedicated space for each section to collaborate, ask questions, and share resources.

                You should generally run this at the start of the semester, before anyone joins the server so that they can be added to their section channel upon entry. If you need to run it again, you can do so at any time to update the sections.
                """,
                color=discord.Color.gold(),
            )
            await ctx.send(view=AssignSectionView(ctx.bot), embed=embed)
        except Exception:
            traceback.print_exc()


async def setup(bot: CoordinateBot):
    await bot.add_cog(Admin(bot))
