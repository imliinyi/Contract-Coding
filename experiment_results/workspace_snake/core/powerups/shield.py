# version: 6
# version: 6
# Implementation of the Shield class

from core.effect_interface import EffectInterface
from typing import Tuple

class Shield(EffectInterface):
    def __init__(self, position: Tuple[int, int]) -> None:
        """
        Initializes the Shield effect.

        Args:
            position (Tuple[int, int]): The position of the Shield power-up in the game.
        """
        self.position = position
        self.active = False

    def apply(self, snake: 'Snake') -> None:
        """
        Applies the Shield effect to the snake, allowing it to pass through walls.
        
        Args:
            snake ('Snake'): The snake instance to apply the effect to.
        """
        self.active = True
        snake.enable_wall_clip()

    def _disable_effect(self, snake: 'Snake') -> None:
        """
        Disables the Shield effect on the snake.
        
        Args:
            snake ('Snake'): The snake instance to disable the effect on.
        """
        self.active = False
        snake.disable_wall_clip()