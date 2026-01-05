# version: 3
# version: 2
import pygame
from game.player import Player
from game.enemy import Enemy
from game.bullet import Bullet

class GameManager:
    def __init__(self, screen):
        self.screen = screen
        self.clock = pygame.time.Clock()
        self.player = Player(400, 500, 5)
        self.enemies = [Enemy(100, 100, 3), Enemy(300, 100, 3), Enemy(500, 100, 3)]
        self.bullets = []
        self.enemy_bullets = []
        self.running = True

    def run_game_loop(self):
        while self.running:
            self.handle_events()
            self.update_game_state()
            self.render()
            self.clock.tick(60)

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

        keys = pygame.key.get_pressed()
        if keys[pygame.K_UP]:
            self.player.move("up")
        if keys[pygame.K_DOWN]:
            self.player.move("down")
        if keys[pygame.K_LEFT]:
            self.player.move("left")
        if keys[pygame.K_RIGHT]:
            self.player.move("right")
        if keys[pygame.K_SPACE]:
            self.bullets.append(self.player.fire_bullet())

    def update_game_state(self):
        for bullet in self.bullets:
            bullet.move()

        for enemy in self.enemies:
            enemy.move()
            if enemy.fire_bullet():
                self.enemy_bullets.append(enemy.fire_bullet())

        self.check_collisions()

    def render(self):
        self.screen.fill((0, 0, 0))
        # Render player, enemies, and bullets here
        self.player.draw(self.screen)
        for enemy in self.enemies:
            pygame.draw.rect(self.screen, (0, 0, 255), (enemy.x, enemy.y, 50, 50))
        for bullet in self.bullets:
            pygame.draw.rect(self.screen, (255, 255, 0), (bullet.x, bullet.y, 10, 10))
        for enemy_bullet in self.enemy_bullets:
            pygame.draw.rect(self.screen, (255, 0, 0), (enemy_bullet.x, enemy_bullet.y, 10, 10))
        pygame.display.flip()

    def check_collisions(self):
        # Check for collisions between player bullets and enemies
        for bullet in self.bullets[:]:
            for enemy in self.enemies[:]:
                if self.is_collision(bullet, enemy):
                    self.bullets.remove(bullet)
                    self.enemies.remove(enemy)
                    break

        # Check for collisions between enemy bullets and the player
        for bullet in self.enemy_bullets[:]:
            if self.is_collision(bullet, self.player):
                self.end_game()
                return

        # End game if all enemies are eliminated
        if not self.enemies:
            self.end_game()

    def is_collision(self, obj1, obj2):
        # Simple collision detection logic (bounding box)
        return (
            obj1.x < obj2.x + 50 and
            obj1.x + 50 > obj2.x and
            obj1.y < obj2.y + 50 and
            obj1.y + 50 > obj2.y
        )

    def end_game(self):
        self.running = False