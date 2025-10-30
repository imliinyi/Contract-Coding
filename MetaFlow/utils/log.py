import logging
import os

from MetaFlow.config import Config


class PathFormatter(logging.Formatter):
    def format(self, record):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        relative_path = os.path.relpath(record.pathname, project_root)
        if relative_path.endswith('.py'):
            relative_path = relative_path[:-3]
        record.custom_path = relative_path.replace(os.path.sep, '.')
        return super().format(record)


def get_logger(log_path: str = Config.LOG_PATH, name: str = "MetaFlow"):
    logger = logging.getLogger(name)

    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    formatter = PathFormatter('%(asctime)s - %(custom_path)s - %(levelname)s - %(message)s')

    # Create log directory if it doesn't exist
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding='utf-8', mode='w')

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger