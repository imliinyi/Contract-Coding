import collections.abc
import logging
import json
from typing import Any, Dict

from MetaFlow.utils.log import get_logger


def _deep_merge(d1: Dict, d2: Dict) -> Dict:
    """
    Recursively merges two dictionaries.
    Nested dictionaries are merged, lists are concatenated, and other values are overwritten.
    """
    for k, v in d2.items():
        if k in d1 and isinstance(d1[k], dict) and isinstance(v, collections.abc.Mapping):
            d1[k] = _deep_merge(d1[k], v)
        elif k in d1 and isinstance(d1[k], list) and isinstance(v, list):
            d1[k].extend(v)
        else:
            d1[k] = v
    return d1


class DocumentManager:
    """
    Manages a global, collaborative document for a single workflow run.
    Provides methods for agents to read, write, and modify shared knowledge.
    """
    def __init__(self):
        self._document: str = ""
        self.logger = get_logger()

    def get(self) -> str:
        """Returns a copy of the entire document with line numbers."""
        lines = self._document.split('\n')
        numbered_lines = [f"{i+1:3d}: {line}" for i, line in enumerate(lines)]
        return '\n'.join(numbered_lines)

    def execute_actions(self, actions: list):
        """
        Executes a list of document actions based on the new role-oriented model.

        :param actions: A list of action dictionaries, e.g.,
                        [
                          {"type": "add", "agent_name": "Frontend_Engineer", "content": "New UI component..."},
                          {"type": "update", "agent_name": "Backend_Engineer", "content": {"api_spec": ...}},
                        ]
        """
        if not isinstance(actions, list):
            return

        for action in actions:
            action_type = action.get("type")
            content = action.get("content", "")
            # Normalize content to string to avoid attribute errors when splitting
            if not isinstance(content, str):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)

            if action_type == "add":
                line = action.get("line")
                # Ensure line is a valid integer within bounds
                try:
                    line = int(line) if line is not None else 1
                except (ValueError, TypeError):
                    line = 1
                documents = self._document.split('\n')

                if not self._document.strip():
                    self._document = content
                    self.logger.info(f"Document was empty. Set content directly.")
                    continue

                if line < 1:
                    line = 1
                elif line > len(documents) + 1:
                    line = len(documents) + 1

                content = content.split('\n')
                documents[line - 1:line - 1] = content
                self._document = '\n'.join(documents)
                
                self.logger.info(f"Added content to document.")

            elif action_type == "update":
                start_line = action.get("start_line")
                end_line = action.get("end_line")
                # Ensure line numbers are valid integers within bounds
                try:
                    start_line = int(start_line) if start_line is not None else 1
                    end_line = int(end_line) if end_line is not None else len(self._document.split('\n'))
                except (ValueError, TypeError):
                    start_line, end_line = 1, len(self._document.split('\n'))

                if start_line < 1:
                    start_line = 1
                elif start_line > len(self._document.split('\n')):
                    start_line = len(self._document.split('\n'))

                if end_line < start_line:
                    end_line = start_line
                elif end_line > len(self._document.split('\n')):
                    end_line = len(self._document.split('\n'))

                documents = self._document.split('\n')
                documents[start_line - 1:end_line] = content.split('\n')
                self._document = '\n'.join(documents)
                
                self.logger.info(f"Updated (overwrote) document.")

            with open("document.txt", "w") as f:
                f.write(self._document)

            # elif action_type == "delete":
            #     if agent_name in self._document:
            #         del self._document[agent_name]
            #         logger.info(f"Deleted {agent_name}'s space.")
