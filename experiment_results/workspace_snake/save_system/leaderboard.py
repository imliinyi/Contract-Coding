# version: 3
from typing import List, Tuple

class Leaderboard:
    """
    Leaderboard class to calculate and manage player rankings.
    """

    def calculate_rank(self, scores: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
        """
        Calculates and returns the leaderboard ranking based on scores.

        Args:
            scores (List[Tuple[str, int]]): A list of tuples where each tuple contains a player's name and their score.

        Returns:
            List[Tuple[str, int]]: A sorted list of tuples representing the leaderboard with the highest scores first.
        """
        # Sort scores in descending order of score values; in case of a tie, sort alphabetically by player name.
        sorted_scores = sorted(scores, key=lambda x: (-x[1], x[0]))
        return sorted_scores