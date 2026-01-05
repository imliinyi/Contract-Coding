# version: 3
import pygame
from game.bullet import Bullet

class Player:
    def __init__(self, x: int, y: int, speed: int):
        self.x = x
        self.y = y
        self.speed = speed
        self.width = 50  # Width of the player sprite
        self.height = 50  # Height of the player sprite
        self.color = (0, 255, 0)  # Green color for the player

    def move(self, direction: str) -> None:
        """
        Moves the player in the specified direction.

        Args:
            direction (str): The direction to move ('up', 'down', 'left', 'right').
        """
        if direction == 'up':
            self.y -= self.speed
        elif direction == 'down':
            self.y += self.speed
        elif direction == 'left':
            self.x -= self.speed
        elif direction == 'right':
            self.x += self.speed

        # Ensure the player stays within the screen bounds
        self.x = max(0, min(self.x, 800 - self.width))
        self.y = max(0, min(self.y, 600 - self.height))

    def fire_bullet(self) -> Bullet:
        """
        Fires a bullet from the player's current position.

        Returns:
            Bullet: A new bullet instance.
        """
        bullet_x = self.x + self.width // 2
        bullet_y = self.y
        bullet_speed = 10
        return Bullet(bullet_x, bullet_y, bullet_speed)

    def draw(self, screen):
        """
        Draws the player on the screen.

        Args:
            screen: The Pygame screen surface.
        """
        pygame.draw.rect(screen, self.color, (self.x, self.y, self.width, self.height))