import collections.abc
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


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
            content = action.get("content")

            if action_type == "add":
                line = action.get("line")
                lines = self._document.split('\n')

                if line < 1:
                    line = 1
                elif line > len(lines) + 1:
                    line = len(lines) + 1

                if not self._document.strip():
                    self._document = content
                else:
                    content = content.split('\n')
                    lines.insert(line - 1, *content)
                    self._document = '\n'.join(lines)
                
                logger.info(f"Added content to document.")

            elif action_type == "update":
                self._document = content
                logger.info(f"Updated (overwrote) document.")

            # elif action_type == "delete":
            #     if agent_name in self._document:
            #         del self._document[agent_name]
            #         logger.info(f"Deleted {agent_name}'s space.")
