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
    def __init__(self, ydl):
        self._ydl = ydl
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
                loop = asyncio.get_event_loop()
                track_handle = self._ydl.prepare_filename(
                    await loop.run_in_executor(None, self._ydl.extract_info, track_name))
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


# A class that provides access to the discord voice client
class Voice:
    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()
        self._busy = asyncio.Lock()
        self._track_finished = asyncio.Event()

    # Returns True if the voice client is already connected
    def is_connected(self):
        return self._client is not None

    # Connects to the given voice channel
    async def connect(self, channel):
        async with self._lock:
            if self._client is not None:
                await self._client.disconnect()

            self._client = await channel.connect()

    # Disconnects from the current voice channel
    async def disconnect(self):
        async with self._lock:
            if self._client is not None:
                await self._client.disconnect()
                self._client = None

    # Plays the track with the given handle in the current voice channel
    async def play(self, track_handle):
        async with self._busy:
            async with self._lock:
                if self._client is not None:
                    loop = asyncio.get_event_loop()
                    self._client.play(discord.FFmpegPCMAudio(track_handle),
                                      after=lambda _: loop.call_soon_threadsafe(self._track_finished.set))
                else:
                    raise RuntimeError('voice client is not connected')

            await self._track_finished.wait()
            self._track_finished.clear()

    # Returns True if the voice client is already playing a track
    def is_playing(self):
        if self._client is not None:
            return self._client.is_playing()
        else:
            raise RuntimeError('voice client is not connected')

    # Returns True if the voice client is paused
    def is_paused(self):
        if self._client is not None:
            return self._client.is_paused()
        else:
            raise RuntimeError('voice client is not connected')

    # Pauses playback if something is playing
    async def pause(self):
        async with self._lock:
            if self._client is not None and self._client.is_playing():
                self._client.pause()

    # Resumes playback if it's paused
    async def resume(self):
        async with self._lock:
            if self._client is not None and self._client.is_paused():
                self._client.resume()

    # Stops playback if something is playing
    async def stop(self):
        async with self._lock:
            if self._client is not None and (self._client.is_playing() or self._client.is_paused()):
                self._client.stop()


# A class that manages the queued tracks, including enqueueing and playing
class TrackQueue:
    def __init__(self, recycler):
        self._recycler = recycler
        self._clearing_queue = asyncio.Event()

        self._enqueue_lock = asyncio.Lock()

        self._track_queue = deque()
        self._track_available = asyncio.Condition()
        self._track_lock = asyncio.Lock()

    # Enqueues the tracks in the given list
    async def enqueue(self, track_list):
        async with self._enqueue_lock:
            if self._clearing_queue.is_set():
                return

            for track in track_list:
                if self._clearing_queue.is_set():
                    break

                track_handle = await self._recycler.get_track_handle(track)
                if track_handle != '':
                    await self._put_track(track_handle)

    # Puts the track with the given handle on the track queue
    async def _put_track(self, track_handle):
        async with self._track_lock:
            self._track_queue.append(track_handle)
            async with self._track_available:
                self._track_available.notify_all()

    # Blocks until the track queue contains a track
    async def wait(self):
        if len(self._track_queue) == 0:
            async with self._track_available:
                await self._track_available.wait_for(lambda: len(self._track_queue) > 0)

        return True

    # Returns the handle of the track at the front of the queue without popping it
    def front(self):
        if len(self._track_queue) > 0:
            return self._track_queue[0]
        else:
            raise IndexError('cannot get the front of an empty queue')

    # Plays the track that's currently at the front of the queue in the given voice channel
    async def play(self, voice):
        async with self._track_lock:
            if len(self._track_queue) == 0:
                return
            else:
                track_handle = self._track_queue.popleft()

        await voice.play(track_handle)
        await self._recycler.decrement(track_handle)

    # Returns the list of tracks currently in the queue, optionally up to a specified maximum length
    async def get_tracklist(self, max_tracks=0):
        tracks = []
        if max_tracks <= 0:
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

    # Clears the queue, including any currently-enqueued downloads
    async def clear(self):
        if not self._clearing_queue.is_set():
            # The ordering of these is important
            self._clearing_queue.set()
            await self._enqueue_lock.acquire()
            await self._track_lock.acquire()
            try:
                while len(self._track_queue) > 0:
                    track_handle = self._track_queue.popleft()
                    await self._recycler.decrement(track_handle)

            finally:
                # The ordering of these is also important
                self._clearing_queue.clear()
                self._track_lock.release()
                self._enqueue_lock.release()


# A class that manages player activities for each discord guild
class Player:
    def __init__(self, ydl, recycler, guild_id):
        self.ydl = ydl
        self.guild_id = guild_id
        self._status_message = None
        self._message_lock = asyncio.Lock()
        self._voice = Voice()
        self._player_looping = asyncio.Lock()
        self._queue = TrackQueue(recycler)

    # Deletes the old player status message before sending the new one
    async def _replace_status_message(self, user_message, string):
        async with self._message_lock:
            if self._status_message is not None:
                try:
                    await self._status_message.delete()
                except discord.errors.NotFound:
                    pass

            self._status_message = await user_message.channel.send(string)

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
            if not self._queue.empty():
                await user_message.channel.send('**Emptying queue...**')
                await self._queue.clear()
            else:
                await user_message.channel.send('**Queue already empty.**')

    # Displays the current queue
    async def display_queue(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if not self._queue.empty():
                max_tracks_displayed = 10
                tracks = await self._queue.get_tracklist(max_tracks=max_tracks_displayed)
                original_size = self._queue.size()
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
            if self._voice.is_connected() and self._voice.is_playing():
                await user_message.channel.send('**Pausing playback...**')
                await self._voice.pause()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Resumes playback of the current track
    async def resume(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and self._voice.is_paused():
                await user_message.channel.send('**Resuming playback...**')
                await self._voice.resume()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Skips playback of the current track
    async def skip(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and (
                    self._voice.is_playing() or self._voice.is_paused()):
                await user_message.channel.send('**Skipping...**')
                await self._voice.stop()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    # Leaves the current voice channel
    async def leave(self, user_message):
        if self._voice.is_connected():
            await self._queue.clear()
            await self._voice.disconnect()
        else:
            await user_message.channel.send('**Not currently connected to a voice channel.**')

    # Loops through the queue and plays everything inside
    async def _player_loop(self, user_message):
        async with self._player_looping:
            while True:
                # Waits for the next track. If we don't get anything new within 5 minutes, disconnect from voice
                try:
                    async with (timeout(300)):
                        await self._queue.wait()

                except asyncio.TimeoutError:
                    break

                try:
                    # Plays the track
                    if not self._voice.is_connected():
                        await self._voice.connect(user_message.author.voice.channel)

                    if not self._queue.empty():
                        track_handle = self._queue.front()
                        await self._replace_status_message(user_message,
                                                           f'**Now playing *{strip_extension(track_handle)}*...**')
                        await self._queue.play(self._voice)

                except Exception as ex:
                    logging.getLogger('discord').error(ex)

            # Leaves voice
            await self._voice.disconnect()

    # Enqueues and plays videos/attachments given in the message
    async def play(self, user_message):
        if await self._sender_in_voice(user_message, send_warning=True):
            url = user_message.content[6:] if len(user_message.content) > 6 else ''
            if is_valid_url(url) or user_message.attachments:
                loop = asyncio.get_event_loop()
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

                    track_list = user_message.attachments
                # Enqueues normal youtube videos
                else:
                    await self._replace_status_message(user_message, '**Collecting info...**')
                    try:
                        info = await loop.run_in_executor(None, self.ydl.extract_info, url, False)
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
                            track_list = [s['url'] for s in info['entries']]
                        else:
                            await self._queue.enqueue([url.split('&')[0]])
                            track_list = [url.split('&')[0]]
                    else:
                        track_list = [info['webpage_url']]

                if self._player_looping.locked() and not playlist:
                    await self._replace_status_message(user_message, '**Enqueueing new track...**')

                _ = loop.create_task(self._queue.enqueue(track_list))
                await self._queue.wait()

                # Starts the player if it hasn't been already
                if not self._player_looping.locked():
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
        self.ydl = YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist', 'extract_audio': True,
                              'format': 'bestaudio', 'outtmpl': '%(title)s.mp3'})
        self.ydl.add_default_info_extractors()
        self.recycler = TrackRecycler(self.ydl)

    # Discord client startup tasks
    async def on_ready(self):
        print(f'{self.user} has connected to Discord')
        print('Severs:')
        for guild in self.guilds:
            print(f'{guild.name} (id: {guild.id})')
            self.players[guild.id] = Player(self.ydl, self.recycler, guild.id)

    # Background task for custom status
    @tasks.loop(minutes=60)
    async def custom_status_background(self):
        await self.wait_until_ready()
        await self.change_presence(
            activity=discord.Activity(name='!help for commands.', type=discord.ActivityType.listening))

    async def setup_hook(self):
        self.custom_status_background.start()
        self.recycler.cleanup.start()

    # Adding a new player when we join a new guild
    async def on_guild_join(self, guild):
        self.players[guild.id] = Player(self.ydl, self.recycler, guild.id)

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
