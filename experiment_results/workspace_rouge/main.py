# version: 1
import pygame
from core.event_bus import EventBus
from core.map_generator import MapGenerator
from entities.player import Player
from ui.message_log import MessageLog
from ui.hud import HUD

def main():
    # Initialize Pygame
    pygame.init()

    # Screen dimensions
    screen_width, screen_height = 800, 600
    screen = pygame.display.set_mode((screen_width, screen_height))
    pygame.display.set_caption("Abyssal Echoes")

    # Initialize EventBus
    event_bus = EventBus()

    # Initialize MapGenerator
    map_width, map_height = 50, 50
    room_min_size, room_max_size, max_rooms = 6, 10, 15
    map_generator = MapGenerator(map_width, map_height, room_min_size, room_max_size, max_rooms, event_bus)

    # Initialize UI components
    message_log = MessageLog(10, 500, 780, 90)
    hud = HUD(10, 10, 780, 50)

    # Subscribe UI components to EventBus
    event_bus.subscribe("log_message", message_log.add_message)
    event_bus.subscribe("update_hud", hud.update_stats)

    # Game state
    game_map = None
    player = None
    TILE_SIZE = 12

    # Generate the map
    def on_map_generated(data):
        nonlocal game_map, player
        # Initialize entities here
        rooms = data["rooms"]
        game_map = data["map"]
        player_start = rooms[0]  # Place player in the first room
        player_x, player_y = player_start[0] + 1, player_start[1] + 1
        # Corrected Player initialization: name, x, y, health
        player = Player("Hero", player_x, player_y, 100)

        # Log map generation
        event_bus.log_message("Map generated with {} rooms.".format(len(rooms)))

    event_bus.subscribe("map_generated", on_map_generated)
    map_generator.generate_map()

    # Main game loop
    running = True
    clock = pygame.time.Clock()

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if player and game_map:
                    dx, dy = 0, 0
                    if event.key == pygame.K_UP:
                        dy = -1
                    elif event.key == pygame.K_DOWN:
                        dy = 1
                    elif event.key == pygame.K_LEFT:
                        dx = -1
                    elif event.key == pygame.K_RIGHT:
                        dx = 1
                    
                    if dx != 0 or dy != 0:
                        new_x = player.x + dx
                        new_y = player.y + dy
                        # Check boundaries and collision
                        if 0 <= new_y < len(game_map) and 0 <= new_x < len(game_map[0]):
                            if game_map[new_y][new_x] == 0: # 0 is floor, 1 is wall
                                player.move(dx, dy)
                                event_bus.log_message(f"Player moved to ({player.x}, {player.y})")

        # Clear screen
        screen.fill((0, 0, 0))

        # Render Map
        if game_map:
            for y, row in enumerate(game_map):
                for x, tile in enumerate(row):
                    if tile == 1:
                        color = (100, 100, 100) # Wall
                    else:
                        color = (50, 50, 50) # Floor
                    pygame.draw.rect(screen, color, (x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE))

        # Render Player
        if player:
            pygame.draw.rect(screen, (0, 255, 0), (player.x * TILE_SIZE, player.y * TILE_SIZE, TILE_SIZE, TILE_SIZE))

        # Render UI components
        message_log.render(screen)
        hud.render(screen)

        # Update display
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()

if __name__ == "__main__":
    main()