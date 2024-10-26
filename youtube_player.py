import asyncio
import discord
import logging
import os
from async_timeout import timeout
from dotenv import load_dotenv
from yt_dlp import YoutubeDL


def is_valid_url(url):
    return (len(url) >= 23 and url[:23] == 'https://www.youtube.com') or (
        len(url) >= 16 and url[:16] == 'https://youtu.be') or (
            len(url) >= 19 and url[:19] == 'https://youtube.com')


def is_valid_attachment(filename):
    valid_extensions = ['.mp3', '.mp4', '.wav', '.webm', '.mov']
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    return filename[i:] in valid_extensions


def strip_extension(filename):
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    return filename[:i]


# A class that manages player activities for each discord guild
class Player:
    def __init__(self, loop, ydl, guild_id):
        self.loop = loop
        self.ydl = ydl
        self.guild_id = guild_id
        self.status_message = None
        self.voice_client = None
        self.queue = asyncio.Queue()
        self.recycling = asyncio.Queue()
        self.next = asyncio.Event()
        self.enqueueing = asyncio.Lock()

    # Deletes the old player status message before sending the new one
    async def replace_status_message(self, user_message, string):
        if self.status_message is not None:
            try:
                await self.status_message.delete()
            except discord.errors.NotFound:
                pass

        self.status_message = await user_message.channel.send(string)

    # Checks to see if the message sender is in a voice channel, and optionally sends a warning if they aren't
    @staticmethod
    async def sender_in_voice(user_message, send_warning=False):
        if user_message.author.voice:
            return True
        else:
            if send_warning:
                await user_message.channel.send('**Connect to a voice channel to use commands.**')

            return False

    # Cleanup tasks when the player reaches the end of the queue or the user uses the leave command
    async def cleanup(self):
        # Leave voice
        if self.voice_client is not None:
            await self.voice_client.disconnect()
            self.voice_client = None

        # Cleanup files
        while not self.recycling.empty():
            os.remove(self.recycling.get_nowait())

    # Clears the queue
    def clear_queue(self):
        if not self.queue.empty():
            while not self.queue.empty():
                os.remove(self.queue.get_nowait())

    async def clear(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            if not self.queue.empty():
                await user_message.channel.send('**Emptying queue...**')
                self.clear_queue()
            else:
                await user_message.channel.send('**Queue already empty.**')

    # Displays the current queue
    async def display_queue(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            if not self.queue.empty():
                max_tracks_displayed = 10
                queue_message = '**__Currently in queue:__\n'
                original_size = self.queue.qsize()
                i = 0
                while i < original_size:
                    try:
                        file = self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    if i < max_tracks_displayed:
                        queue_message += f'*{strip_extension(file)}*\n'

                    await self.queue.put(file)
                    i += 1

                if i > max_tracks_displayed:
                    queue_message += f'+{original_size - max_tracks_displayed} more.**'
                else:
                    queue_message += '**'

                await user_message.channel.send(queue_message)
            else:
                await user_message.channel.send('**Queue currently empty.**')

    # Pauses playback of the current video
    async def pause(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and self.voice_client.is_playing():
                await user_message.channel.send('**Pausing playback...**')
                self.voice_client.pause()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Resumes playback of the current video
    async def resume(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and self.voice_client.is_paused():
                await user_message.channel.send('**Resuming playback...**')
                self.voice_client.resume()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Skips playback of the current video
    async def skip(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and (
                    self.voice_client.is_playing() or self.voice_client.is_paused()):
                await user_message.channel.send('**Skipping...**')
                self.voice_client.stop()

            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Leaves the current voice channel
    async def leave(self, user_message):
        if self.voice_client is not None:
            self.clear_queue()
            await self.cleanup()
        else:
            await user_message.channel.send('**Not currently connected to a voice channel.**')

    # Downloads/enqueues each of the videos in 'urls'
    async def enqueue_videos(self, urls):
        await self.enqueueing.acquire()  # Waiting for earlier enqueueing operations to finish
        for url in urls:
            info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url))
            await self.queue.put(self.ydl.prepare_filename(info))

        self.loop.call_soon_threadsafe(self.enqueueing.release)

    # Enqueues attachments
    async def enqueue_attachments(self, attachments):
        await self.enqueueing.acquire()  # Waiting for earlier enqueueing operations to finish
        for attachment in attachments:
            await attachment.save(attachment.filename)
            await self.queue.put(attachment.filename)

        self.loop.call_soon_threadsafe(self.enqueueing.release)

    # Loops through the queue and plays everything inside
    async def player_loop(self, user_message):
        self.voice_client = await user_message.author.voice.channel.connect()
        while True:
            self.next.clear()
            # Wait for the next track. If we don't get anything new within 5 minutes, disconnect from voice and cleanup
            try:
                async with timeout(300):
                    file = await self.queue.get()

            except asyncio.TimeoutError:
                break

            # Play the file in voice
            await self.recycling.put(file)
            await self.replace_status_message(user_message, f'**Now playing *{strip_extension(file)}*...**')
            self.voice_client.play(discord.FFmpegPCMAudio(file),
                                   after=lambda _: self.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()  # Waiting for ffmpeg to finish

        await self.cleanup()

    # Plays video/attachment given in the message
    async def play(self, user_message):
        if await self.sender_in_voice(user_message, send_warning=True):
            url = user_message.content[6:] if len(user_message.content) > 6 else ''
            if is_valid_url(url) or user_message.attachments:
                playlist = False
                # Enqueues attachments
                if user_message.attachments:
                    # Checks to see that all attachments are valid
                    for attachment in user_message.attachments:
                        if not is_valid_attachment(attachment.filename):
                            user_message.channel.send('**Error. One or more attachments is an invalid file type.**')
                            return

                    if len(user_message.attachments) > 1:
                        playlist = True
                        await self.replace_status_message(
                            user_message, f'**Enqueuing {len(user_message.attachments)} attachments...**')

                    self.loop.create_task(self.enqueue_attachments(user_message.attachments))
                # Enqueues normal youtube videos
                else:
                    await self.replace_status_message(user_message, '**Collecting info...**')
                    try:
                        info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url, download=False))
                    except Exception as e:
                        await user_message.channel.send('**Error getting video(s).**')
                        logging.getLogger('discord').error(e)
                        return

                    # Enqueues a playlist
                    if 'entries' in info:
                        playlist = True
                        await self.replace_status_message(
                            user_message, f'**Enqueueing {info["playlist_count"]} videos...**')
                        self.loop.create_task(self.enqueue_videos([s['webpage_url'] for s in info['entries']]))
                    else:
                        self.loop.create_task(self.enqueue_videos([info['webpage_url']]))

                # Ends here if the player is already active
                if self.voice_client is not None:
                    if not playlist:
                        await self.replace_status_message(user_message, '**Enqueueing new track...**')

                # Otherwise starts running through the queue
                else:
                    await self.player_loop(user_message)

            else:
                await user_message.channel.send('**Not a valid link/attachment.**')

    # Sends a help message with all the bot's commands
    @staticmethod
    async def help(user_message):
        help_message = ('**!help - list all commands\n'
                        '!play [url] - play specified video(s) in the current voice channel\n'
                        '!play (with attachment) - play attachment(s) in the current voice channel\n'
                        '!pause - pause playback\n'
                        '!resume - resume playback\n'
                        '!skip - skip the current video\n'
                        '!queue - display the current contents of the queue\n'
                        '!clear - clear the queue\n'
                        '!leave - leave the current voice channel**')
        await user_message.channel.send(help_message)


class YoutubeBot(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        load_dotenv()  # Loads the .env file where the tokens are stored
        self.discord_token = os.getenv('DISCORD_TOKEN')
        self.players = {}  # Dictionary of players for each discord guild
        self.ydl = YoutubeDL({'quiet': True})
        self.ydl.add_default_info_extractors()

    # Discord client startup tasks
    async def on_ready(self):
        print(f'{self.user} has connected to Discord')
        print('Severs:')
        for guild in self.guilds:
            print(f'{guild.name} (id: {guild.id})')
            self.players[guild.id] = Player(self.loop, self.ydl, guild.id)

    # Background task for custom status
    async def custom_status_background(self):
        await self.wait_until_ready()
        while True:
            await self.change_presence(activity=discord.Game('Type !help for commands.'))
            await asyncio.sleep(3600)

    async def setup_hook(self):
        self.loop.create_task(self.custom_status_background())

    # Adding a new player when we join a new guild
    async def on_guild_join(self, guild):
        self.players[guild.id] = Player(self.loop, self.ydl, guild.id)

    # Bot message commands
    async def on_message(self, message):
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
            elif message.content == '!leave':
                await self.players[message.guild.id].leave(message)
            elif message.content == '!queue':
                await self.players[message.guild.id].display_queue(message)
            elif message.content == '!clear':
                await self.players[message.guild.id].clear(message)
            elif message.content == '!help':
                await self.players[message.guild.id].help(message)


def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    log_handler = logging.FileHandler(filename='log.txt', encoding='utf-8', mode='w')
    bot = YoutubeBot(intents=intents)
    bot.run(bot.discord_token, log_handler=log_handler, log_level=logging.INFO)


if __name__ == '__main__':
    main()
