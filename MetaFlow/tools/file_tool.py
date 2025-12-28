import ast
import codecs
import os
import re
from typing import Callable, List


class WorkspaceFS:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        os.makedirs(self.workspace_dir, exist_ok=True)

    def _normalize_path(self, path: str) -> str:
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

        workspace_prefix = os.path.basename(self.workspace_dir).rstrip("/") + "/"
        if path.startswith(workspace_prefix):
            path = path[len(workspace_prefix):]
        if path.startswith("workspace/"):
            path = path[len("workspace/"):]

        return os.path.normpath(path) or "."

    def resolve(self, path: str) -> str:
        path = path or "."
        path = self._normalize_path(path)

        if os.path.isabs(path):
            abs_path = os.path.abspath(path)
            if abs_path.startswith(self.workspace_dir):
                return abs_path
            raise ValueError(
                f"Absolute path '{path}' is outside the allowed workspace directory '{self.workspace_dir}'."
            )

        normalized_path = os.path.normpath(path)
        if normalized_path.startswith(".."):
            raise ValueError(f"Path '{path}' attempts to access parent directories outside the workspace.")

        abs_path = os.path.abspath(os.path.join(self.workspace_dir, normalized_path))
        if not abs_path.startswith(self.workspace_dir):
            raise ValueError(
                f"Path '{path}' resolves to '{abs_path}', which is outside the allowed workspace directory '{self.workspace_dir}'."
            )
        return abs_path

    def file_tree(self, path: str, max_depth: int = 3) -> str:
        full_path = self.resolve((path or ".").replace("workspace/", ""))

        ignore_names = {
            ".git",
            ".DS_Store",
            ".idea",
            ".vscode",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            ".venv",
        }

        def generate_tree(dir_path: str, prefix: str = "", depth: int = 0) -> List[str]:
            if depth >= max_depth:
                return []

            items = sorted(os.listdir(dir_path))
            lines: List[str] = []
            visible_items = [i for i in items if not i.startswith(".") and i not in ignore_names]

            for idx, item in enumerate(visible_items):
                item_path = os.path.join(dir_path, item)
                is_last = idx == len(visible_items) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{item}{'/' if os.path.isdir(item_path) else ''}")
                if os.path.isdir(item_path):
                    extension = "    " if is_last else "│   "
                    lines.extend(generate_tree(item_path, prefix + extension, depth + 1))

            return lines

        root_name = path if path and path != "." else os.path.basename(full_path)
        tree = [f"{root_name}/"] + generate_tree(full_path, depth=0)
        return "\n".join(tree)

    def read_lines(self, path: str, start_line: int, end_line: int) -> List[str] | str:
        try:
            full_path = self.resolve(path)
            start_line = max(1, start_line)
            end_line = max(start_line + 50, end_line)
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return lines[start_line - 1 : end_line]
        except FileNotFoundError:
            return f"Error: File not found at path: {path}"
        except Exception as e:
            return f"An error occurred while reading the file: {str(e)}"

    def read_file(self, path: str) -> str:
        try:
            full_path = self.resolve(path)
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"An error occurred while reading the file: {str(e)}"

    def write_file(self, path: str, content: str) -> str:
        try:
            full_path = self.resolve(path)
            if ".md" in path:
                return "Error: Don't write markdown files directly. Use a code editor instead."
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            version = 1
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    first_line = f.readline()
                    match = re.match(r"# version: (\d+)", first_line)
                    if match:
                        version = int(match.group(1)) + 1

            version_comment = f"# version: {version}\n"
            content = version_comment + (content or "")
            content = content.replace('"', '\\"')
            content = codecs.decode(content, "unicode_escape")
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"File successfully written to {path}"
        except Exception as e:
            return f"An error occurred while writing to the file: {str(e)}"

    def list_directory(self, path: str) -> List[str] | str:
        try:
            full_path = self.resolve(path)
            if not os.path.isdir(full_path):
                return f"Error: Path is not a directory: {path}"
            return os.listdir(full_path)
        except Exception as e:
            return f"An error occurred while listing the directory: {str(e)}"

    def update_file_lines(self, file_path: str, start_line: int, end_line: int, new_content: str) -> str:
        try:
            full_path = self.resolve(file_path)
            if not os.path.isfile(full_path):
                return f"Error: Path '{file_path}' is not a file or does not exist."

            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_index = start_line - 1
            end_index = end_line - 1

            if not (0 <= start_index < len(lines)):
                return f"Error: start_line {start_line} is out of bounds for file with {len(lines)} lines."
            if not (0 <= end_index < len(lines)):
                return f"Error: end_line {end_line} is out of bounds for file with {len(lines)} lines."
            if start_index > end_index:
                return f"Error: start_line ({start_line}) cannot be greater than end_line ({end_line})."

            new_lines = (new_content or "").splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith(("\n", "\r", "\r\n")):
                new_lines[-1] += "\n"

            new_file_content = lines[:start_index] + new_lines + lines[end_index + 1 :]

            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(new_file_content)

            return f"Successfully updated lines {start_line} through {end_line} in file '{file_path}'."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

    def add_code(self, path: str, line: int, content: str) -> str:
        try:
            full_path = self.resolve(path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            insert_text = codecs.decode((content or "").replace('"', '\\"'), "unicode_escape")
            if insert_text and not insert_text.endswith(("\n", "\r", "\r\n")):
                insert_text += "\n"

            if not os.path.exists(full_path):
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(insert_text)
                return f"File created and content inserted into '{path}' at line 1."

            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            target_idx = max(0, line - 1)
            target_idx = min(target_idx, len(lines))

            new_lines = lines[:target_idx] + [insert_text] + lines[target_idx:]

            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            return f"Successfully inserted content into '{path}' at line {target_idx + 1}."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

    def code_outline(self, file_path: str) -> str:
        if file_path.endswith(".py"):
            return self._python_outline(file_path)
        raise ValueError(f"Unsupported code file extension: {file_path}. Use `read_file()` instead.")

    def _python_outline(self, file_path: str) -> str:
        try:
            full_path = self.resolve(file_path)
            with open(full_path, "r", encoding="utf-8") as file:
                file_content = file.read()

            tree = ast.parse(file_content)
            outline: List[str] = []

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    class_range = (
                        f"[{node.lineno}, {node.end_lineno}]" if hasattr(node, "end_lineno") else f"[{node.lineno}, ?]"
                    )
                    outline.append(f"Class: {node.name}, line_range={class_range}")
                    for class_member in node.body:
                        if isinstance(class_member, ast.FunctionDef):
                            method_range = (
                                f"[{class_member.lineno}, {class_member.end_lineno}]"
                                if hasattr(class_member, "end_lineno")
                                else f"[{class_member.lineno}, ?]"
                            )
                            outline.append(f"  Method: {class_member.name}, line_range={method_range}")
                        elif isinstance(class_member, ast.Assign):
                            for target in class_member.targets:
                                if isinstance(target, ast.Name):
                                    attr_range = (
                                        f"[{class_member.lineno}, {class_member.end_lineno}]"
                                        if hasattr(class_member, "end_lineno")
                                        else f"[{class_member.lineno}, ?]"
                                    )
                                    outline.append(f"  Attribute: {target.id}, line_range={attr_range}")
                elif isinstance(node, ast.FunctionDef):
                    func_range = (
                        f"[{node.lineno}, {node.end_lineno}]" if hasattr(node, "end_lineno") else f"[{node.lineno}, ?]"
                    )
                    outline.append(f"Function: {node.name}, line_range={func_range}")
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            var_range = (
                                f"[{node.lineno}, {node.end_lineno}]" if hasattr(node, "end_lineno") else f"[{node.lineno}, ?]"
                            )
                            outline.append(f"Variable: {target.id}, line_range={var_range}")

            return "\n".join(outline)
        except FileNotFoundError:
            return f"Error: File not found at path '{file_path}'"
        except Exception as e:
            return f"Error: {str(e)}"


def build_file_tools(workspace_dir: str) -> List[Callable]:
    fs = WorkspaceFS(workspace_dir)

    def file_tree(path: str, max_depth: int = 3) -> str:
        return fs.file_tree(path, max_depth=max_depth)

    file_tree.openai_schema = {
        "type": "function",
        "function": {
            "name": "file_tree",
            "description": "Generates a tree structure of the files and directories in the given path up to the specified depth. All paths are relative to the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The relative path within the workspace to generate the tree structure for. Use '.' for workspace root."},
                    "max_depth": {"type": "integer", "description": "The maximum depth to traverse in the directory structure. Default is 3.", "default": 3},
                },
                "required": ["path"],
            },
        },
    }

    def read_lines(path: str, start_line: int, end_line: int):
        return fs.read_lines(path, start_line, end_line)

    read_lines.openai_schema = {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": "Reads lines from a file within a specified range. Path is relative to workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The relative path to the file within the workspace."},
                    "start_line": {"type": "integer", "description": "The line number to start reading from (1-indexed)."},
                    "end_line": {"type": "integer", "description": "The line number to stop reading at (1-indexed)."},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    }

    def read_file(path: str) -> str:
        return fs.read_file(path)

    read_file.openai_schema = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the content of a file. Path is relative to workspace directory.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "The relative path to the file within the workspace."}}, "required": ["path"]},
        },
    }

    def write_file(path: str, content: str) -> str:
        return fs.write_file(path, content)

    write_file.openai_schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes content to a file. Creates the file if it does not exist, and overwrites it if it does. Path is relative.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The relative path to the file within the workspace. Will be created under workspace directory."},
                    "content": {"type": "string", "description": "The content to write to the file. Should be properly escaped for JSON."},
                },
                "required": ["path", "content"],
            },
        },
    }

    def list_directory(path: str):
        return fs.list_directory(path)

    list_directory.openai_schema = {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Lists the contents of a directory. Path is relative to workspace directory.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "The relative path to the directory within the workspace. Use '.' for workspace root."}}, "required": ["path"]},
        },
    }

    def update_file_lines(file_path: str, start_line: int, end_line: int, new_content: str) -> str:
        return fs.update_file_lines(file_path, start_line, end_line, new_content)

    update_file_lines.openai_schema = {
        "type": "function",
        "function": {
            "name": "update_file_lines",
            "description": "Updates or replaces a specific range of lines in a file with new content. Path is relative to the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "The relative path to the file within the workspace."},
                    "start_line": {"type": "integer", "description": "The 1-indexed line number where the replacement should begin."},
                    "end_line": {"type": "integer", "description": "The 1-indexed line number where the replacement should end (inclusive)."},
                    "new_content": {"type": "string", "description": "The new content to insert. Each line in the string will replace a corresponding range of lines in the file."},
                },
                "required": ["file_path", "start_line", "end_line", "new_content"],
            },
        },
    }

    def add_code(path: str, line: int, content: str) -> str:
        return fs.add_code(path, line, content)

    add_code.openai_schema = {
        "type": "function",
        "function": {
            "name": "add_code",
            "description": "Insert code into a file at a specified 1-indexed line using only path, line, and content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace."},
                    "line": {"type": "integer", "description": "1-indexed line number where the content will be inserted."},
                    "content": {"type": "string", "description": "Code/content to insert; unicode-escape decoded; newline ensured."},
                },
                "required": ["path", "line", "content"],
            },
        },
    }

    def code_outline(file_path: str) -> str:
        return fs.code_outline(file_path)

    code_outline.openai_schema = {
        "type": "function",
        "function": {
            "name": "code_outline",
            "description": "Generates a structured outline of a code file, including classes, methods, functions, and variables.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "The relative path to the code file within the workspace."}}, "required": ["file_path"]},
        },
    }

    return [file_tree, write_file, list_directory, read_file, read_lines, update_file_lines, add_code, code_outline]
