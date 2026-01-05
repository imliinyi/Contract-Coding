# version: 1
from core.event_bus import EventBus

class InventorySystem:
    """
    A system to manage the player's inventory.
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize the inventory system.

        :param event_bus: The event bus to use for publishing events.
        """
        self.event_bus = event_bus
        self.items = []

    def add_item(self, item: str):
        """
        Add an item to the inventory.

        :param item: The item to add.
        """
        self.items.append(item)
        self.event_bus.publish("item_added", {"item": item})

    def use_item(self, item: str):
        """
        Use an item from the inventory.

        :param item: The item to use.
        """
        if item in self.items:
            self.items.remove(item)
            self.event_bus.publish("item_used", {"item": item})