"""A module containing the subclass of Discord.Client that implements the bot."""
import discord
import logging
from discord.ext import tasks

from youtube_player.player import Player
from youtube_player.helpers.download_manager import download_manager
from youtube_player.validation.config_validation import BotModel


class YoutubeBot(discord.Client):
    """A class that implements a YouTube player bot."""
    def __init__(self, config: BotModel, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._logger = logging.getLogger('discord')
        self.players = {}  # Dictionary of players for each discord guild

    async def on_ready(self) -> None:
        """Runs Discord client startup tasks."""
        self._logger.info(f'{self.user} as connected to Discord.')
        for guild in self.guilds:
            self._logger.info(f'Adding player for guild {guild.name} (id: {guild.id}).')
            self.players[guild.id] = Player(guild.id)

    @tasks.loop(minutes=60)
    async def custom_status_background(self) -> None:
        """Background task for custom status."""
        await self.wait_until_ready()
        await self.change_presence(
            activity=discord.Activity(name='!help for commands.', type=discord.ActivityType.listening))

    async def setup_hook(self) -> None:
        self.custom_status_background.start()
        download_manager.cleanup.start()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Adding a new player when we join a new guild."""
        self._logger.info(f'Adding player for guild {guild.name} (id: {guild.id}).')
        self.players[guild.id] = Player(guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Removing player when we are removed from a guild."""
        self._logger.info(f'Removing player for guild {guild.name} (id: {guild.id}).')
        self.players.pop(guild.id)

    async def on_message(self, message: discord.Message) -> None:
        """Processing messages for commands."""
        # Ignores any messages from the bot itself
        if message.author == self.user:
            return
        else:
            if '!play' in message.content:
                await self.players[message.guild.id].play(message)
            elif message.content == '!pause':
                await self.players[message.guild.id].pause(message)
            elif message.content == '!resume':
                await self.players[message.guild.id].resume(message)
            elif message.content == '!skip':
                await self.players[message.guild.id].skip(message)
            elif message.content == '!loop':
                await self.players[message.guild.id].loop(message)
            elif message.content == '!leave':
                await self.players[message.guild.id].leave(message)
            elif message.content == '!queue':
                await self.players[message.guild.id].display_queue(message)
            elif message.content == '!clear':
                await self.players[message.guild.id].clear(message)
            elif message.content == '!help':
                await self.players[message.guild.id].help(message)
