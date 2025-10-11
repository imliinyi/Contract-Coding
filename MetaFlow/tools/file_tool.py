import os
from typing import List

def read_file(path: str) -> str:
    """
    Reads the content of a file.

    :param path: The path to the file to read.
    :return: The content of the file.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File not found at path: {path}"
    except Exception as e:
        return f"An error occurred while reading the file: {str(e)}"

read_file.openai_schema = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Reads the content of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to read."
                }
            },
            "required": ["path"]
        }
    }
}

def write_file(path: str, content: str) -> str:
    """
    Writes content to a file. Creates the file if it does not exist, and overwrites it if it does.

    :param path: The path to the file to write to.
    :param content: The content to write to the file.
    :return: A success or error message.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"File successfully written to {path}"
    except Exception as e:
        return f"An error occurred while writing to the file: {str(e)}"

write_file.openai_schema = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Writes content to a file. Creates the file if it does not exist, and overwrites it if it does.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to write to."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file."
                }
            },
            "required": ["path", "content"]
        }
    }
}

def list_directory(path: str) -> List[str]:
    """
    Lists the contents of a directory.

    :param path: The path to the directory to list.
    :return: A list of the contents of the directory.
    """
    try:
        if not os.path.isdir(path):
            return f"Error: Path is not a directory: {path}"
        return os.listdir(path)
    except FileNotFoundError:
        return f"Error: Directory not found at path: {path}"
    except Exception as e:
        return f"An error occurred while listing the directory: {str(e)}"

list_directory.openai_schema = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "Lists the contents of a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the directory to list."
                }
            },
            "required": ["path"]
        }
    }
}