# version: 3
import pygame
from core.game import Game

class Main:
    """
    Main class to initialize and run the game.
    """
    def run(self) -> None:
        """
        Initializes the game and starts the main loop.
        """
        # Initialize Pygame
        pygame.init()

        # Set up the screen
        screen = pygame.display.set_mode((800, 600))
        pygame.display.set_caption("Micro City Simulator")

        # Initialize the game instance
        game = Game()

        # Start the game loop
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            # Run the game logic
            game.run()

            # Update the display
            pygame.display.flip()

        # Quit Pygame
        pygame.quit()



if __name__ == "__main__":
    main = Main()
    main.run()