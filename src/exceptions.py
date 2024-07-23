from __future__ import annotations

import contextlib
import datetime
import logging
import sys
import traceback
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from gradescope_api.errors import GradescopeAPIError
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .bot import CoordinateBot
    from .db import StaffMember


class CoordinateException(Exception):
    """
    Base class for all exceptions handled by the system.
    """


class SessionNotFound(CoordinateException):
    """
    A lookup for an office hours session was attempted, but no matching document was found.
    """

    def __init__(self):
        super().__init__("No session found.")


class ButtonOnCooldown(commands.CommandError):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


class StaffMemberNotFound(CoordinateException):
    """
    A lookup for a staff member was attempted, but no matching document was found.
    """

    def __init__(self, name: str | None, discord_uid: int | None):
        self.name = name
        self.discord_uid = discord_uid
        super().__init__(
            f"No staff member found with name {name} and discord_uid {discord_uid}.",
        )


class OfficeHoursRequestNotFound(CoordinateException):
    """
    A lookup for an office hours request was attempted, but no matching document was found.
    """

    def __init__(self, message_id: int):
        self.message_id = message_id
        super().__init__(f"No office hours request found for {message_id}.")


class StudentsOnly(CoordinateException):
    """
    An operation was attempted that requires the user to be a student, but they are not.
    """


class NoFutureTimeslots(CoordinateException):
    """
    Indicates that no future timeslots occur for a certain staff member.
    """

    def __init__(self, staff_member: StaffMember):
        self.staff_member = staff_member


class NvidiaNGCException(CoordinateException):
    """
    An exception occurred while interacting with the Nvidia NGC API.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class FormValidationError(CoordinateException):
    """
    An exception occurred when attempting to validate an input from a form/modal.
    """

    def __init__(
        self,
        attempted: str,
        into: type,
        validation_message: str | None = None,
    ):
        self.attempted = attempted
        self.into = into
        self.validation_message = validation_message
        val_formatted = (
            f" Specific reason: {validation_message}" if validation_message else ""
        )
        message = (
            f"Attempted to convert {attempted} into {into}, but failed.{val_formatted}"
        )
        super().__init__(message)


class CoordinateBotErrorHandler:
    """
    General error handler for the bot. Handles command errors, interaction errors,
    and errors with events. Can be instantitated infinite times, although using
    CoordinateBotView and CoordinateBotModal will take care of the error handling
    for most interactions.
    """

    no_logs_needed = (
        app_commands.MissingAnyRole,
        app_commands.MissingRole,
        StudentsOnly,
    )

    def discord_logging_desired(self, error: BaseException) -> bool:
        return error.__class__ not in self.no_logs_needed

    def error_message(self, error: BaseException) -> tuple[str, float | None]:
        """
        Returns the error message and the delay, if any.
        """
        delay = None

        # Handle our failures first
        if isinstance(error, StaffMemberNotFound):
            return (
                f"Sorry, I tried looking up a staff member with the name ({error.name}) and/or ID ({error.discord_uid}) you provided, but was unable to find a matching database record.",
                delay,
            )
        elif isinstance(error, FormValidationError):
            humanized_types = {
                datetime.datetime: "a date and time",
                datetime.date: "a date",
                datetime.time: "a time",
            }
            val_humanized = (
                f"\n\nSpecific error message is:\n> {error.validation_message}"
            )
            humanized = f"Attempted to convert your response (`{error.attempted}`) into {humanized_types[error.into]}, but was unable to do.{val_humanized}"
            return (
                humanized,
                delay,
            )
        elif isinstance(
            error,
            app_commands.CommandInvokeError | commands.CommandInvokeError,
        ):
            return (
                f"This command experienced a general error of type `{error.original.__class__}`.",
                delay,
            )
        elif isinstance(
            error,
            app_commands.CommandOnCooldown | commands.CommandOnCooldown,
        ):
            next_time = discord.utils.utcnow() + datetime.timedelta(
                seconds=error.retry_after,
            )
            message = (
                "Time to _chill out_ - this command is on cooldown! "
                f"Please try again **{discord.utils.format_dt(next_time, 'R')}.**"
                "\n\n"
                "For future reference, this command is currently limited to "
                f"being executed **{error.cooldown.rate} times every {error.cooldown.per} seconds**."
            )
            delay = error.retry_after
            return message, delay
        elif isinstance(
            error,
            app_commands.MissingRole
            | app_commands.MissingAnyRole
            | commands.MissingRole
            | commands.MissingAnyRole,
        ):
            return str(error), delay
        elif isinstance(error, NoFutureTimeslots):
            return (
                f"Could not any future timeslots for **{error.staff_member.name}**.",
                delay,
            )
        elif isinstance(error, SQLAlchemyError):
            return (
                "An SQLAlchemy error occurred while trying to interact with the database. This isn't good! If you could take a screenshot and send it to a developer, that would be amazing.",
                delay,
            )

        error_messages: dict[type[BaseException], str] = {
            # Custom messages
            GradescopeAPIError: "An exception occurred while interacting with the Gradescope API.",
            StudentsOnly: "Sorry friend, this feature is only available for students, and it looks like you aren't one.",
            NvidiaNGCException: "An exception occurred while interacting with the Nvidia NGC API. The resource could not be accessed.",
            # Application commands or Interactions
            app_commands.NoPrivateMessage: "Sorry, but this command does not work in private message. Please hop on over to the server to use the command!",
            app_commands.MissingPermissions: "Hey pal, you don't have the necessary permissions to run this command.",
            app_commands.BotMissingPermissions: "Hmm, looks like I don't have the permissions to do that. Something went wrong. You should definitely let someone know about this.",
            app_commands.CommandLimitReached: "Oh no! I've reached my max command limit. Please contact a developer.",
            app_commands.TransformerError: "This command experienced a transformer error.",
            app_commands.CommandAlreadyRegistered: "This command was already registered.",
            app_commands.CommandSignatureMismatch: "This command is currently out of sync.",
            app_commands.CheckFailure: "A check failed indicating you are not allowed to perform this action at this time.",
            app_commands.CommandNotFound: "This command could not be found.",
            app_commands.MissingApplicationID: "This application needs an application ID.",
            commands.NotOwner: "This command is only available to the owner.",
            commands.MissingRequiredArgument: "This command is missing a required argument.",
            commands.BadArgument: "This command received a bad argument.",
            commands.TooManyArguments: "This command received too many arguments.",
            commands.UserInputError: "This command received a bad input.",
            discord.InteractionResponded: "An exception occurred because I tried responding to an already-completed user interaction.",
            # General
            discord.LoginFailure: "Failed to log in.",
            discord.Forbidden: "An exception occurred because I tried completing an operation that I don't have permission to do.",
            discord.NotFound: "An exception occurred because I tried completing an operation that doesn't exist.",
            discord.DiscordServerError: "An exception occurred because of faulty communication with the Discord API server.",
        }
        return (
            error_messages.get(
                error.__class__,
                f"Ups, an unhandled error occurred: `{error.__class__}`.",
            ),
            delay,
        )

    async def handle_event_exception(
        self,
        event: str,
        client: CoordinateBot,
    ):
        e_type, error, tb = sys.exc_info()
        if error:
            logger.exception(f"{e_type}: {error} occurred in `{event}` event.")
            exc_format = "".join(traceback.format_exception(e_type, error, tb, None))
            if self.discord_logging_desired(error):
                await client.bot_log_ch.send(
                    f"**{error.__class__.__name__}** occurred in a `{event}` event:\n"
                    f"```py\n{exc_format[:3900]}\n```",
                )

    async def handle_command_exception(
        self,
        ctx: commands.Context,
        error: Exception,
    ):
        message, _ = self.error_message(error)
        logger.exception(f"{error.__class__.__name__}: {error} occurred.")
        if isinstance(error, commands.CommandInvokeError):
            error = error.original
        try:
            raise error
        except Exception:
            if self.discord_logging_desired(error):
                await ctx.bot.bot_log_ch.send(
                    f"**{error.__class__.__name__}** occurred in a command:\n"
                    f"```py\n{traceback.format_exc()[:3900]}\n```",
                )
                await ctx.reply(message)

    async def handle_interaction_exception(
        self,
        interaction: discord.Interaction[CoordinateBot],
        error: Exception,
    ) -> None:
        # For commands on cooldown, delete message after delay
        message, delay = self.error_message(error)

        if interaction.response.is_done() and interaction.response.type not in (
            discord.InteractionResponseType.deferred_message_update,
            discord.InteractionResponseType.deferred_channel_message,
        ):
            msg = await interaction.edit_original_response(
                content=message,
                view=None,
                embed=None,
            )
        else:
            await interaction.response.defer(ephemeral=True)
            msg = await interaction.followup.send(message, ephemeral=True, wait=True)

        if delay is not None:
            await msg.delete(delay=delay)

        if not self.discord_logging_desired(error):
            return

        logger.exception(f"{error.__class__.__name__}: {error} occurred.")

        channel_name = None
        if interaction.channel:
            if isinstance(interaction.channel, discord.DMChannel):
                channel_name = f"DM with {interaction.channel.recipient}"
            elif isinstance(interaction.channel, discord.GroupChannel):
                channel_name = f"DM with {interaction.channel.recipients}"
            else:
                channel_name = interaction.channel.mention

        # Attempt to log to channel, but only log errors not from our code
        if error.__class__.__module__ != __name__:
            with contextlib.suppress():
                await interaction.client.bot_log_ch.send(
                    f"**{error.__class__.__name__}** occurred in {channel_name} interaction by {interaction.user.mention}:\n"
                    f"```py\n{traceback.format_exc()[:3900]}```",
                )
