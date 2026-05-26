"""A module containing helper functions used by the bot."""


def is_valid_youtube(url: str) -> bool:
    return (
            len(url) >= 23 and url[:23] == 'https://www.youtube.com') or (
            len(url) >= 16 and url[:16] == 'https://youtu.be') or (
            len(url) >= 19 and url[:19] == 'https://youtube.com') or (
            len(url) >= 21 and url[:21] == 'https://m.youtube.com')


def is_valid_attachment(filename: str) -> bool:
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    if i < 0:
        return False

    extension = filename[i:]
    return (
            extension == '.mp3' or
            extension == '.mp4' or
            extension == '.wav' or
            extension == '.webm' or
            extension == '.mov')


def strip_extension(filename: str) -> str:
    i = len(filename) - 1
    while i >= 0:
        if filename[i] == '.':
            break

        i -= 1

    if i < 0:
        return ''

    return filename[:i]
