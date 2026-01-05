# version: 3
# version: 3
import random
from typing import Tuple
from core.powerups.apple import Apple
from core.powerups.magnet import Magnet
from core.powerups.shield import Shield
from core.effect_interface import EffectInterface

class PowerUpFactory:
    """
    Factory class to create power-up objects based on the specified type.
    """

    def create_powerup(self, powerup_type: str, position: Tuple[int, int]) -> EffectInterface:
        """
        Creates a power-up object based on the specified type.

        Args:
            powerup_type (str): Type of power-up to create. Valid types are 'apple', 'magnet', and 'shield'.
            position (Tuple[int, int]): The position where the power-up will be placed.

        Returns:
            EffectInterface: An instance of the specified power-up.

        Raises:
            ValueError: If the power-up type is unsupported.
        """
        if powerup_type == 'apple':
            return Apple(position)
        elif powerup_type == 'magnet':
            return Magnet(position)
        elif powerup_type == 'shield':
            return Shield(position)
        else:
            raise ValueError(f"Unsupported power-up type: {powerup_type}")