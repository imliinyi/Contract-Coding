# version: 3
from typing import Tuple

class Building:
    def __init__(self, x: int, y: int, type: str):
        """
        Initialize a building with its position and type.

        :param x: X-coordinate on the grid.
        :param y: Y-coordinate on the grid.
        :param type: Type of the building (e.g., 'House', 'Power Plant').
        """
        self.x = x
        self.y = y
        self.type = type

    def get_resource_effect(self) -> Tuple[int, int]:
        """
        Returns the Money and Energy effect of the building based on its type.

        :return: A tuple (money_effect, energy_effect) where:
                 - money_effect: Positive for income, negative for expense.
                 - energy_effect: Positive for energy generation, negative for consumption.
        """
        if self.type == 'House':
            # Houses generate money but consume energy
            return (10, -5)
        elif self.type == 'Power Plant':
            # Power Plants generate energy but cost money to maintain
            return (-200, 20)
        elif self.type == 'Road':
            # Roads have no effect on resources
            return (0, 0)
        else:
            # Default case for undefined building types
            return (0, 0)