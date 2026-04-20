import logging
import os


class PathFormatter(logging.Formatter):
    def format(self, record):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        relative_path = os.path.relpath(record.pathname, project_root)
        if relative_path.endswith('.py'):
            relative_path = relative_path[:-3]
        record.custom_path = relative_path.replace(os.path.sep, '.')
        return super().format(record)


def get_logger(log_path: str = "./agent.log", name: str = "MetaFlow"):
    logger = logging.getLogger(name)

    desired = os.path.abspath(log_path)

    if logger.hasHandlers():
        for h in list(logger.handlers):
            if isinstance(h, logging.FileHandler):
                if os.path.abspath(getattr(h, "baseFilename", "")) == desired:
                    return logger
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    formatter = PathFormatter('%(asctime)s - %(custom_path)s - %(levelname)s - %(message)s')

    os.makedirs(os.path.dirname(desired), exist_ok=True)

    file_handler = logging.FileHandler(desired, encoding='utf-8', mode='w')

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
