# version: 1
import pygame

class MessageLog:
    def __init__(self, x: int, y: int, width: int, height: int, font_size: int = 20):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font_size = font_size
        self.messages = []
        self.font = pygame.font.Font(None, font_size)

    def add_message(self, data: dict):
        message = data.get("message", "")
        self.messages.append(message)
        if len(self.messages) > self.height // self.font_size:
            self.messages.pop(0)

    def render(self, screen: pygame.Surface):
        for i, message in enumerate(self.messages):
            text_surface = self.font.render(message, True, (255, 255, 255))
            screen.blit(text_surface, (self.x, self.y + i * self.font_size))