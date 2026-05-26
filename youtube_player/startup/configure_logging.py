"""A module containing a function that sets up logging for the bot."""
import logging
import pathlib
import platformdirs
from logging import handlers

import youtube_player.config.parameters as params


def get_log_handler() -> logging.handlers.RotatingFileHandler:
    """A function sets up and returns the bot's logging handler."""
    logging.captureWarnings(True)
    log_directory = pathlib.Path(platformdirs.user_log_path(params.APP_NAME, appauthor=False))
    if not log_directory.is_dir():
        log_directory.mkdir(parents=True)

    handler = logging.handlers.RotatingFileHandler(
        filename=f'{log_directory}/{params.APP_NAME}.txt',
        encoding='utf-8',
        maxBytes=params.MAX_LOG_SIZE_BYTES,
        backupCount=params.MAX_LOG_ROLLOVERS)
    handler.setFormatter(logging.Formatter("{asctime} - {levelname} - {message}",
                                           style="{",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    return handler
