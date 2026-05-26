"""A module containing a class that manages queued tracks."""
import asyncio
import logging
from collections import deque
from typing import List

from youtube_player.voice import Voice
from youtube_player.tracks.track import Track


class TrackQueue:
    """A class that manages the queued tracks, including enqueueing and playing."""
    def __init__(self) -> None:
        self._clearing_queue = asyncio.Event()

        self._enqueue_lock = asyncio.Lock()

        self._track_queue = deque()
        self._track_available = asyncio.Condition()
        self._track_lock = asyncio.Lock()
        self._looping_track = asyncio.Event()

    async def enqueue(self, track_list: List[Track]) -> None:
        """Enqueues the tracks in the given list."""
        async with self._enqueue_lock:
            if self._clearing_queue.is_set():
                return

            for track in track_list:
                if self._clearing_queue.is_set():
                    break

                try:
                    await track.prepare()
                except Exception:
                    logging.getLogger('discord').exception('Encountered exception when preparing track:')
                    continue

                await self._put_track(track)

    async def _put_track(self, track: Track) -> None:
        """Puts the given track on the queue."""
        async with self._track_lock:
            self._track_queue.append(track)
            async with self._track_available:
                self._track_available.notify_all()

    async def wait(self) -> None:
        """Blocks until the track queue contains a track."""
        if len(self._track_queue) == 0:
            async with self._track_available:
                await self._track_available.wait_for(lambda: len(self._track_queue) > 0)

    def front(self) -> str:
        """Returns the name of the track at the front of the queue."""
        if len(self._track_queue) > 0:
            return self._track_queue[0].name
        else:
            raise IndexError('cannot get the front of an empty queue')

    async def play(self, voice: Voice) -> None:
        """Plays the track that's currently at the front of the queue in the given voice channel."""
        async with self._track_lock:
            if len(self._track_queue) == 0:
                return
            else:
                track = self._track_queue.popleft()

        try:
            await voice.play(track)
        finally:
            if self._looping_track.is_set():
                async with self._track_lock:
                    self._track_queue.appendleft(track)

            else:
                await track.discard()

    def is_looping_track(self) -> bool:
        """Returns True if track looping is enabled, and False otherwise."""
        return self._looping_track.is_set()

    def toggle_track_looping(self) -> None:
        """Toggles track looping."""
        if self._looping_track.is_set():
            self._looping_track.clear()
        else:
            self._looping_track.set()

    async def get_tracklist(self, max_tracks: int = 0) -> List[str]:
        """Returns the list of tracks currently in the queue, optionally up to a specified maximum length."""
        tracks = []
        if max_tracks <= 0:
            max_tracks = float('inf')

        async with self._track_lock:
            for i in range(len(self._track_queue)):
                track = self._track_queue.popleft()
                if i < max_tracks:
                    tracks.append(track.name)

                self._track_queue.append(track)

        return tracks

    def empty(self) -> bool:
        """Returns True if the track queue is empty."""
        return len(self._track_queue) == 0

    def size(self) -> int:
        """Returns the current size of the track queue."""
        return len(self._track_queue)

    async def clear(self) -> None:
        """Clears the queue, including any currently-enqueued downloads."""
        if not self._clearing_queue.is_set():
            # The ordering of these is important
            self._clearing_queue.set()
            await self._enqueue_lock.acquire()
            await self._track_lock.acquire()
            try:
                while len(self._track_queue) > 0:
                    track = self._track_queue.popleft()
                    await track.discard()

            finally:
                # The ordering of these is also important
                self._clearing_queue.clear()
                self._track_lock.release()
                self._enqueue_lock.release()
