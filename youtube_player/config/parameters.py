"""A module containing parameters used by the bot."""
import platformdirs

APP_NAME = 'youtube_player'
CONFIG_FILE = 'youtube_player.ini'
MAX_LOG_SIZE_BYTES = 10000000
MAX_LOG_ROLLOVERS = 5
DOWNLOAD_LOC = f'{platformdirs.user_data_dir(APP_NAME, appauthor=False)}'