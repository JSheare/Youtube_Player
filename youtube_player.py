import asyncio
import discord
import logging
import os
from async_timeout import timeout
from discord.ext import tasks
from dotenv import load_dotenv
from yt_dlp import YoutubeDL


def is_valid_url(url):
    return (len(url) >= 23 and url[:23] == 'https://www.youtube.com') or (
        len(url) >= 16 and url[:16] == 'https://youtu.be') or (
            len(url) >= 19 and url[:19] == 'https://youtube.com')


def is_valid_attachment(filename):
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    if i < 0:
        return False

    extension = filename[i:]
    if extension == '.mp3':
        return True
    elif extension == '.mp4':
        return True
    elif extension == '.wav':
        return True
    elif extension == '.webm':
        return True
    elif extension == '.mov':
        return True

    return False


def strip_extension(filename):
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    if i < 0:
        return ''

    return filename[:i]


# A class that manages downloaded files
class Recycler:
    def __init__(self, loop):
        self.loop = loop
        self.files = {}  # A map of files with reference counts
        self.lock = asyncio.Lock()

    # Increment the reference count for a file
    async def increment(self, file):
        async with self.lock:
            if file in self.files:
                self.files[file] += 1
            else:
                self.files[file] = 1

    # Decrement the reference count for a file
    async def decrement(self, file):
        async with self.lock:
            if file in self.files:
                self.files[file] -= 1

    # Cleanup files with no references
    @tasks.loop(seconds=1)
    async def cleanup(self):
        async with self.lock:
            dead_files = []
            for file in self.files:
                if self.files[file] == 0:
                    dead_files.append(file)

            for file in dead_files:
                del self.files[file]
                os.remove(file)


# A class that manages the queued tracks, including enqueueing and cleanup
class TrackQueue:
    def __init__(self, loop, ydl, recycler):
        self.loop = loop
        self.ydl = ydl
        self.recycler = recycler

        # Stuff related to the download work queue
        self._work_types = {'attachments': self._put_attachments, 'videos': self._put_videos}
        self._work_queue = asyncio.Queue()
        self._work_available = asyncio.Condition()
        self._stop_processing = asyncio.Event()
        self._processing_stopped = asyncio.Event()

        # Stuff related to the track queue itself
        self._track_queue = asyncio.Queue()
        self._prev_track = None
        self._track_available = asyncio.Condition()

        # Starting up the work processing task
        self.loop.create_task(self._process_work())

    # Enqueues the given attachments for download and track queueing
    async def enqueue_attachments(self, attachments):
        if not self._stop_processing.is_set():
            await self._work_queue.put(('attachments', attachments))
            async with self._work_available:
                self._work_available.notify()

    # Enqueues the videos given in urls for download and track queueing
    async def enqueue_videos(self, urls):
        if not self._stop_processing.is_set():
            await self._work_queue.put(('videos', urls))
            async with self._work_available:
                self._work_available.notify()

    # Downloads the given attachments and puts them onto the track queue
    async def _put_attachments(self, attachments):
        for attachment in attachments:
            await attachment.save(attachment.filename)
            await self.recycler.increment(attachment.filename)
            await self._track_queue.put(attachment.filename)

    # Downloads the videos given in urls and puts them onto the track queue
    async def _put_videos(self, urls):
        for url in urls:
            try:
                info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url))
            except Exception as e:
                info = None
                logging.getLogger('discord').error(e)

            if info is not None:
                file = self.ydl.prepare_filename(info)
                await self.recycler.increment(file)
                await self._track_queue.put(file)

    # Executes the work in the work queue
    async def _process_work(self):
        while True:
            if self._work_queue.empty():
                # Sleeping until there's work to do
                async with self._work_available:
                    await self._work_available.wait()

            # Stopping the task if instructed to do so by another task
            if self._stop_processing.is_set():
                self._processing_stopped.set()
                break

            # Executing the work and notifying anyone who's waiting that the track queue has a new track
            job = await self._work_queue.get()
            await self._work_types[job[0]](job[1])
            async with self._track_available:
                self._track_available.notify()

    # Blocks until the track queue contains a track
    async def wait(self):
        if self._track_queue.empty():
            async with self._track_available:
                await self._track_available.wait()

        return True

    # Gets and returns a track from the queue. Also recycles the previous one if it hasn't been recycled already
    async def get(self):
        await self.track_finished()
        track = await self._track_queue.get()
        self._prev_track = track
        return track

    # Signals the queue to recycle the track most recently returned by get()
    async def track_finished(self):
        if self._prev_track is not None:
            await self.recycler.decrement(self._prev_track)
            self._prev_track = None

    # Returns the list of tracks currently in the queue, optionally up to a specified maximum length
    def get_tracklist(self, max_tracks=-1):
        tracks = []
        if max_tracks == -1:
            max_tracks = float('inf')

        for i in range(0, self._track_queue.qsize()):
            track = self._track_queue.get_nowait()
            if i < max_tracks:
                tracks.append(track)

            self._track_queue.put_nowait(track)

        return tracks

    # Returns True if the track queue is empty
    def empty(self):
        return self._track_queue.empty()

    # Returns the current size of the track queue
    def qsize(self):
        return self._track_queue.qsize()

    # Clears the queue, including any currently-enqueued work
    async def clear(self):
        if (not self._track_queue.empty() or not self._work_queue.empty()) and not self._stop_processing.is_set():
            # Waking up the work processing task if necessary and telling it to stop
            self._stop_processing.set()
            async with self._work_available:
                self._work_available.notify()

            await self._processing_stopped.wait()  # Unblocks when the work processing task has stopped
            # Emptying the queues
            while not self._work_queue.empty():
                self._work_queue.get_nowait()

            while not self._track_queue.empty():
                track = self._track_queue.get_nowait()
                await self.recycler.decrement(track)

            # Clearing all the signaling events and restarting the work processing task
            self._stop_processing.clear()
            self._processing_stopped.clear()
            self.loop.create_task(self._process_work())


# A class that manages player activities for each discord guild
class Player:
    def __init__(self, loop, ydl, recycler, guild_id):
        self.loop = loop
        self.ydl = ydl
        self.guild_id = guild_id
        self.status_message = None
        self.voice_client = None
        self.queue = TrackQueue(loop, ydl, recycler)
        self.next = asyncio.Event()

    # Deletes the old player status message before sending the new one
    async def _replace_status_message(self, user_message, string):
        if self.status_message is not None:
            try:
                await self.status_message.delete()
            except discord.errors.NotFound:
                pass

        self.status_message = await user_message.channel.send(string)

    # Checks to see if the message sender is in a voice channel, and optionally sends a warning if they aren't
    @staticmethod
    async def _sender_in_voice(user_message, send_warning=False):
        if user_message.author.voice:
            return True
        else:
            if send_warning:
                await user_message.channel.send('**Connect to a voice channel to use commands.**')

            return False

    # Clears the queue
    async def clear(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if not self.queue.empty():
                await user_message.channel.send('**Emptying queue...**')
                await self.queue.clear()
            else:
                await user_message.channel.send('**Queue already empty.**')

    # Displays the current queue
    async def display_queue(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if not self.queue.empty():
                max_tracks_displayed = 10
                original_size = self.queue.qsize()
                tracks = self.queue.get_tracklist(max_tracks=max_tracks_displayed)
                queue_message = '**__Currently in queue:__\n'
                for track in tracks:
                    queue_message += f'*{strip_extension(track)}*\n'

                if original_size > max_tracks_displayed:
                    queue_message += f'+{original_size - max_tracks_displayed} more.**'
                else:
                    queue_message += '**'

                await user_message.channel.send(queue_message)

            else:
                await user_message.channel.send('**Queue currently empty.**')

    # Pauses playback of the current track
    async def pause(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and self.voice_client.is_playing():
                await user_message.channel.send('**Pausing playback...**')
                self.voice_client.pause()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Resumes playback of the current track
    async def resume(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and self.voice_client.is_paused():
                await user_message.channel.send('**Resuming playback...**')
                self.voice_client.resume()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Skips playback of the current track
    async def skip(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if self.voice_client is not None and (
                    self.voice_client.is_playing() or self.voice_client.is_paused()):
                await user_message.channel.send('**Skipping...**')
                self.voice_client.stop()

            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Leaves the current voice channel
    async def leave(self, user_message):
        if self.voice_client is not None:
            await self.queue.clear()
            await self.voice_client.disconnect()
            self.voice_client = None
        else:
            await user_message.channel.send('**Not currently connected to a voice channel.**')

    # Loops through the queue and plays everything inside
    async def _player_loop(self, user_message):
        await self.queue.wait()
        self.voice_client = await user_message.author.voice.channel.connect()
        while True:
            self.next.clear()
            # Wait for the next track. If we don't get anything new within 5 minutes, disconnect from voice and cleanup
            try:
                async with timeout(300):
                    track = await self.queue.get()

            except asyncio.TimeoutError:
                break

            # Plays the file in voice
            await self._replace_status_message(user_message, f'**Now playing *{strip_extension(track)}*...**')
            self.voice_client.play(discord.FFmpegPCMAudio(track),
                                   after=lambda _: self.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()  # Waiting for ffmpeg to finish
            await self.queue.track_finished()

        # Leaves voice
        if self.voice_client is not None:
            await self.voice_client.disconnect()
            self.voice_client = None

    # Plays video/attachment given in the message
    async def play(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
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
                        await self._replace_status_message(
                            user_message, f'**Enqueuing {len(user_message.attachments)} attachments...**')

                    await self.queue.enqueue_attachments(user_message.attachments)
                # Enqueues normal youtube videos
                else:
                    await self._replace_status_message(user_message, '**Collecting info...**')
                    try:
                        info = await self.loop.run_in_executor(None, lambda: self.ydl.extract_info(url, download=False))
                    except Exception as e:
                        await user_message.channel.send('**Error getting video(s).**')
                        logging.getLogger('discord').error(e)
                        return

                    # Enqueues a playlist
                    if 'entries' in info:
                        if 'playlist' in url:
                            playlist = True
                            await self._replace_status_message(
                                user_message, f'**Enqueueing {info["playlist_count"]} videos...**')
                            await self.queue.enqueue_videos([s['url'] for s in info['entries']])
                        else:
                            await self.queue.enqueue_videos([url.split('&')[0]])
                    else:
                        await self.queue.enqueue_videos([info['webpage_url']])

                # Ends here if the player is already active
                if self.voice_client is not None:
                    if not playlist:
                        await self._replace_status_message(user_message, '**Enqueueing new track...**')

                # Otherwise starts running through the queue
                else:
                    await self._player_loop(user_message)

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
        self.recycler = Recycler(self.loop)
        self.ydl = YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist'})
        self.ydl.add_default_info_extractors()

    # Discord client startup tasks
    async def on_ready(self):
        print(f'{self.user} has connected to Discord')
        print('Severs:')
        for guild in self.guilds:
            print(f'{guild.name} (id: {guild.id})')
            self.players[guild.id] = Player(self.loop, self.ydl, self.recycler, guild.id)

    # Background task for custom status
    @tasks.loop(minutes=60)
    async def custom_status_background(self):
        await self.wait_until_ready()
        await self.change_presence(activity=discord.Game('Type !help for commands.'))

    async def setup_hook(self):
        self.custom_status_background.start()
        self.recycler.cleanup.start()

    # Adding a new player when we join a new guild
    async def on_guild_join(self, guild):
        self.players[guild.id] = Player(self.loop, self.ydl, self.recycler, guild.id)

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
