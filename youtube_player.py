import discord
import os
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from yt_dlp import YoutubeDL


def is_valid_url(url):
    return (len(url) >= 23 and url[:23] == 'https://www.youtube.com') or (
        len(url) >= 16 and url[:16] == 'https://youtu.be')


class Youtubebot(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        load_dotenv()  # Loads the .env file where the tokens are stored
        self.discord_token = os.getenv('DISCORD_TOKEN')
        self.log = open('log.txt', 'w')
        self.queues = {}  # Keeps a queue of songs to be played for each guild
        self.recycling = {}  # Keeps a queue of audio files to be deleted for each guild

        self.ydl = YoutubeDL()
        self.ydl.add_default_info_extractors()

    # Discord client startup tasks
    async def on_ready(self):
        print(f'{self.user} has connected to Discord')
        print(f'Severs: {", ".join([guild.name + f" (id: {guild.id})" for guild in self.guilds])}')
        now = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(tz=None)
        print(f'{now}: Bot initialization', file=self.log)

    # Bot message commands
    async def on_message(self, message):
        # Ignores any messages from the bot itself
        if message.author == self.user:
            return
        else:
            if '!play' in message.content:
                await self.play(message, message.content[6:])
            elif message.content == '!pause':
                await self.pause(message)
            elif message.content == '!resume':
                await self.resume(message)
            elif message.content == '!skip':
                await self.skip(message)
            elif message.content == '!leave':
                await self.leave(message)
            elif message.content == '!queue':
                await self.display_queue(message)
            elif message.content == '!clear':
                await self.clear_queue(message)
            elif message.content == '!help':
                await self.help(message)

    async def list_from_url(self, url):
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: self.ydl.extract_info(url))
        queue = []
        # If the url is a playlist
        if 'entries' in info:
            for video in info['entries']:
                queue.append(self.ydl.prepare_filename(video))

        # If the url is a single video
        else:
            queue.append(self.ydl.prepare_filename(info))

        return queue

    # Check if user is connected to a voice channel and send a message if they aren't
    async def is_valid_command(self, message):
        if message.author.voice:
            return True
        else:
            await message.channel.send('Connect to a voice channel to use commands.')
            return False

    # Leaves the voice channel that the message sender is in
    async def leave(self, message):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_connected():
            await self.clear_queue(message, True)
            await voice_client.disconnect()
        else:
            await message.channel.send('Not currently connected to a voice channel.')

    # Waits five minutes and then disconnects the bot from the current voice channel if it isn't playing anything
    async def timeout(self, voice_client):
        await asyncio.sleep(300)
        if not voice_client.is_playing():
            await voice_client.disconnect()

    # Recursively plays more tracks from the queue. Note: this means that you can only enqueue ~1000 videos at a time
    async def check_queue(self, guild_id, message, voice_client, prev_file):
        # These os removes will unfortunately lead to a situation where if the same video is queued twice
        # It will error a little. Too lazy to fix this right now
        if self.queues[guild_id].empty():
            os.remove(prev_file)
            while not self.recycling[guild_id].empty():
                os.remove(self.recycling[guild_id].get_nowait())

            del self.queues[guild_id]
            del self.recycling[guild_id]
            await self.timeout(voice_client)
        else:
            await self.recycling[guild_id].put(prev_file)
            file = self.queues[guild_id].get_nowait()
            await message.channel.send(f'Now playing {file[:-5]}...')
            # voice_client.play(discord.FFmpegPCMAudio(file, executable='C:/ffmpeg/bin/ffmpeg.exe'),  # on win
            #                   after=lambda x:
            #                   asyncio.run_coroutine_threadsafe(
            #                   self.check_queue(guild_id, message, voice_client, file), self.loop))
            voice_client.play(discord.FFmpegPCMAudio(file),
                              after=lambda x:
                              asyncio.run_coroutine_threadsafe(
                                  self.check_queue(guild_id, message, voice_client, file), self.loop))

    # Downloads/enqueues each of the videos in 'urls'
    async def enqueue(self, urls, guild_id):
        queue = self.queues[guild_id]
        for url in urls:
            info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url))
            await queue.put(self.ydl.prepare_filename(info))

    # Plays the video corresponding to the given url
    async def play(self, message, url):
        if await self.is_valid_command(message):
            if is_valid_url(url):
                guild_id = message.guild.id
                voice_client = message.guild.voice_client
                if not voice_client:
                    voice_client = await message.author.voice.channel.connect()

                await message.channel.send('Collecting info...')
                try:
                    info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url, download=False))

                except Exception as e:
                    await message.channel.send('Error getting video(s).')
                    now = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(tz=None)
                    print(f'{now}: Failed to get video(s) from {url}. Error:', file=self.log)
                    print(e, file=self.log)
                    await self.timeout(voice_client)
                    return

                if guild_id in self.queues:
                    queue = self.queues[guild_id]
                else:
                    self.queues[guild_id] = asyncio.Queue()
                    self.recycling[guild_id] = asyncio.Queue()
                    queue = self.queues[guild_id]

                now = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(tz=None)
                print(f'{now}: Enqueueing video(s) from {url}', file=self.log)
                if 'entries' in info:
                    await message.channel.send(f'Enqueueing {info["playlist_count"]} videos...')
                    self.loop.create_task(self.enqueue([s['webpage_url'] for s in info['entries']], guild_id))
                else:
                    self.loop.create_task(self.enqueue([info['webpage_url']], guild_id))

                # Ends here if the player is already active
                if voice_client.is_playing() or (guild_id in self.queues and not self.queues[guild_id].empty()):
                    if 'entries' not in info:
                        await message.channel.send('Enqueueing new video...')

                    return
                # Otherwise just plays the videos
                else:
                    file = await queue.get()
                    await message.channel.send(f'Now playing {file[:-5]}...')
                    # voice_client.play(discord.FFmpegPCMAudio(file, executable='C:/ffmpeg/bin/ffmpeg.exe'),  # on win
                    #                   after=lambda x:
                    #                   asyncio.run_coroutine_threadsafe(
                    #                   self.check_queue(guild_id, message, voice_client, file), self.loop))
                    voice_client.play(discord.FFmpegPCMAudio(file),
                                      after=lambda x:
                                      asyncio.run_coroutine_threadsafe(
                                          self.check_queue(guild_id, message, voice_client, file), self.loop))

            else:
                await message.channel.send('Not a valid link.')

    # Pauses playback of the current video
    async def pause(self, message):
        if await self.is_valid_command(message):
            voice_client = message.guild.voice_client
            if voice_client and voice_client.is_playing():
                await message.channel.send('Pausing playback...')
                voice_client.pause()
            else:
                await message.channel.send('Nothing playing right now.')

    # Resumes playback of the current video
    async def resume(self, message):
        if await self.is_valid_command(message):
            voice_client = message.guild.voice_client
            if voice_client and voice_client.is_paused():
                await message.channel.send('Resuming playback...')
                voice_client.resume()
            else:
                await message.channel.send('No videos enqueued.')

    # Skips playback of the current video
    async def skip(self, message):
        if await self.is_valid_command(message):
            voice_client = message.guild.voice_client
            if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
                await message.channel.send('Skipping...')
                voice_client.stop()
            else:
                await message.channel.send('Nothing playing right now.')

    # Displays the current queue
    async def display_queue(self, message):
        if await self.is_valid_command(message):
            guild_id = message.guild.id
            if guild_id in self.queues and not self.queues[guild_id].empty():
                queue = self.queues[guild_id]
                i = 0
                queue_message = 'Currently in queue:\n'
                original_size = queue.qsize()
                while i < original_size:
                    video_title = queue.get_nowait()
                    if i < 10:
                        queue_message += f'{video_title[:-5]}\n'
                    elif i == 10:
                        queue_message += f'+{queue.qsize()} more.'

                    await queue.put(video_title)

                    i += 1

                await message.channel.send(queue_message)
            else:
                await message.channel.send('Queue currently empty.')

    # Clears all videos currently in the queue
    async def clear_queue(self, message, supress_message=False):
        if await self.is_valid_command(message):
            guild_id = message.guild.id
            if guild_id in self.queues and not self.queues[guild_id].empty():
                if not supress_message:
                    await message.channel.send('Emptying queue...')

                queue = self.queues[guild_id]
                while not queue.empty():
                    file = queue.get_nowait()
                    os.remove(file)
            else:
                if not supress_message:
                    await message.channel.send('Queue already empty.')

    # Sends a help message with all the bots commands
    async def help(self, message):
        help_message = ('!help - list all commands\n'
                        '!play [url] - play specified video in the current voice channel\n'
                        '!pause - pause playback\n'
                        '!resume - resume playback\n'
                        '!skip - skip the current video\n'
                        '!queue - display the current contents of the queue\n'
                        '!clear - clear the queue\n'
                        '!leave - leave the current voice channel')
        now = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(tz=None)
        print(f'{now}: Sending help message', file=self.log)
        await message.channel.send(help_message)

    async def custom_status_background(self):
        await self.wait_until_ready()
        await self.change_presence(activity=discord.Game('Type !help for commands.'))

    async def setup_hook(self):
        self.loop.create_task(self.custom_status_background())


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = Youtubebot(intents=intents)
bot.run(bot.discord_token)
