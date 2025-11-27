import codecs
import os
from typing import List

# Dynamically set WORKSPACE based on environment variable
# workspace_id = os.environ.get('WORKSPACE_ID')
# if workspace_id:
#     WORKSPACE = f"./workspace{workspace_id}"
# else:
#     WORKSPACE = "./workspace"
WORKSPACE = "./workspace"

# Create the workspace directory (if it doesn't exist)
if not os.path.exists(WORKSPACE):
    os.makedirs(WORKSPACE)

def _normalize_path(path: str) -> str:
    """
    Normalizes the path to prevent various path-related issues.
    
    :param path: The input path (could be relative, absolute, or contain various issues)
    :return: A normalized relative path suitable for workspace usage
    """
    if not path:
        return "."
    
    path = path.strip().strip("'\"")
    
    if path.startswith("./"):
        path = path[2:]
    
    if path.startswith("/"):
        path = path.lstrip("/")
    
    path = path.replace("\\", "/")
    
    while "//" in path:
        path = path.replace("//", "/")
    
    if not path or path == ".":
        return "."
    
    path = os.path.normpath(path)

    return path if path else "."

def _get_full_path(path: str) -> str:
    """
    Constructs a full path by appending the relative path to the workspace directory.
    
    :param path: The relative path to append to the workspace directory.
    :return: The full path to the file or directory in the workspace.
    """
    path = path or "."  
    path = _normalize_path(path)
    
    if os.path.isabs(path):
        abs_path = os.path.abspath(path)
        workspace_abs = os.path.abspath(WORKSPACE)
        if abs_path.startswith(workspace_abs):
            return abs_path
        else:
            raise ValueError(f"Absolute path '{path}' is outside the allowed workspace directory '{WORKSPACE}'.")
    
    normalized_path = os.path.normpath(path)
    
    if normalized_path.startswith('..'):
        raise ValueError(f"Path '{path}' attempts to access parent directories outside the workspace.")
    
    full_path = os.path.join(WORKSPACE, normalized_path)
    abs_path = os.path.abspath(full_path)
    workspace_abs = os.path.abspath(WORKSPACE)
    
    if not abs_path.startswith(workspace_abs):
        raise ValueError(f"Path '{path}' resolves to '{abs_path}', which is outside the allowed workspace directory '{WORKSPACE}'.")
        
    return abs_path

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
    try:
        full_path = _get_full_path(path)
    except ValueError as e:
        return str(e)
        
    def generate_tree(path: str, prefix: str = "", depth: int = 0):
        if depth >= max_depth:
            return []

        tree = []
        items = sorted(os.listdir(path))

        for index, item in enumerate(items):
            item_path = os.path.join(path, item)

            if item.startswith('.') or item in GLOBAL_GITIGNORE.splitlines() or item == 'workspace':
                continue

            is_last = index == len(items) - 1
            connector = "└── " if is_last else "├── "
            tree.append(
                f"{prefix}{connector}{item}{'/' if os.path.isdir(item_path) else ''}"
            )

            if os.path.isdir(item_path):
                extension = "    " if is_last else "│   "
                tree.extend(generate_tree(item_path, prefix + extension, depth + 1))
        return tree

    root_name = path if path != "." else os.path.basename(os.path.abspath(full_path))
    tree = [f"{root_name}/"] + generate_tree(full_path, depth=0)
    return "\n".join(tree)

file_tree.openai_schema = {
    "type": "function",
    "function": {
        "name": "file_tree",
        "description": "Generates a tree structure of the files and directories in the given path up to the specified depth. All paths are relative to the workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The relative path within the workspace to generate the tree structure for. Use '.' for workspace root."
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

def read_lines(path: str, start_line: int, end_line: int) -> List[str]:
    """
    Reads lines from a file within a specified range.

    :param path: The path to the file to read.
    :param start_line: The line number to start reading from (1-indexed).
    :param end_line: The line number to stop reading at (1-indexed).
    :return: A list of lines from the file within the specified range.
    """
    try:
        full_path = _get_full_path(path)
        start_line = max(1, start_line)
        end_line = max(start_line + 50, end_line)
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return lines[start_line-1:end_line]
    except FileNotFoundError:
        return f"Error: File not found at path: {path}"
    except Exception as e:
        return f"An error occurred while reading the file: {str(e)}"

read_lines.openai_schema = {
    "type": "function",
    "function": {
        "name": "read_lines",
        "description": "Reads lines from a file within a specified range. Path is relative to workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The relative path to the file within the workspace."
                },
                "start_line": {
                    "type": "integer",
                    "description": "The line number to start reading from (1-indexed)."
                },
                "end_line": {
                    "type": "integer",
                    "description": "The line number to stop reading at (1-indexed)."
                }
            },
            "required": ["path", "start_line", "end_line"]
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
        full_path = _get_full_path(path)
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"An error occurred while reading the file: {str(e)}"

read_file.openai_schema = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Reads the content of a file. Path is relative to workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The relative path to the file within the workspace."
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
        full_path = _get_full_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        content = content.replace('"', '\"')
        content = codecs.decode(content, 'unicode_escape')
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"File successfully written to {path}"
    except Exception as e:
        return f"An error occurred while writing to the file: {str(e)}"

write_file.openai_schema = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Writes content to a file. Creates the file if it does not exist, and overwrites it if it does. Path is relative.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The relative path to the file within the workspace. Will be created under workspace directory."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file. Should be properly escaped for JSON."
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
        full_path = _get_full_path(path)
        if not os.path.isdir(full_path):
            return f"Error: Path is not a directory: {path}"
        return os.listdir(full_path)
    except Exception as e:
        return f"An error occurred while listing the directory: {str(e)}"

list_directory.openai_schema = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "Lists the contents of a directory. Path is relative to workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The relative path to the directory within the workspace. Use '.' for workspace root."
                }
            },
            "required": ["path"]
        }
    }
}


def update_file_lines(file_path: str, start_line: int, end_line: int, new_content: str) -> str:
    """
    Updates or replaces a specific range of lines in a file with new content.

    :param file_path: The relative path to the file within the workspace.
    :param start_line: The 1-indexed line number where the replacement should begin.
    :param end_line: The 1-indexed line number where the replacement should end (inclusive).
    :param new_content: The new content to insert in place of the specified lines.
    :return: A string indicating the success or failure of the operation.
    """
    try:
        full_path = _get_full_path(file_path)
        if not os.path.isfile(full_path):
            return f"Error: Path '{file_path}' is not a file or does not exist."

        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Convert 1-indexed line numbers to 0-indexed list indices
        start_index = start_line - 1
        end_index = end_line - 1

        if not (0 <= start_index < len(lines)):
            return f"Error: start_line {start_line} is out of bounds for file with {len(lines)} lines."
        if not (0 <= end_index < len(lines)):
            return f"Error: end_line {end_line} is out of bounds for file with {len(lines)} lines."
        if start_index > end_index:
            return f"Error: start_line ({start_line}) cannot be greater than end_line ({end_line})."

        # Prepare the new content, splitting it into lines
        new_lines = new_content.splitlines(keepends=True)
        # If new_content is not empty and doesn't end with a newline, add one to the last line
        if new_lines and not new_lines[-1].endswith(('\n', '\r', '\r\n')):
            new_lines[-1] += '\n'


        # Reconstruct the file content
        new_file_content = lines[:start_index] + new_lines + lines[end_index + 1:]

        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(new_file_content)

        return f"Successfully updated lines {start_line} through {end_line} in file '{file_path}'."

    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"

update_file_lines.openai_schema = {
    "type": "function",
    "function": {
        "name": "update_file_lines",
        "description": "Updates or replaces a specific range of lines in a file with new content. Path is relative to the workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The relative path to the file within the workspace."
                },
                "start_line": {
                    "type": "integer",
                    "description": "The 1-indexed line number where the replacement should begin."
                },
                "end_line": {
                    "type": "integer",
                    "description": "The 1-indexed line number where the replacement should end (inclusive)."
                },
                "new_content": {
                    "type": "string",
                    "description": "The new content to insert. Each line in the string will replace a corresponding range of lines in the file."
                }
            },
            "required": ["file_path", "start_line", "end_line", "new_content"]
        }
    }
}