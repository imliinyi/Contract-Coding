# version: 3
from game.bullet import Bullet

class Enemy:
    def __init__(self, x: int, y: int, speed: int):
        """
        Initialize an enemy with position and speed.
        :param x: Horizontal position of the enemy.
        :param y: Vertical position of the enemy.
        :param speed: Speed of the enemy.
        """
        self.x = x
        self.y = y
        self.speed = speed

    def move(self) -> None:
        """
        Move the enemy horizontally based on its speed.
        """
        self.x += self.speed

    def fire_bullet(self) -> Bullet:
        """
        Fire a bullet from the enemy's current position.
        :return: A Bullet object.
        """
        return Bullet(self.x, self.y, 10)