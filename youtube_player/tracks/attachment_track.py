"""A module containing a class for Discord attachments."""
import discord
import pathlib
import platformdirs

import youtube_player.config.parameters as params
from youtube_player.helpers.download_manager import download_manager
from youtube_player.helpers.helper_funcs import strip_extension
from youtube_player.tracks.track import Track


class AttachmentTrack(Track):
    def __init__(self, info: discord.Attachment) -> None:
        super().__init__(info)
        self._file_name = f'{platformdirs.user_data_dir(params.APP_NAME, appauthor=False)}/{self._info.filename}'

    def __del__(self):
        """Decrements the reference count of the track's associated file on object destruction."""
        if self._prepared:
            download_manager.decrement(self._file_name)

    async def prepare(self) -> None:
        file_name = download_manager.get_file_name(self._file_name)
        if file_name == '':
            await self._info.save(pathlib.Path(self._file_name))

        self.name = strip_extension(self._file_name)
        download_manager.increment(self._file_name, self._file_name)
        self._prepared = True

    def get_audio(self) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(self._file_name)
