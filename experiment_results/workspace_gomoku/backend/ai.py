# version: 2
from typing import List, Tuple
import numpy as np

class AI:
    def __init__(self, difficulty: int):
        """
        Initializes the AI with a given difficulty level.

        :param difficulty: The difficulty level of the AI (1: Easy, 2: Medium, 3: Hard).
        """
        self.difficulty = difficulty

    def make_move(self, board: List[List[int]]) -> Tuple[int, int]:
        """
        Determines the AI's move based on the current board state.

        :param board: The current game board as a 2D list.
        :return: A tuple (x, y) representing the AI's move.
        """
        board_array = np.array(board)
        size = len(board)

        # Simple AI logic for demonstration purposes
        if self.difficulty == 1:
            return self._random_move(board_array)
        elif self.difficulty == 2:
            return self._defensive_move(board_array)
        elif self.difficulty == 3:
            return self._strategic_move(board_array)
        else:
            raise ValueError("Invalid difficulty level")

    def _random_move(self, board: np.ndarray) -> Tuple[int, int]:
        """
        Makes a random valid move.

        :param board: The current game board as a numpy array.
        :return: A tuple (x, y) representing the AI's move.
        """
        empty_positions = np.argwhere(board == 0)
        if len(empty_positions) == 0:
            raise ValueError("No valid moves available")
        move = empty_positions[np.random.choice(len(empty_positions))]
        return tuple(move)

    def _defensive_move(self, board: np.ndarray) -> Tuple[int, int]:
        """
        Makes a defensive move to block the opponent.

        :param board: The current game board as a numpy array.
        :return: A tuple (x, y) representing the AI's move.
        """
        # Placeholder for defensive logic
        return self._random_move(board)

    def _strategic_move(self, board: np.ndarray) -> Tuple[int, int]:
        """
        Makes a strategic move to maximize chances of winning.

        :param board: The current game board as a numpy array.
        :return: A tuple (x, y) representing the AI's move.
        """
        # Placeholder for strategic logic
        return self._random_move(board)