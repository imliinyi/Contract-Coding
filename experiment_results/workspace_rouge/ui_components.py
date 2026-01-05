# version: 1
# Message Log UI Component
import pygame
from core.event_bus import EventBus

class MessageLog:
    def __init__(self, x, y, width, height, font_size=20):
        pygame.font.init()
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font = pygame.font.Font(None, font_size)
        self.messages = []
        self.max_messages = height // font_size
        self.event_bus = EventBus()
        self.event_bus.subscribe("log_message", self.add_message)

    def add_message(self, data):
        message = data.get("message", "")
        if message:
            self.messages.append(message)
            if len(self.messages) > self.max_messages:
                self.messages.pop(0)

    def render(self, screen):
        y_offset = self.y
        for message in self.messages:
            text_surface = self.font.render(message, True, (255, 255, 255))
            screen.blit(text_surface, (self.x, y_offset))
            y_offset += self.font.get_height()

# HUD UI Component
class HUD:
    def __init__(self, x, y, width, height, font_size=20):
        pygame.font.init()
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font = pygame.font.Font(None, font_size)
        self.stats = {}
        self.event_bus = EventBus()
        self.event_bus.subscribe("update_hud", self.update_stats)

    def update_stats(self, data):
        self.stats = data

    def render(self, screen):
        y_offset = self.y
        for key, value in self.stats.items():
            text_surface = self.font.render(f"{key}: {value}", True, (255, 255, 255))
            screen.blit(text_surface, (self.x, y_offset))
            y_offset += self.font.get_height()