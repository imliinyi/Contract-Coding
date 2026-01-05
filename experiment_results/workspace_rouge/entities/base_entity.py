# version: 1
class BaseEntity:
    """
    A base class for all entities in the game.
    """

    def __init__(self, name: str, x: int, y: int):
        """
        Initialize the base entity.

        :param name: The name of the entity.
        :param x: The x-coordinate of the entity.
        :param y: The y-coordinate of the entity.
        """
        self.name = name
        self.x = x
        self.y = y

    def move(self, dx: int, dy: int):
        """
        Move the entity by a specified amount.

        :param dx: Change in x-coordinate.
        :param dy: Change in y-coordinate.
        """
        self.x += dx
        self.y += dy

    def __str__(self):
        return f"{self.name} at ({self.x}, {self.y})"