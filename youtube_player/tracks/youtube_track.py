"""A module containing a class for YouTube audio tracks."""
import asyncio
import discord

from youtube_player.helpers.downloader import ydl
from youtube_player.helpers.download_manager import download_manager
from youtube_player.helpers.helper_funcs import strip_extension
from youtube_player.tracks.track import Track


class YoutubeTrack(Track):
    """A class for YouTube audio tracks."""
    def __init__(self, info: str) -> None:
        super().__init__(info)
        self._file_name = ''

    def __del__(self):
        """Decrements the reference count of the track's associated file on object destruction."""
        if self._prepared:
            download_manager.decrement(self._info)

    async def prepare(self) -> None:
        file_name = download_manager.get_file_name(self._info)
        if file_name == '':
            file_name = ydl.prepare_filename(await asyncio.to_thread(ydl.extract_info, self._info))

        self._file_name = file_name
        self.name = strip_extension(self._file_name)
        download_manager.increment(self._info, self._file_name)
        self._prepared = True

    def get_audio(self) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(self._file_name)
