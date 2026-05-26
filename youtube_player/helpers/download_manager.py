"""A module containing a class that manages downloaded tracks for the bot, and its singleton instance."""
import asyncio
import logging
import os
from discord.ext import tasks


class FileInfo:
    """A class that keeps track of info for a downloaded track file."""
    def __init__(self, handle: str, file_name: str) -> None:
        self.handle = handle
        self.file_name = file_name
        self.references = 0


class DownloadManager:
    """A class that manages downloaded tracks for the bot."""
    def __init__(self):
        self._logger = logging.getLogger('discord')
        self._files = {}
        self._lock = asyncio.Lock()

    def get_file_name(self, handle: str) -> str:
        """Returns the name of the file associated with the given handle, if it exists."""
        if handle in self._files:
            return self._files[handle].file_name

        return ''

    async def increment(self, handle: str, file_name: str) -> None:
        """Increments the reference count for the file with the given handle, or adds it if it doesn't exist."""
        async with self._lock:
            if handle not in self._files:
                self._logger.debug(f"Adding file '{file_name}' with handle '{handle}' to download manager.")
                self._files[handle] = FileInfo(handle, file_name)

            self._logger.debug(f"Incrementing reference count for file with handle '{handle}'.")
            self._files[handle].references += 1

    async def decrement(self, handle: str) -> None:
        """Decrements the reference count for the file with the given handle."""
        async with self._lock:
            if handle in self._files:
                self._logger.debug(f"Decrementing reference count for file with handle '{handle}'.")
                self._files[handle].references -= 1

    @tasks.loop(seconds=5)
    async def cleanup(self):
        """Deletes files with no references periodically."""
        async with self._lock:
            # Getting a list of files with no references
            dead_files = []
            for handle in self._files:
                info = self._files[handle]
                if info.references == 0:
                    dead_files.append(info)

            # Deleting them
            for info in dead_files:
                self._logger.debug(f"Deleting file '{info.file_name}'.")
                del self._files[info.handle]
                try:
                    await asyncio.to_thread(os.remove, info.file_name)
                except Exception:
                    self._logger.exception(f"Encountered exception while deleting file '{info.file_name}':")


download_manager = DownloadManager()