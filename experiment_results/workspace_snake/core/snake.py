# version: 5
from typing import List, Tuple
from core.effect_interface import EffectInterface

class Snake:
    def __init__(self, initial_body: List[Tuple[int, int]], initial_direction: Tuple[int, int], initial_speed: float):
        """
        Initializes the Snake instance.

        :param initial_body: List of tuples representing the initial body coordinates.
        :param initial_direction: A tuple (dx, dy) representing the initial movement direction.
        :param initial_speed: Movement speed of the snake.
        """
        self.body = initial_body
        self.direction = initial_direction
        self.speed = initial_speed

    def move(self) -> None:
        """
        Updates the snake's position based on its current direction and speed.
        """
        head_x, head_y = self.body[0]
        new_head = (head_x + self.direction[0] * self.speed, head_y + self.direction[1] * self.speed)
        self.body = [new_head] + self.body[:-1]

    def grow(self) -> None:
        """
        Increases the length of the snake by adding a new segment.
        """
        tail_x, tail_y = self.body[-1]
        self.body.append((tail_x, tail_y))

    def check_collision(self, walls: List[Tuple[int, int]]) -> bool:
        """
        Checks if the snake has collided with itself or the walls.

        :param walls: List of wall coordinates.
        :return: True if collision occurs, False otherwise.
        """
        head = self.body[0]
        # Check collision with walls
        if head in walls:
            return True
        # Check collision with itself
        if head in self.body[1:]:
            return True
        return False

    def apply_effect(self, effect: 'EffectInterface') -> None:
        """
        Applies the effect of a power-up to the snake.

        :param effect: An object implementing the EffectInterface.
        """
        effect.apply(self)