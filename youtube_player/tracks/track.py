"""A module containing an abstract base class for all audio tracks."""
import discord
from abc import ABC, abstractmethod
from typing import Any


class Track(ABC):
    """An abstract base class for all audio tracks."""
    def __init__(self, info: Any) -> None:
        self.name = ''
        self._info = info
        self._prepared = False

    @abstractmethod
    async def prepare(self) -> None:
        """Does any necessary prep work for the track to be played."""
        pass

    @abstractmethod
    def get_audio(self) -> type[discord.AudioSource]:
        """Returns the track as an AudioSource."""
        pass

    @abstractmethod
    async def discard(self) -> None:
        """Discard the track and do necessary teardown work."""
        pass
