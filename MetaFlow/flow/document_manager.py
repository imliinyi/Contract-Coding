import collections.abc
from enum import Enum
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union



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

    def _get_nested_item(self, data: Dict, path: str) -> Tuple[Optional[Dict], Optional[str]]:
        """Helper to navigate to a nested dictionary path. Returns the parent dict and the final key."""
        keys = path.split('.')
        current_level = data
        for key in keys[:-1]:
            current_level = current_level.get(key)
            if not isinstance(current_level, dict):
                return None, None
        return current_level, keys[-1]

    def execute_actions(self, actions: List[Dict[str, Any]]):
        """
        Executes a list of document actions provided by an agent.

        :param actions: A list of action dictionaries.
                        Each action is a dict with 'type' and other parameters.
                        e.g.,
                        [
                          {"type": "update", "data": {"key": "value"}},
                          {"type": "delete", "path": "key.to.delete"},
                          {"type": "delete_by_pattern", "pattern": "temp_.*"}
                        ]
        """
        if not isinstance(actions, list):
            return

        for action in actions:
            action_type = action.get("type")
            
            if action_type == "update":
                data_to_update = action.get("data", {})
                if isinstance(data_to_update, dict):
                    self._document = _deep_merge(self._document, data_to_update)
            
            elif action_type == "set":
                path = action.get("path")
                value = action.get("value")
                if path:
                    target, key = self._get_nested_item(self._document, path)
                    if isinstance(target, dict) and key:
                        target[key] = value

            elif action_type == "delete":
                path = action.get("path")
                if path:
                    target, key = self._get_nested_item(self._document, path)
                    if isinstance(target, dict) and key and key in target:
                        del target[key]
            
            elif action_type == "delete_by_pattern":
                pattern = action.get("pattern")
                if pattern:
                    # This implementation only supports top-level key deletion by pattern for safety and simplicity.
                    # A full recursive search-and-delete could be implemented if needed.
                    try:
                        keys_to_delete = [k for k in self._document.keys() if re.match(pattern, k)]
                        for k in keys_to_delete:
                            del self._document[k]
                    except re.error:
                        # Ignore invalid regex patterns from the LLM
                        pass