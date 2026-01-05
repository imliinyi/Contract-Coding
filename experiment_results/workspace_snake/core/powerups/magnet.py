# version: 3
# version: 3
from core.effect_interface import EffectInterface
from core.snake import Snake
from typing import Tuple

class Magnet(EffectInterface):
    """
    Magnet power-up pulls nearby power-ups towards the snake.
    """

    def __init__(self, position: Tuple[int, int]) -> None:
        """
        Initializes the Magnet power-up.

        Args:
            position (Tuple[int, int]): The position of the Magnet power-up in the game.
        """
        self.position = position

    def apply(self, snake: Snake) -> None:
        """
        Pulls nearby power-ups towards the snake.

        Args:
            snake (Snake): The snake instance to which the effect is applied.

        Note:
            This implementation assumes that we have a way to determine nearby power-ups
            and their movement toward the snake. The actual implementation of this logic
            depends on the game engine and power-up system.
        """
        # Pseudo implementation for attracting power-ups:
        # For every power-up in the game:
        #     If the power-up is within a certain radius from the snake:
        #         Move the power-up closer to the snake.

        print("Magnet effect applied to the snake. Nearby power-ups are pulled towards the snake.")