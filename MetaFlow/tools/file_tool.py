import os
from typing import List

GLOBAL_GITIGNORE = """
# Ignore git files
.git

# Ignore macOS system files
.DS_Store

# Ignore temporary files
*.swp
*.swo
*.tmp
*.temp

# Ignore IDE settings
.idea/
.vscode/

# Ignore log files
*.log

# Ignore node_modules
node_modules/

# Ignore build files
build/
dist/

# Ignore Python files
__pycache__
__pycache__/
wheels/
*.egg-info
*.py[oc]
.venv
"""


def file_tree(path: str, max_depth: int = 3) -> str:
    """
    Generates a tree structure of the files and directories in the given path up to the specified depth.

    :param path: The path to the directory to generate the tree structure for.
    :param max_depth: The maximum depth to traverse in the directory structure. Default is 3.
    :return: A string representing the tree structure of the directory.
    """
    def generate_tree(path: str, prefix: str = "", depth: int = 0):
        if depth >= max_depth:
            return []

        tree = []
        items = sorted(os.listdir(path))

        for index, item in enumerate(items):
            full_path = os.path.join(path, item)

            if item.startswith('.') or item in GLOBAL_GITIGNORE.splitlines():
                continue

            is_last = index == len(items) - 1
            connector = "└── " if is_last else "├── "
            tree.append(
                f"{prefix}{connector}{item}{'/' if os.path.isdir(full_path) else ''}"
            )

            if os.path.isdir(full_path):
                extension = "    " if is_last else "│   "
                tree.extend(generate_tree(full_path, prefix + extension, depth + 1))
        return tree

    root_name = os.path.basename(os.path.abspath(path))
    tree = [f"{root_name}/"] + generate_tree(path, depth=0)
    return "\n".join(tree)

file_tree.openai_schema = {
    "type": "function",
    "function": {
        "name": "file_tree",
        "description": "Generates a tree structure of the files and directories in the given path up to the specified depth.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the directory to generate the tree structure for."
                },
                "max_depth": {
                    "type": "integer",
                    "description": "The maximum depth to traverse in the directory structure. Default is 3.",
                    "default": 3
                }
            },
            "required": ["path"]
        }
    }
}

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