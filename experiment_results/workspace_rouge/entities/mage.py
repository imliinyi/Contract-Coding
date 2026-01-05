# version: 1
from entities.base_entity import BaseEntity

class Mage(BaseEntity):
    """
    A class representing a Mage enemy.
    """

    def __init__(self, name: str, x: int, y: int, mana: int):
        """
        Initialize the Mage.

        :param name: The name of the Mage.
        :param x: The x-coordinate of the Mage.
        :param y: The y-coordinate of the Mage.
        :param mana: The mana of the Mage.
        """
        super().__init__(name, x, y)
        self.mana = mana

    def cast_spell(self, target):
        """
        Cast a spell on a target entity.

        :param target: The entity to cast a spell on.
        """
        if isinstance(target, BaseEntity):
            print(f"{self.name} casts a spell on {target.name} using {self.mana} mana!")