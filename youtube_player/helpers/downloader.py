"""A module containing the singleton yt_dlp downloader used by the bot."""
from yt_dlp import YoutubeDL

import youtube_player.config.parameters as params


ydl = YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist', 'extract_audio': True, 'format': 'bestaudio',
                 'outtmpl': f'{params.DOWNLOAD_LOC}/%(title)s.mp3'})
ydl.add_default_info_extractors()
