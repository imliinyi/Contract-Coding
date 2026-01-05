# version: 1
from entities.base_entity import BaseEntity

class Player(BaseEntity):
    """
    A class representing the player character.
    """

    def __init__(self, name: str, x: int, y: int, health: int):
        """
        Initialize the player.

        :param name: The name of the player.
        :param x: The x-coordinate of the player.
        :param y: The y-coordinate of the player.
        :param health: The health of the player.
        """
        super().__init__(name, x, y)
        self.health = health

    def take_damage(self, amount: int):
        """
        Reduce the player's health by a specified amount.

        :param amount: The amount of damage to take.
        """
        self.health -= amount
        if self.health < 0:
            self.health = 0

    def heal(self, amount: int):
        """
        Increase the player's health by a specified amount.

        :param amount: The amount of health to restore.
        """
        self.health += amount

    def __str__(self):
        return f"Player {self.name} at ({self.x}, {self.y}) with {self.health} health"