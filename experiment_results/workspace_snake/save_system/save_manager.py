# version: 2
import os
import json
from typing import List, Tuple

class SaveManager:
    def __init__(self, save_file_path: str):
        """
        Initializes the SaveManager with the path to the save file.

        :param save_file_path: Path to the save file where scores will be saved.
        """
        self.save_file_path = save_file_path

    def save_score(self, player_name: str, score: int) -> None:
        """
        Saves the player's score to the leaderboard.

        :param player_name: Name of the player.
        :param score: Score of the player.
        """
        leaderboard = self.load_leaderboard()
        leaderboard.append((player_name, score))
        leaderboard.sort(key=lambda x: (-x[1], x[0]))  # Sort by score descending, then name ascending

        try:
            with open(self.save_file_path, 'w') as file:
                json.dump(leaderboard, file)
        except IOError as e:
            print(f"Error saving score: {e}")

    def load_leaderboard(self) -> List[Tuple[str, int]]:
        """
        Loads and returns the leaderboard data.

        :return: A list of tuples containing player names and their scores.
        """
        if not os.path.exists(self.save_file_path):
            return []  # Return an empty leaderboard if the file doesn't exist

        try:
            with open(self.save_file_path, 'r') as file:
                return json.load(file)
        except (IOError, json.JSONDecodeError) as e:
            print(f"Error loading leaderboard: {e}")
            return []