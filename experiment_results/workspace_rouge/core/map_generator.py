# version: 4
import random
from core.event_bus import EventBus

class MapGenerator:
    def __init__(self, width, height, room_min_size, room_max_size, max_rooms, event_bus: EventBus):
        """
        Initialize the map generator with the given parameters.

        :param width: Width of the map.
        :param height: Height of the map.
        :param room_min_size: Minimum size of a room.
        :param room_max_size: Maximum size of a room.
        :param max_rooms: Maximum number of rooms to generate.
        :param event_bus: Instance of EventBus for publishing events.
        """
        self.width = width
        self.height = height
        self.room_min_size = room_min_size
        self.room_max_size = room_max_size
        self.max_rooms = max_rooms
        self.event_bus = event_bus

    def generate_map(self):
        """
        Generate a procedural map with rooms and tunnels.

        :return: A dictionary containing the map data.
        """
        if self.width < self.room_min_size or self.height < self.room_min_size:
            raise ValueError("Map dimensions are too small to fit even the smallest room.")

        map_data = [[1 for _ in range(self.width)] for _ in range(self.height)]  # Initialize map with walls
        rooms = []

        for _ in range(self.max_rooms):
            # Randomly determine room size and position
            room_width = random.randint(self.room_min_size, self.room_max_size)
            room_height = random.randint(self.room_min_size, self.room_max_size)
            x = random.randint(0, self.width - room_width - 1)
            y = random.randint(0, self.height - room_height - 1)

            new_room = (x, y, room_width, room_height)

            # Check for overlap with existing rooms
            if not self._does_overlap(new_room, rooms):
                self._create_room(map_data, new_room)
                if rooms:
                    # Connect the new room to the previous room with a tunnel
                    prev_x, prev_y, _, _ = rooms[-1]
                    self._create_tunnel(map_data, prev_x, prev_y, x, y)
                rooms.append(new_room)

        # Publish an event to notify that the map has been generated
        self.event_bus.publish("map_generated", {"map": map_data, "rooms": rooms})

        return {"map": map_data, "rooms": rooms}

    def _does_overlap(self, new_room, rooms, buffer=1):
        """
        Check if the new room overlaps with existing rooms, including a buffer zone.

        :param new_room: The new room to check.
        :param rooms: List of existing rooms.
        :param buffer: Minimum distance between rooms.
        :return: True if there is an overlap, False otherwise.
        """
        x, y, w, h = new_room
        for room in rooms:
            room_x, room_y, room_w, room_h = room
            if (x < room_x + room_w + buffer and x + w + buffer > room_x and
                y < room_y + room_h + buffer and y + h + buffer > room_y):
                return True
        return False

    def _create_room(self, map_data, room):
        """
        Carve out a rectangular room in the map.

        :param map_data: The map data to modify.
        :param room: The room dimensions (x, y, width, height).
        """
        x, y, w, h = room
        for i in range(y, y + h):
            for j in range(x, x + w):
                map_data[i][j] = 0  # 0 represents a floor

    def _create_tunnel(self, map_data, x1, y1, x2, y2):
        """
        Create a tunnel connecting two points using a more natural path.

        :param map_data: The map data to modify.
        :param x1: Starting x-coordinate.
        :param y1: Starting y-coordinate.
        :param x2: Ending x-coordinate.
        :param y2: Ending y-coordinate.
        """
        current_x, current_y = x1, y1

        while current_x != x2 or current_y != y2:
            if current_x != x2:
                current_x += 1 if current_x < x2 else -1
            elif current_y != y2:
                current_y += 1 if current_y < y2 else -1

            map_data[current_y][current_x] = 0  # 0 represents a floor

    def _create_horizontal_tunnel(self, map_data, x1, x2, y):
        """
        Create a horizontal tunnel.

        :param map_data: The map data to modify.
        :param x1: Starting x-coordinate.
        :param x2: Ending x-coordinate.
        :param y: The y-coordinate of the tunnel.
        """
        for x in range(min(x1, x2), max(x1, x2) + 1):
            map_data[y][x] = 0  # 0 represents a floor

    def _create_vertical_tunnel(self, map_data, y1, y2, x):
        """
        Create a vertical tunnel.

        :param map_data: The map data to modify.
        :param y1: Starting y-coordinate.
        :param y2: Ending y-coordinate.
        :param x: The x-coordinate of the tunnel.
        """
        for y in range(min(y1, y2), max(y1, y2) + 1):
            map_data[y][x] = 0  # 0 represents a floor