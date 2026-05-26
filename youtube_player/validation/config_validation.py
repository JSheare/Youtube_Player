"""A module containing the pydantic model used to validate the bot's config file."""
import logging
import pydantic
from typing import Any, Annotated


def is_valid_log_level(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError('input should be a string corresponding to a log level.')

    level_mappings = logging.getLevelNamesMapping()
    value = value.upper()
    if value not in level_mappings:
        raise ValueError('input should be a string corresponding to a log level.')

    return level_mappings[value]


class BotModel(pydantic.BaseModel):
    log_level: Annotated[int, pydantic.BeforeValidator(is_valid_log_level)]
    discord_token : str
