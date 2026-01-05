# version: 1
import pygame

class HUD:
    def __init__(self, x: int, y: int, width: int, height: int, font_size: int = 20):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font_size = font_size
        self.stats = {}
        self.font = pygame.font.Font(None, font_size)

    def update_stats(self, data: dict):
        self.stats = data

    def render(self, screen: pygame.Surface):
        y_offset = self.y
        for key, value in self.stats.items():
            text_surface = self.font.render(f"{key}: {value}", True, (255, 255, 255))
            screen.blit(text_surface, (self.x, y_offset))
            y_offset += self.font_size