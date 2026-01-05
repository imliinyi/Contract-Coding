# version: 3
class Bullet:
    def __init__(self, x: int, y: int, speed: int):
        """
        Initialize a bullet with position and speed.
        :param x: Horizontal position of the bullet.
        :param y: Vertical position of the bullet.
        :param speed: Speed of the bullet.
        """
        self.x = x
        self.y = y
        self.speed = speed

    def move(self) -> None:
        """
        Move the bullet vertically based on its speed.
        """
        self.y -= self.speed