# version: 1
import pygame
from typing import List, Tuple

class UIManager:
    def __init__(self, screen_width: int, screen_height: int):
        """
        Initializes the UIManager with a Pygame screen.
        :param screen_width: Width of the game window.
        :param screen_height: Height of the game window.
        """
        pygame.init()
        self.screen = pygame.display.set_mode((screen_width, screen_height))
        pygame.display.set_caption("Snake Grand-Master")
        self.font = pygame.font.Font(None, 36)  # Default font for score and leaderboard.
        self.clock = pygame.time.Clock()

    def render_game_state(self, snake: 'Snake', powerups: List['EffectInterface'], walls: List[Tuple[int, int]], score: int) -> None:
        """
        Renders the current game state, including the snake, power-ups, walls, and score.
        :param snake: Snake object representing the player.
        :param powerups: List of power-ups to display on the screen.
        :param walls: List of wall coordinates.
        :param score: Current score of the player.
        """
        self.screen.fill((0, 0, 0))  # Clear the screen with black.

        # Draw the snake.
        for segment in snake.body:
            pygame.draw.rect(self.screen, (0, 255, 0), (segment[0], segment[1], 20, 20))

        # Draw the power-ups.
        for powerup in powerups:
            pygame.draw.rect(self.screen, (255, 255, 0), (powerup.position[0], powerup.position[1], 20, 20))  # Assuming power-ups have a draw method.

        # Draw the walls.
        for wall in walls:
            pygame.draw.rect(self.screen, (255, 0, 0), (wall[0], wall[1], 20, 20))

        # Display the score.
        score_text = self.font.render(f"Score: {score}", True, (255, 255, 255))
        self.screen.blit(score_text, (10, 10))

        pygame.display.flip()  # Update the display.
        self.clock.tick(30)  # Cap the frame rate to 30 FPS.

    def render_leaderboard(self, leaderboard: List[Tuple[str, int]]) -> None:
        """
        Displays the leaderboard.
        :param leaderboard: List of tuples containing player names and scores.
        """
        self.screen.fill((0, 0, 0))  # Clear the screen with black.

        title_text = self.font.render("Leaderboard", True, (255, 255, 255))
        self.screen.blit(title_text, (10, 10))

        y_offset = 50  # Start position for leaderboard entries.
        for rank, (name, score) in enumerate(leaderboard, start=1):
            entry_text = self.font.render(f"{rank}. {name}: {score}", True, (255, 255, 255))
            self.screen.blit(entry_text, (10, y_offset))
            y_offset += 30

        pygame.display.flip()  # Update the display.
        self.clock.tick(30)  # Cap the frame rate to 30 FPS.