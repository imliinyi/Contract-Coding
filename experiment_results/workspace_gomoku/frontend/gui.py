# version: 1
import tkinter as tk
from backend.game_logic import Game

class GUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("Gomoku")
        self.game = Game(size=15)  # Initialize a 15x15 Gomoku board
        self.cell_size = 40  # Size of each cell in pixels
        self.canvas = tk.Canvas(self.window, width=self.cell_size * 15, height=self.cell_size * 15)
        self.canvas.pack()
        self.draw_board()
        self.canvas.bind("<Button-1>", self.on_click)

    def draw_board(self):
        """Draws the game board grid."""
        for i in range(15):
            # Draw vertical lines
            self.canvas.create_line(i * self.cell_size, 0, i * self.cell_size, self.cell_size * 15, fill="black")
            # Draw horizontal lines
            self.canvas.create_line(0, i * self.cell_size, self.cell_size * 15, i * self.cell_size, fill="black")

        # Draw pieces based on the game board state
        for x in range(15):
            for y in range(15):
                if self.game.board[x][y] == 1:  # Player piece
                    self.canvas.create_oval(
                        x * self.cell_size + 5, y * self.cell_size + 5,
                        (x + 1) * self.cell_size - 5, (y + 1) * self.cell_size - 5,
                        fill="black"
                    )
                elif self.game.board[x][y] == 2:  # AI piece
                    self.canvas.create_oval(
                        x * self.cell_size + 5, y * self.cell_size + 5,
                        (x + 1) * self.cell_size - 5, (y + 1) * self.cell_size - 5,
                        fill="white"
                    )

    def on_click(self, event):
        """Handles user clicks on the board."""
        x = event.x // self.cell_size
        y = event.y // self.cell_size

        if self.game.make_move(x, y):
            self.draw_board()
            winner = self.game.check_win()
            if winner:
                self.display_winner(winner)
            else:
                ai_x, ai_y = self.game.ai.make_move(self.game.board)
                self.game.make_move(ai_x, ai_y)
                self.draw_board()
                winner = self.game.check_win()
                if winner:
                    self.display_winner(winner)

    def display_winner(self, winner):
        """Displays the winner in a popup window."""
        winner_text = "Player wins!" if winner == 1 else "AI wins!"
        popup = tk.Toplevel(self.window)
        popup.title("Game Over")
        tk.Label(popup, text=winner_text, font=("Arial", 16)).pack()
        tk.Button(popup, text="OK", command=self.window.quit).pack()

if __name__ == "__main__":
    gui = GUI()
    gui.window.mainloop()