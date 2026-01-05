# version: 1
import pygame
from pygame import Surface

class HUD:
    """
    Handles the display of resources and alerts on the screen.
    """

    def __init__(self, money: int, energy: int):
        """
        Initializes the HUD with the given money and energy values.

        :param money: Current amount of money to display.
        :param energy: Current amount of energy to display.
        """
        self.money = money
        self.energy = energy
        self.font = None
        self.low_energy_alert = False
        self.alert_timer = 0

    def load_assets(self):
        """
        Loads the required font for the HUD.
        """
        pygame.font.init()
        self.font = pygame.font.Font(None, 36)  # Default font, size 36

    def update(self, money: int, energy: int):
        """
        Updates the HUD with the latest resource values.

        :param money: Updated money value.
        :param energy: Updated energy value.
        """
        self.money = money
        self.energy = energy

        # Check if energy is low and enable alert if necessary
        self.low_energy_alert = self.energy < 50

    def render(self, screen: Surface) -> None:
        """
        Renders the HUD on the screen.

        :param screen: The Pygame Surface to render the HUD on.
        """
        if not self.font:
            self.load_assets()

        # Render Money
        money_text = self.font.render(f"Money: {self.money}", True, (255, 255, 255))
        screen.blit(money_text, (10, 10))

        # Render Energy
        energy_color = (255, 0, 0) if self.low_energy_alert else (255, 255, 255)
        energy_text = self.font.render(f"Energy: {self.energy}", True, energy_color)
        screen.blit(energy_text, (10, 50))

        # Render Low Energy Alert if applicable
        if self.low_energy_alert:
            self.alert_timer += 1
            if (self.alert_timer // 30) % 2 == 0:  # Flashing effect every 30 frames
                alert_text = self.font.render("Low Energy!", True, (255, 0, 0))
                screen.blit(alert_text, (10, 90))