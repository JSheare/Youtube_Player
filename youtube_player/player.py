"""A module containing a class that manages player activity for each Discord guild."""
import asyncio
import discord
import logging
from async_timeout import timeout
from typing import List

from youtube_player.track_queue import TrackQueue
from youtube_player.voice import Voice
from youtube_player.helpers.downloader import ydl
from youtube_player.helpers.helper_funcs import is_valid_attachment, is_valid_youtube
from youtube_player.tracks.attachment_track import AttachmentTrack
from youtube_player.tracks.youtube_track import YoutubeTrack


class Player:
    """A class that manages player activities for each discord guild."""
    def __init__(self, guild_id) -> None:
        self._logger = logging.getLogger('discord')
        self.guild_id = guild_id
        self._status_message = None
        self._message_lock = asyncio.Lock()
        self._voice = Voice()
        self._player_looping = asyncio.Lock()
        self._queue = TrackQueue()

    async def _replace_status_message(self, user_message: discord.Message, string: str) -> None:
        """Deletes the old player status message before sending the new one"""
        async with self._message_lock:
            if self._status_message is not None:
                try:
                    await self._status_message.delete()
                except discord.errors.NotFound:
                    pass

            self._status_message = await user_message.channel.send(string)

    @staticmethod
    async def _sender_in_voice(user_message: discord.Message, send_warning: bool = False) -> bool:
        """Checks to see if the message sender is in a voice channel, and optionally sends a warning if they aren't."""
        if user_message.author.voice:
            return True
        else:
            if send_warning:
                await user_message.channel.send('**Connect to a voice channel to use commands.**')

            return False

    async def clear(self, user_message: discord.Message) -> None:
        """Clears the queue."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if not self._queue.empty():
                await user_message.channel.send('**Emptying queue...**')
                await self._queue.clear()
            else:
                await user_message.channel.send('**Queue already empty.**')

    async def display_queue(self, user_message: discord.Message) -> None:
        """Displays the current queue."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if not self._queue.empty():
                max_tracks_displayed = 10
                tracks = await self._queue.get_tracklist(max_tracks=max_tracks_displayed)
                original_size = self._queue.size()
                queue_message = '**__Currently in queue:__\n'
                for track in tracks:
                    queue_message += f'*{track}*\n'

                if original_size > max_tracks_displayed:
                    queue_message += f'+{original_size - max_tracks_displayed} more.**'
                else:
                    queue_message += '**'

                await user_message.channel.send(queue_message)

            else:
                await user_message.channel.send('**Queue currently empty.**')

    async def pause(self, user_message: discord.Message) -> None:
        """Pauses playback of the current track."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and self._voice.is_playing():
                await user_message.channel.send('**Pausing playback...**')
                await self._voice.pause()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    async def resume(self, user_message: discord.Message) -> None:
        """Resumes playback of the current track."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and self._voice.is_paused():
                await user_message.channel.send('**Resuming playback...**')
                await self._voice.resume()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    async def skip(self, user_message: discord.Message) -> None:
        """Skips playback of the current track."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and (
                    self._voice.is_playing() or self._voice.is_paused()):
                await user_message.channel.send('**Skipping...**')
                if self._queue.is_looping_track():
                    self._queue.toggle_track_looping()

                await self._voice.stop()
            else:
                await user_message.channel.send('**Nothing playing right now.**')

    async def loop(self, user_message: discord.Message) -> None:
        """Toggles looping of the current track."""
        if await self._sender_in_voice(user_message, send_warning=True):
            if self._voice.is_connected() and (
                    self._voice.is_playing() or self._voice.is_paused()):
                if self._queue.is_looping_track():
                    await user_message.channel.send('**Stopping loop...**')
                else:
                    await user_message.channel.send('**Looping...**')

                self._queue.toggle_track_looping()

            else:
                await user_message.channel.send('**Nothing playing right now.**')

    async def leave(self, user_message: discord.Message) -> None:
        """Leaves the current voice channel."""
        if self._voice.is_connected():
            if self._queue.is_looping_track():
                self._queue.toggle_track_looping()

            await self._queue.clear()
            await self._voice.disconnect()
        else:
            await user_message.channel.send('**Not currently connected to a voice channel.**')

    async def _player_loop(self, user_message: discord.Message) -> None:
        """Loops through the queue and plays everything inside."""
        async with self._player_looping:
            while True:
                if self._queue.empty():
                    # Waits for the next track. If we don't get anything new within 5 minutes, disconnect from voice
                    try:
                        async with timeout(300):
                            await self._queue.wait()

                    except asyncio.TimeoutError:
                        break

                try:
                    # Plays the track
                    if not self._voice.is_connected():
                        await self._voice.connect(user_message.author.voice.channel)

                    if not self._queue.empty():
                        if not self._queue.is_looping_track():
                            track = self._queue.front()
                            await self._replace_status_message(user_message,
                                                               f'**Now playing *{track.name}*...**')

                        await self._queue.play(self._voice)

                except (discord.errors.ClientException, RuntimeError):
                    await user_message.channel.send(f'**Could not play track(s) due to voice connection error.**')
                    await self._queue.clear()
                    break

                except Exception:
                    self._logger.exception('Encountered exception when playing track:')

            # Leaves voice
            await self._voice.disconnect()

    async def _handle_attachments(self, user_message: discord.Message) -> List[AttachmentTrack]:
        """Returns an attachment tracklist from the given user message"""
        track_list = []
        for attachment in user_message.attachments:
            if not is_valid_attachment(attachment.filename):
                return []

            track_list.append(AttachmentTrack(attachment))

        return track_list

    async def _handle_youtube(self, url: str) -> List[YoutubeTrack]:
        """Returns a YouTube tracklist from the given url."""
        try:
            info = await asyncio.to_thread(ydl.extract_info, url, False)
        except Exception:
            self._logger.exception('Encountered an exception when getting YouTube video info:')
            return []

        if 'entries' in info:
            if 'playlist' in url:
                # Playlist link
                track_list = [YoutubeTrack(s['url']) for s in info['entries']]
            else:
                # YouTube video from a playlist
                track_list = [YoutubeTrack(url.split('&')[0])]

        else:
            # Regular Youtube video
            track_list = [YoutubeTrack(info['webpage_url'])]

        return track_list


    async def play(self, user_message: discord.Message) -> None:
        """Enqueues and plays videos/attachments given in the message."""
        if await self._sender_in_voice(user_message, send_warning=True):
            url = user_message.content[6:] if len(user_message.content) > 6 else ''
            if url != '' or user_message.attachments:
                playlist = False
                if user_message.attachments:
                    track_list = await self._handle_attachments(user_message)
                    list_len = len(track_list)
                    if list_len == 0:
                        await user_message.channel.send('**Error. One or more attachments is an invalid file type.**')
                        return
                    elif list_len > 1:
                        playlist = True
                        await self._replace_status_message(
                            user_message, f'**Enqueuing {list_len} attachments...**')

                else:
                    if is_valid_youtube(url):
                        await self._replace_status_message(user_message, '**Collecting info...**')
                        track_list = await self._handle_youtube(url)
                        list_len = len(track_list)
                        if list_len == 0:
                            await user_message.channel.send('**Error getting video(s).**')
                            return
                        elif list_len > 1:
                            playlist = True
                            await self._replace_status_message(
                                user_message, f'**Enqueueing {list_len} videos...**')

                    else:
                        await user_message.channel.send('**Not a valid link.**')
                        return

                if self._player_looping.locked() and not playlist:
                    await self._replace_status_message(user_message, '**Enqueueing new track...**')

                _ = asyncio.create_task(self._queue.enqueue(track_list))
                await self._queue.wait()

                # Starts the player if it hasn't been already
                if not self._player_looping.locked():
                    await self._player_loop(user_message)

            else:
                if url != '':
                    await user_message.channel.send('**Not a valid link/attachment.**')

                if self._queue.size() > 0:
                    # Starts the player if it got stopped unexpectedly
                    if not self._player_looping.locked():
                        await self._player_loop(user_message)

    @staticmethod
    async def help(user_message: discord.Message) -> None:
        """Sends a help message with all the bot's commands."""
        await user_message.channel.send(
            '**!help - list all commands\n'
            '!play [url] - play specified video(s) in the current voice channel\n'
            '!play (with attachment) - play attachment(s) in the current voice channel\n'
            '!pause - pause playback\n'
            '!resume - resume playback\n'
            '!skip - skip the current video\n'
            '!loop - loop the current track\n'
            '!queue - display the current contents of the queue\n'
            '!clear - clear the queue\n'
            '!leave - leave the current voice channel**')
