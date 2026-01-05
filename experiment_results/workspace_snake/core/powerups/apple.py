# version: 5
# version: 4
from core.effect_interface import EffectInterface
from typing import Tuple

class Apple(EffectInterface):
    def __init__(self, position: Tuple[int, int]):
        """
        Initializes the Apple power-up.

        Args:
            position (Tuple[int, int]): The position of the Apple in the game.
        """
        self.position = position

    def apply(self, snake: 'Snake') -> None:
        """
        Applies the growth effect to the snake.

        Args:
            snake ('Snake'): The snake instance to apply the effect to.
        """
        snake.grow()