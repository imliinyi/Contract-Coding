# version: 1
from entities.base_entity import BaseEntity

class Orc(BaseEntity):
    """
    A class representing an Orc enemy.
    """

    def __init__(self, name: str, x: int, y: int, strength: int):
        """
        Initialize the Orc.

        :param name: The name of the Orc.
        :param x: The x-coordinate of the Orc.
        :param y: The y-coordinate of the Orc.
        :param strength: The strength of the Orc.
        """
        super().__init__(name, x, y)
        self.strength = strength

    def attack(self, target):
        """
        Attack a target entity.

        :param target: The entity to attack.
        """
        if isinstance(target, BaseEntity):
            print(f"{self.name} attacks {target.name} with strength {self.strength}!")