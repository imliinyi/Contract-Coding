# version: 12
# version: 3
import pygame
from config import MAP_DIRECTORY, SAVE_FILE_PATH, SNAKE_INITIAL_SPEED, POWERUP_SPAWN_INTERVAL
from core.snake import Snake
from core.movement import Movement
from core.map_loader import MapLoader
from save_system.save_manager import SaveManager
from core.powerups.powerup_factory import PowerUpFactory
from core.ui_manager import UIManager
import time

def main() -> None:
    """
    Entry point for the game. Initializes components, starts the game loop,
    and manages interactions between systems.
    """
    pygame.init()

    # Screen dimensions
    screen_width, screen_height = 800, 600
    screen = pygame.display.set_mode((screen_width, screen_height))

    # Initialize components
    snake = Snake([(100, 100)], (1, 0), SNAKE_INITIAL_SPEED)
    movement = Movement()
    map_loader = MapLoader()
    save_manager = SaveManager(SAVE_FILE_PATH)
    powerup_factory = PowerUpFactory()
    ui_manager = UIManager(screen_width, screen_height)

    walls = map_loader.load_map("box_map")
    powerups = []
    last_powerup_spawn_time = time.time()

    clock = pygame.time.Clock()
    running = True
    score = 0

    while running:
        # Handle events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_UP:
                    snake.direction = movement.change_direction(snake.direction, (0, -1))
                elif event.key == pygame.K_DOWN:
                    snake.direction = movement.change_direction(snake.direction, (0, 1))
                elif event.key == pygame.K_LEFT:
                    snake.direction = movement.change_direction(snake.direction, (-1, 0))
                elif event.key == pygame.K_RIGHT:
                    snake.direction = movement.change_direction(snake.direction, (1, 0))

        # Spawn power-ups at intervals
        if time.time() - last_powerup_spawn_time > POWERUP_SPAWN_INTERVAL:
            powerup_type = "apple"  # Example, could be randomized
            powerup_position = (screen_width // 2, screen_height // 2)  # Temporary example position
            powerups.append(powerup_factory.create_powerup(powerup_type, powerup_position))
            last_powerup_spawn_time = time.time()

        # Update snake position
        snake.move()

        # Check collisions
        if snake.check_collision(walls):
            running = False

        # Check power-up interactions
        for powerup in powerups[:]:
            if snake.body[0] == powerup.position:
                powerup.apply(snake)
                powerups.remove(powerup)
                score += 10  # Example scoring

        # Render game state
        ui_manager.render_game_state(snake, powerups, walls, score)

        # Cap the frame rate
        clock.tick(10)

    # Save score and display leaderboard
    player_name = input("Enter your name: ")
    save_manager.save_score(player_name, score)
    leaderboard = save_manager.load_leaderboard()
    ui_manager.render_leaderboard(leaderboard)

    pygame.quit()

if __name__ == "__main__":
    main()