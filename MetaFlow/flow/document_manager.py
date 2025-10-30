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
        self._document: Dict[str, Any] = {}

    def get(self) -> Dict[str, Any]:
        """Returns a copy of the entire document."""
        return self._document.copy()

    def execute_actions(self, actions: list):
        """
        Executes a list of document actions based on the new role-oriented model.

        :param actions: A list of action dictionaries, e.g.,
                        [
                          {"type": "add", "agent_name": "Frontend_Engineer", "content": "New UI component..."},
                          {"type": "update", "agent_name": "Backend_Engineer", "content": {"api_spec": ...}},
                          {"type": "delete", "agent_name": "Old_Agent"}
                        ]
        """
        if not isinstance(actions, list):
            return

        for action in actions:
            action_type = action.get("type")
            agent_name = action.get("agent_name")
            content = action.get("content")

            if not agent_name:
                continue

            if action_type == "add":
                if agent_name not in self._document or self._document[agent_name] is None:
                    self._document[agent_name] = content
                elif isinstance(self._document.get(agent_name), dict) and isinstance(content, dict):
                    self._document[agent_name] = _deep_merge(self._document[agent_name], content)
                elif isinstance(self._document.get(agent_name), str) and isinstance(content, str):
                    self._document[agent_name] += "\n" + content
                else:
                    # Fallback for incompatible types, treat as update
                    self._document[agent_name] = content
                logger.info(f"Added content to {agent_name}'s space.")

            elif action_type == "update":
                self._document[agent_name] = content
                logger.info(f"Updated (overwrote) {agent_name}'s space.")

            elif action_type == "delete":
                if agent_name in self._document:
                    del self._document[agent_name]
                    logger.info(f"Deleted {agent_name}'s space.")
