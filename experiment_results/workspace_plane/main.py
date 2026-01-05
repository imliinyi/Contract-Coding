# version: 2
import pygame
from game.game_manager import GameManager

def main():
    # Initialize Pygame
    pygame.init()

    # Set up the game window
    screen = pygame.display.set_mode((800, 600))
    pygame.display.set_caption("Flying Battle Game")

    # Create an instance of GameManager
    game_manager = GameManager(screen)

    # Start the game loop
    game_manager.run_game_loop()

    # Quit Pygame
    pygame.quit()

if __name__ == "__main__":
    main()