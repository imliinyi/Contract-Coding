# version: 1
from core.event_bus import EventBus

class InteractionSystem:
    """
    A system to handle interactions between entities and the game world.
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize the interaction system.

        :param event_bus: The event bus to use for publishing events.
        """
        self.event_bus = event_bus

    def interact(self, entity, action: str):
        """
        Handle an interaction involving an entity.

        :param entity: The entity performing the interaction.
        :param action: The action to perform.
        """
        self.event_bus.publish("interaction", {"entity": entity, "action": action})