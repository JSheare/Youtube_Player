"""A module containing the singleton yt_dlp downloader used by the bot."""
import platformdirs
from yt_dlp import YoutubeDL

import youtube_player.config.parameters as params


ydl = YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist', 'extract_audio': True, 'format': 'bestaudio',
                 'outtmpl': f'{platformdirs.user_data_dir(params.APP_NAME, appauthor=False)}/%(title)s.mp3'})
ydl.add_default_info_extractors()
