"""A module containing a class that wraps a Discord voice client."""
import asyncio
import discord

from youtube_player.tracks.track import Track


class Voice:
    """A class that wraps a Discord voice client."""
    def __init__(self) -> None:
        self._client = None
        self._lock = asyncio.Lock()
        self._busy = asyncio.Lock()
        self._track_finished = asyncio.Event()

    def is_connected(self) -> bool:
        """Returns True if the voice client is already connected."""
        if self._client is not None:
            return self._client.is_connected()

        return False

    async def connect(self, channel: discord.VoiceChannel) -> None:
        """Connects to the given voice channel."""
        async with self._lock:
            if self.is_connected():
                try:
                    await self._client.disconnect()
                except discord.errors.ClientException:
                    pass

            self._client = await channel.connect()

    async def disconnect(self) -> None:
        """Disconnects from the current voice channel."""
        async with self._lock:
            if self.is_connected():
                try:
                    await self._client.disconnect()
                except discord.errors.ClientException:
                    pass

            self._client = None

    async def play(self, track: Track):
        """Plays the given track in the current voice channel."""
        async with self._busy:
            async with self._lock:
                if self.is_connected():
                    loop = asyncio.get_event_loop()
                    self._client.play(track.get_audio(),
                                      after=lambda _: loop.call_soon_threadsafe(self._track_finished.set))
                else:
                    raise RuntimeError('voice client is not connected')

            await self._track_finished.wait()
            self._track_finished.clear()

    def is_playing(self) -> bool:
        """Returns True if the voice client is already playing a track."""
        if self.is_connected():
            return self._client.is_playing()
        else:
            raise RuntimeError('voice client is not connected')

    def is_paused(self) -> bool:
        """Returns True if the voice client is paused."""
        if self.is_connected():
            return self._client.is_paused()
        else:
            raise RuntimeError('voice client is not connected')

    async def pause(self) -> None:
        """Pauses playback if something is playing."""
        async with self._lock:
            if self.is_connected() and self._client.is_playing():
                self._client.pause()

    async def resume(self) -> None:
        """Resumes playback if it's paused."""
        async with self._lock:
            if self.is_connected() and self._client.is_paused():
                self._client.resume()

    async def stop(self) -> None:
        """Stops playback if something is playing."""
        async with self._lock:
            if self.is_connected() and (self._client.is_playing() or self._client.is_paused()):
                self._client.stop()
