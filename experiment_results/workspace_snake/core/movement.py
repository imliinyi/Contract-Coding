# version: 7
# Implementation of the Movement class

class Movement:
    def change_direction(self, current_direction: tuple[int, int], new_direction: tuple[int, int]) -> tuple[int, int]:
        """
        Changes the snake's direction, ensuring it does not reverse.
        
        Args:
            current_direction (tuple[int, int]): The current direction of the snake.
            new_direction (tuple[int, int]): The new direction to change to.

        Returns:
            tuple[int, int]: The updated direction.
        """
        if current_direction[0] + new_direction[0] == 0 and current_direction[1] + new_direction[1] == 0:
            # If the new direction is directly opposite to the current direction, ignore it.
            return current_direction
        return new_direction
