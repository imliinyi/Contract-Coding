# version: 2
# version: 2
class EventBus:
    """
    A simple event bus implementation to handle publish-subscribe pattern.
    """

    def __init__(self):
        # Dictionary to store event subscriptions
        self.subscribers = {}

    def subscribe(self, event: str, callback):
        """
        Subscribe a callback function to a specific event.

        :param event: The name of the event to subscribe to.
        :param callback: The function to call when the event is published.
        """
        if event not in self.subscribers:
            self.subscribers[event] = []
        self.subscribers[event].append(callback)

    def publish(self, event: str, data: dict):
        """
        Publish an event with associated data to all subscribers.

        :param event: The name of the event to publish.
        :param data: A dictionary containing event-specific data.
        """
        if event in self.subscribers:
            for callback in self.subscribers[event]:
                callback(data)

    def log_message(self, message: str):
        """
        Publish a log message event.

        :param message: The message to log.
        """
        self.publish("log_message", {"message": message})

    def update_hud(self, stats: dict):
        """
        Publish an update HUD event.

        :param stats: A dictionary containing HUD stats to update.
        """
        self.publish("update_hud", stats)