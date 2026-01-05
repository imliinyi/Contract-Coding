# version: 2
from backend.ai import AI

class Game:
    def __init__(self, size: int):
        """
        Initializes the game board.

        :param size: The size of the game board (size x size).
        """
        self.size = size
        self.board = [[0 for _ in range(size)] for _ in range(size)]  # 0 represents an empty cell
        self.current_player = 1  # Player 1 starts the game
        self.ai = AI(1)  # Initialize the AI agent

    def make_move(self, x: int, y: int) -> bool:
        """
        Updates the board with a move.

        :param x: The x-coordinate of the move.
        :param y: The y-coordinate of the move.
        :return: True if the move is valid and successful, False otherwise.
        """
        if 0 <= x < self.size and 0 <= y < self.size and self.board[x][y] == 0:
            self.board[x][y] = self.current_player
            self.current_player = 3 - self.current_player  # Switch player: 1 -> 2, 2 -> 1
            return True
        return False

    def check_win(self) -> int:
        """
        Checks if there is a winner.

        :return: The player number who won (1 or 2), or 0 if no winner yet.
        """
        # Check rows, columns, and diagonals for a win
        for i in range(self.size):
            for j in range(self.size):
                if self.board[i][j] != 0:
                    # Check horizontal
                    if j + 4 < self.size and all(self.board[i][j + k] == self.board[i][j] for k in range(5)):
                        return self.board[i][j]
                    # Check vertical
                    if i + 4 < self.size and all(self.board[i + k][j] == self.board[i][j] for k in range(5)):
                        return self.board[i][j]
                    # Check diagonal (top-left to bottom-right)
                    if i + 4 < self.size and j + 4 < self.size and all(self.board[i + k][j + k] == self.board[i][j] for k in range(5)):
                        return self.board[i][j]
                    # Check diagonal (top-right to bottom-left)
                    if i + 4 < self.size and j - 4 >= 0 and all(self.board[i + k][j - k] == self.board[i][j] for k in range(5)):
                        return self.board[i][j]
        return 0  # No winner yet