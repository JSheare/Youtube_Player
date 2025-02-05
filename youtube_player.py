import asyncio
import discord
import logging
import os
from async_timeout import timeout
from collections import deque
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


# A class that downloads and manages tracks for the bot
class TrackRecycler:
    def __init__(self, loop, ydl):
        self.loop = loop
        self.ydl = ydl
        self._tracks = {}  # Maps track names to handles
        self._handles = {}  # Maps handles to track names
        self._references = {}  # Maps handles to references
        self._lock = asyncio.Lock()

    # Returns the track handle for the specified track and increments the track's counter
    async def get_track_handle(self, track):
        # For Discord message attachment
        if isinstance(track, discord.Attachment):
            track_name = track.filename
            async with self._lock:
                # If the attachment is in the recycler already
                if track_name in self._tracks:
                    self._references[self._tracks[track_name]] += 1
                    return self._tracks[track_name]

            track_handle = track.filename
            try:
                await track.save(track_handle)
            except Exception as ex:
                logging.getLogger('discord').error(ex)
                return ''

        # For Youtube video url
        else:
            track_name = track
            async with self._lock:
                # If the video is in the recycler already
                if track_name in self._tracks:
                    self._references[self._tracks[track_name]] += 1
                    return self._tracks[track_name]

            try:
                info = self.ydl.extract_info(track_name)
                track_handle = self.ydl.prepare_filename(info)
            except Exception as ex:
                logging.getLogger('discord').error(ex)
                return ''

        async with self._lock:
            self._tracks[track_name] = track_handle
            self._handles[track_handle] = track_name
            self._references[track_handle] = 1
            return track_handle

    # Decrements the reference count for the track with the given handle
    async def decrement(self, track_handle):
        async with self._lock:
            if track_handle in self._handles:
                self._references[track_handle] -= 1

    # Cleanup tracks with no references
    @tasks.loop(seconds=5)
    async def cleanup(self):
        async with self._lock:
            dead_tracks = []
            for track_handle in self._references:
                if self._references[track_handle] == 0:
                    dead_tracks.append(track_handle)

            for track_handle in dead_tracks:
                track_name = self._handles[track_handle]
                del self._tracks[track_name]
                del self._handles[track_handle]
                del self._references[track_handle]
                os.remove(track_handle)


# A class that manages the queued tracks, including enqueueing and playing
class TrackQueue:
    def __init__(self, loop, ydl, recycler):
        self.loop = loop
        self.ydl = ydl
        self.recycler = recycler

        # Stuff related to the download work queue
        self._work_queue = asyncio.Queue()
        self._work_available = asyncio.Condition()
        self._work_lock = asyncio.Lock()
        self._stop_processing = asyncio.Event()

        # Stuff related to the track queue itself
        self._track_queue = deque()
        self._track_available = asyncio.Condition()
        self._track_lock = asyncio.Lock()
        self._track_finished = asyncio.Event()

        # Starting up the work processing task
        self._download_task = self.loop.create_task(self._process_work())

    # Enqueues the tracks in the given list
    async def enqueue(self, tracks):
        async with self._work_lock:
            await self._work_queue.put(tracks)
            async with self._work_available:
                self._work_available.notify()

    # Puts the track with the given handle on the track queue
    async def _put_track(self, track_handle):
        async with self._track_lock:
            self._track_queue.append(track_handle)

        async with self._track_available:
            self._track_available.notify()

    async def _process_work(self):
        while True:
            if self._work_queue.empty():
                # Sleeping until there's work to do
                async with self._work_available:
                    await self._work_available.wait()

            # Stopping the task if instructed to do so by another task
            if self._stop_processing.is_set():
                break

            # Executing the work and notifying anyone who's waiting that the track queue has a new track
            tracks = await self._work_queue.get()
            for track in tracks:
                track_handle = await self.recycler.get_track_handle(track)
                if track_handle != '':
                    await self._put_track(track_handle)
                    async with self._track_available:
                        self._track_available.notify()

    # Blocks until the track queue contains a track
    async def wait(self):
        if len(self._track_queue) == 0:
            async with self._track_available:
                await self._track_available.wait()

        return True

    # Returns the handle of the track at the front of the queue without popping it
    async def front(self):
        async with self._track_lock:
            return self._track_queue[0]

    # Plays the track that's currently at the front of the queue in the given voice channel
    async def play(self, voice_client):
        async with self._track_lock:
            if len(self._track_queue) == 0:
                return
            else:
                track_handle = self._track_queue.popleft()

        voice_client.play(discord.FFmpegPCMAudio(track_handle),
                          after=lambda _: self.loop.call_soon_threadsafe(self._track_finished.set))
        await self._track_finished.wait()
        self._track_finished.clear()
        await self.recycler.decrement(track_handle)

    # Returns the list of tracks currently in the queue, optionally up to a specified maximum length
    async def get_tracklist(self, max_tracks=-1):
        tracks = []
        if max_tracks == -1:
            max_tracks = float('inf')

        async with self._track_lock:
            for i in range(len(self._track_queue)):
                track_handle = self._track_queue.popleft()
                if i < max_tracks:
                    tracks.append(track_handle)

                self._track_queue.append(track_handle)

        return tracks

    # Returns True if the track queue is empty
    def empty(self):
        return len(self._track_queue) == 0

    # Returns the current size of the track queue
    def size(self):
        return len(self._track_queue)

    # Clears the queue, including any currently-enqueued work
    async def clear(self):
        await self._work_lock.acquire()
        await self._track_lock.acquire()
        try:
            if len(self._track_queue) != 0 or not self._work_queue.empty():
                # Waking up the work processing task if necessary and telling it to stop
                self._stop_processing.set()
                async with self._work_available:
                    self._work_available.notify()

                await self._download_task  # Unblocks when the work processing task has finished executing
                self._download_task = None
                # Emptying the queues
                while len(self._track_queue) > 0:
                    track_handle = self._track_queue.popleft()
                    await self.recycler.decrement(track_handle)

                while not self._work_queue.empty():
                    self._work_queue.get_nowait()

        finally:
            self._stop_processing.clear()
            if self._download_task is None:
                self._download_task = self.loop.create_task(self._process_work())

            self._track_lock.release()
            self._work_lock.release()


# A class that manages player activities for each discord guild
class Player:
    def __init__(self, loop, ydl, recycler, guild_id):
        self.loop = loop
        self.ydl = ydl
        self.guild_id = guild_id
        self.status_message = None
        self.voice_client = None
        self.queue = TrackQueue(loop, ydl, recycler)

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
                tracks = await self.queue.get_tracklist(max_tracks=max_tracks_displayed)
                original_size = self.queue.size()
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
            # Wait for the next track. If we don't get anything new within 5 minutes, disconnect from voice
            try:
                async with (timeout(300)):
                    await self.queue.wait()
                    track_handle = await self.queue.front()
                    await self._replace_status_message(user_message,
                                                       f'**Now playing *{strip_extension(track_handle)}*...**')
                    await self.queue.play(self.voice_client)

            except asyncio.TimeoutError:
                break
            except Exception as ex:
                logging.getLogger('discord').error(ex)

        # Leaves voice
        if self.voice_client is not None:
            await self.voice_client.disconnect()
            self.voice_client = None

    # Enqueues and plays video/attachment given in the message
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

                    await self.queue.enqueue(user_message.attachments)
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
                            await self.queue.enqueue([s['url'] for s in info['entries']])
                        else:
                            await self.queue.enqueue([url.split('&')[0]])
                    else:
                        await self.queue.enqueue([info['webpage_url']])

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
        self.ydl = YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist'})
        self.ydl.add_default_info_extractors()
        self.recycler = TrackRecycler(self.loop, self.ydl)

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
