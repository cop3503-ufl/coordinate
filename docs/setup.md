There are several steps you will need to follow to setup Coordinate on your
own machine.

## Discord Bot

First, you will need to create a Discord bot. This bot is the user that your
students (or you) will interact with in the Discord interface. You will also
create a test server that you will use to interact with your bot in a controlled
environment.

1. Add a bot account in the [Discord Developers Portal](https://discord.com/developers/applications/).
   Create a new application and add a bot in the Bot section. There, get your bot token.
1. Create a new server in Discord. This will be your test server. You should use
   this server template, as it has all the necessary permissions for Coordinate to
   work: [Coordinate Server Template](https://discord.new/QU2VzzDwHhfM).

## Environment variables

You will want to create a `.env` file in the `src/` directory -- this is where
you will store all credentials and configuration variables related to your running
bot instance.

Here is an example of what your `.env` file should look like:

```env
# The token for your Discord bot (from the Discord Developer Portal)
DISCORD_TOKEN=
# You definitely want this on!
DEV_MODE=TRUE
# The server ID of the server you want to use for testing
GUILD_ID=
# The URL of your PostgreSQL database (this is likely what the URL will be if you are using Docker Compose)
POSTGRES_URL="postgresql+asyncpg://abc:def@localhost/mydb"
# The URL of your Canvas instance
CANVAS_URL=
# Your Canvas API token
CANVAS_API_TOKEN=
# The URL of your Qualtrics instance
QUALTRICS_URL=
# Your Qualtrics API token
QUALTRICS_API_TOKEN=
# The datacenter of your Qualtrics instance
QUALTRICS_API_DATACENTER=
# The ID of the survey you want to use for Coordinate
QUALTRICS_SURVEY_ID=
# The ID of the filter (from your survey) you want to use for Coordinate
QUALTRICS_FILTER_ID=
# (optional) Your client ID for Codio
CODIO_CLIENT_ID=
# (optional) Your client secret for Codio
CODIO_CLIENT_SECRET=
# (optional) Your GitHub token
TOKEN_FOR_GH=
# (optional) Your NVIDIA NGC token (for LLM model)
NVIDIA_NGC_TOKEN=
# (optional) The name of the Canvas folder to use for sourcing LLM embeddings
LLAMA_SOURCE_FOLDER=
# (optional) The email of your Gradescope account
GRADESCOPE_EMAIL=
# (optional) The password of your Gradescope account
GRADESCOPE_PASSWORD=
# (optional) The course ID of the course you want to use for the bot
GRADESCOPE_COURSE_ID=
# (optional) The course ID of the course you want to use for pytest
GRADESCOPE_TEST_COURSE_ID=
```
