# version: 9
import pygame
from core.grid import Grid
from core.economy import Economy
from core.hud import HUD
from core.buildings import Building

class Game:
    def __init__(self):
        """Initializes the game components."""
        self.grid = Grid(800, 600)
        self.economy = Economy(initial_money=1000, initial_energy=500)
        self.hud = HUD(money=self.economy.money, energy=self.economy.energy)
        self.running = True

    def run(self):
        """Runs the game loop, coordinating all components."""
        pygame.init()
        screen = pygame.display.set_mode((800, 600))
        pygame.display.set_caption("Micro City Sim")
        clock = pygame.time.Clock()

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_mouse_click(event.pos)

            # Update economy based on current grid state
            all_buildings = self.grid.get_all_buildings()
            self.economy.update_resources(all_buildings)

            # Update and render HUD
            self.hud.update(self.economy.money, self.economy.energy)
            screen.fill((0, 0, 0))  # Clear the screen
            self.hud.render(screen)
            for building in all_buildings:
                pygame.draw.rect(screen, (0, 255, 0), pygame.Rect(building.x * 32, building.y * 32, 32, 32))

            # Update the display
            pygame.display.flip()
            clock.tick(60)

        pygame.quit()

    def handle_mouse_click(self, position):
        """Handles mouse clicks for building placement."""
        grid_x, grid_y = self.get_grid_coordinates(position)
        building_type = self.get_selected_building_type()

        if building_type:
            building = Building(x=grid_x, y=grid_y, type=building_type)
            if self.grid.place_building(building):
                print(f"Placed {building_type} at ({grid_x}, {grid_y})")
            else:
                print(f"Failed to place {building_type} at ({grid_x}, {grid_y})")

    def get_grid_coordinates(self, position):
        """Converts screen coordinates to grid coordinates."""
        x, y = position
        grid_x = x // 32  # Assuming each grid cell is 32x32 pixels
        grid_y = y // 32
        return grid_x, grid_y

    def get_selected_building_type(self):
        """Returns the currently selected building type."""
        # Placeholder for building selection logic
        # Replace this with actual UI integration
        return "House"