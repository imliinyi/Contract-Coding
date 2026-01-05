# version: 7
from typing import List
from core.buildings import Building

class Economy:
    def __init__(self, initial_money: int, initial_energy: int):
        """
        Initialize the Economy with starting money and energy.

        :param initial_money: The initial amount of money.
        :param initial_energy: The initial amount of energy.
        """
        self.money = initial_money
        self.energy = initial_energy

    def update_resources(self, buildings: List[Building]) -> None:
        """
        Updates Money and Energy based on the buildings in the city.

        :param buildings: List of Building objects in the city.
        """
        total_money_effect = 0
        total_energy_effect = 0

        for building in buildings:
            money_effect, energy_effect = building.get_resource_effect()
            total_money_effect += money_effect
            total_energy_effect += energy_effect

        self.money += total_money_effect
        self.energy += total_energy_effect

        # Ensure energy does not go below zero
        if self.energy < 0:
            self.energy = 0

    def __repr__(self):
        return f"Economy(money={self.money}, energy={self.energy})"