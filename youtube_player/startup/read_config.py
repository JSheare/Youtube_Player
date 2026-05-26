"""A module containing functions that read the bot's config file."""
import configparser
import pathlib
import platformdirs

import youtube_player.config.parameters as params


def get_config_template() -> configparser.ConfigParser:
    """A helper function that returns the bot's config file template as a ConfigParser instance."""
    template_file = f'{pathlib.Path(__file__).parent.parent}/config/{params.CONFIG_FILE}'
    template_config = configparser.ConfigParser()
    template_config.read(template_file)
    return template_config


def read_config(update_user_config: bool = True) -> configparser.ConfigParser:
    """A function that creates and reads the bot's config file and returns it as a ConfigParser instance."""

    # Reading the user config file, or making one if it doesn't exist
    directory = platformdirs.user_config_path(params.APP_NAME, appauthor=False)
    user_file = f'{directory}/{params.CONFIG_FILE}'
    if not pathlib.Path(user_file).is_file():
        directory_path = pathlib.Path(directory)
        if not directory_path.is_dir():
            directory_path.mkdir(parents=True)

        template_config = get_config_template()
        with open(user_file, 'w') as user_file:
            template_config.write(user_file)

        return template_config

    else:
        user_config = configparser.ConfigParser()
        user_config.read(user_file)
        if update_user_config:
            template_config = get_config_template()
            needs_update = False
            # Checking to see if the user config is missing necessary sections or options
            for section in template_config.sections():
                if user_config.has_section(section):
                    for option in template_config.options(section):
                        if not user_config.has_option(section, option):
                            needs_update = True
                            break

                    if needs_update:
                        break

                else:
                    needs_update = True
                    break

            # Checking to see if the user config has old sections or options
            if not needs_update:  # No sense in doing this check if the file already needs an update
                for section in user_config.sections():
                    if template_config.has_section(section):
                        for option in user_config.options(section):
                            if not template_config.has_option(section, option):
                                needs_update = True
                                break

                        if needs_update:
                            break

                    else:
                        needs_update = True
                        break

            if needs_update:
                # Copying out user-modified data before writing
                for section in user_config.sections():
                    if template_config.has_section(section):
                        for option in user_config.options(section):
                            if template_config.has_option(section, option):
                                template_config.set(section, option, user_config.get(section, option))

                with open(user_file, 'w') as user_file:
                    template_config.write(user_file)

                return template_config

        return user_config
