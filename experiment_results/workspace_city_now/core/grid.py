# version: 4
# version: 4
from typing import List, Optional
from core.buildings import Building

class Grid:
    """
    Represents the game grid, which is a 2D array of cells where buildings can be placed.
    """

    def __init__(self, width: int, height: int):
        """
        Initializes the grid with the specified width and height.

        :param width: The number of columns in the grid.
        :param height: The number of rows in the grid.
        """
        self.cells = [[None for _ in range(width)] for _ in range(height)]

    def get_all_buildings(self) -> List[Building]:
        """
        Retrieves all buildings currently placed on the grid.

        :return: A list of all buildings on the grid.
        """
        return [building for row in self.cells for building in row if building is not None]

    def get__allbuilding(self, x: int, y: int) -> Optional[Building]:
        """
        Retrieves the building at the specified coordinates.

        :param x: The x-coordinate of the cell.
        :param y: The y-coordinate of the cell.
        :return: The building at the specified coordinates, or None if the cell is empty.
        """
        return self.cells[y][x] if 0 <= x < len(self.cells[0]) and 0 <= y < len(self.cells) else None

    def place_building(self, building: Building) -> bool:
        """
        Places a building on the grid if the cell is empty and adjacency rules are satisfied.

        :param building: The building to place.
        :return: True if the building was placed successfully, False otherwise.
        """
        x, y = building.x, building.y

        # Check if the coordinates are within bounds
        if not (0 <= x < len(self.cells[0]) and 0 <= y < len(self.cells)):
            return False

        # Check if the cell is empty
        if self.cells[y][x] is not None:
            return False

        # Validate adjacency rules for Roads
        if building.type == "Road":
            if not self._has_adjacent_road_or_building(x, y):
                return False

        # Place the building on the grid
        self.cells[y][x] = building
        return True

    def _has_adjacent_road_or_building(self, x: int, y: int) -> bool:
        """
        Checks if there is at least one adjacent Road or other building to the given coordinates.

        :param x: The x-coordinate of the cell.
        :param y: The y-coordinate of the cell.
        :return: True if there is an adjacent Road or building, False otherwise.
        """
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]  # Left, Right, Up, Down

        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            if 0 <= nx < len(self.cells[0]) and 0 <= ny < len(self.cells):
                adjacent_building = self.cells[ny][nx]
                if adjacent_building is not None and adjacent_building.type in ("Road", "House", "Power Plant"):
                    return True

        return False