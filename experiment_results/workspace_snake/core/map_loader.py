# version: 3
# Implementation of the MapLoader class

import json
from typing import List, Tuple

class MapLoader:
    def load_map(self, map_name: str) -> List[Tuple[int, int]]:
        """
        Loads the specified map configuration and returns wall coordinates.

        Args:
            map_name (str): Name of the map file to load.

        Returns:
            List[Tuple[int, int]]: List of wall coordinates.

        Raises:
            FileNotFoundError: If the map file does not exist.
            ValueError: If the map file is improperly formatted.
        """
        try:
            with open(f"core/maps/{map_name}.json", "r") as file:
                data = json.load(file)
                walls = data.get("walls", [])
                if not isinstance(walls, list):
                    raise ValueError(f"Invalid format in {map_name}: 'walls' should be a list.")
                return walls
        except FileNotFoundError:
            raise FileNotFoundError(f"Map file {map_name} not found.")
        except json.JSONDecodeError:
            raise ValueError(f"Map file {map_name} is improperly formatted.")
