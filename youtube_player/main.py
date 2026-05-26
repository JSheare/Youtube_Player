"""A module containing the top level main function of the bot."""
import discord
import os
import sys

# Adds parent directory to sys.path. Necessary to make the imports below work when running this file as a script
if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import youtube_player.config.parameters as params
from youtube_player.youtube_bot import YoutubeBot
from youtube_player.startup.configure_logging import get_log_handler
from youtube_player.startup.read_config import read_config
from youtube_player.validation.config_validation import BotModel


def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    config = BotModel(**dict(read_config().items(params.APP_NAME)))
    log_handler = get_log_handler()
    bot = YoutubeBot(config, intents=intents)
    bot.run(config.discord_token, log_handler=log_handler, log_level=config.log_level)


if __name__ == '__main__':
    main()