## Product Requirement Document (PRD)

### 1.1 Project Overview
The goal of this project is to develop a relaxing grid-based city builder game in Python/Pygame. The game will focus on resource balancing, allowing players to build and manage a virtual city while balancing resources like Money and Energy. The game will be modular and extensible, ensuring maintainability and ease of adding new features.

### 1.2 User Stories (Features)
* **Economy Loop:**
  * Houses generate Money (Tax) but consume Energy.
  * Power Plants generate Energy but cost Money to build.
  * If Energy is low, Houses stop paying taxes.
* **Building System:**
  * Players can select different structures from a menu and place them on a grid.
  * The system should support Roads, Residential Zones, and Industrial Zones.
  * The system should be designed to allow easy addition of new building types.
* **Visual Feedback:**
  * A HUD displays current resources (Money and Energy).
  * Alerts notify the player when Energy is low.
  * The map is visually clean and intuitive.

### 1.3 Constraints
* **Tech Stack:** Python, Pygame
* **Standards:** PEP8 coding standard, modular and maintainable code structure

## Technical Architecture Document (System Design)

### 2.1 Directory Structure
```
workspace/
├── main.py
├── core/
│   ├── economy.py
│   ├── buildings.py
│   ├── grid.py
│   ├── hud.py
│   ├── game.py
├── assets/
│   ├── images/
│   ├── fonts/
│   ├── sounds/
```

### 2.2 Global Shared Knowledge
* **CONSTANTS:**
  * INITIAL_MONEY: 1000
  * INITIAL_ENERGY: 500
  * HOUSE_TAX: 10
  * HOUSE_ENERGY_CONSUMPTION: 5
  * POWER_PLANT_ENERGY_OUTPUT: 20
  * POWER_PLANT_COST: 200

### 2.3 Dependency Relationships(MUST):
* `main.py` depends on `game.py` to initialize and run the game loop.
* `game.py` coordinates `economy.py`, `buildings.py`, `grid.py`, and `hud.py`.
* `economy.py` handles the resource calculations and interactions with `buildings.py`.
* `buildings.py` defines the structure and behavior of different building types.
* `grid.py` manages the grid and building placements, including adjacency rules.
* `hud.py` handles the display of resources and alerts, including flashing warnings for low energy levels.

### 2.4 Symbolic API Specifications
**File:** `main.py`
* **Class:** `Main`
    * **Methods:**
        * `def run(self) -> None:`
            + Docstring: Initializes the game and starts the main loop.
* **Owner:** Backend_Engineer
* **Version:** 3
* **Status:** VERIFIED

**File:** `core/economy.py`
* **Class:** `Economy`
    * **Attributes:**
        * `money: int` - Current amount of money.
        * `energy: int` - Current amount of energy.
    * **Methods:**
        * `def update_resources(self, buildings: List[Building]) -> None:`
            + Docstring: Updates Money and Energy based on the buildings in the city.
            + Details: Should iterate through buildings, summing their resource effects, and update the economy's money and energy attributes accordingly.
* **Owner:** Backend_Engineer
* **Version:** 3
* **Status:** VERIFIED

**File:** `core/buildings.py`
* **Class:** `Building`
    * **Attributes:**
        * `x: int` - X-coordinate on the grid.
        * `y: int` - Y-coordinate on the grid.
        * `type: str` - Type of building (e.g., 'House', 'Power Plant').
    * **Methods:**
        * `def get_resource_effect(self) -> Tuple[int, int]:`
            + Docstring: Returns the Money and Energy effect of the building.
            + Details: Implements logic for each building type (House generates money and consumes energy; Power Plant generates energy and costs money).
* **Owner:** Backend_Engineer
* **Version:** 2
* **Status:** VERIFIED

**File:** `core/grid.py`
* **Class:** `Grid`
    * **Attributes:**
        * `cells: List[List[Optional[Building]]]` - 2D grid of buildings.
    * **Methods:**
        * `def place_building(self, building: Building) -> bool:`
            + Docstring: Places a building on the grid if the cell is empty and adjacency rules are satisfied.
            + Details: Validates that the cell is empty and, for certain building types (e.g., Roads), checks adjacency rules before placement.
            + Returns: `True` if the building was successfully placed, `False` otherwise.
        * `def _has_adjacent_road_or_building(self, x: int, y: int) -> bool:`
            + Docstring: Checks if there is at least one adjacent Road or other building to the given coordinates.
            + Details: Used internally to validate adjacency rules for Roads.
            + Returns: `True` if there is an adjacent Road or building, `False` otherwise.
* **Owner:** Backend_Engineer
* **Version:** 4
* **Status:** VERIFIED

**File:** `core/hud.py`
* **Class:** `HUD`
    * **Attributes:**
        * `money: int` - Current amount of money to display.
        * `energy: int` - Current amount of energy to display.
    * **Methods:**
        * `def render(self, screen: pygame.Surface) -> None:`
            + Docstring: Renders the HUD on the screen.
            + Details: Includes logic for displaying resource alerts (e.g., flashing red when energy is low).
        * `def update(self, money: int, energy: int) -> None:`
            + Docstring: Updates the HUD with the latest resource values.
        * `def load_assets(self) -> None:`
            + Docstring: Loads the required font for the HUD.
    * **Owner:** Frontend_Engineer
    * **Version:** 2
    * **Status:** VERIFIED

**File:** `core/game.py`
* **Class:** `Game`
    * **Attributes:**
        * `grid: Grid` - The game grid.
        * `economy: Economy` - The economy system.
        * `hud: HUD` - The HUD for visual feedback.
    * **Methods:**
        * `def run(self) -> None:`
            + Docstring: Runs the game loop, coordinating all components.
            + Details: Includes logic for updating the grid, economy, and HUD in each frame, and handling user input for building placement.
        * `def handle_mouse_click(self, position: Tuple[int, int]) -> None:`
            + Docstring: Handles mouse clicks for building placement.
            + Details: Converts screen coordinates to grid coordinates, retrieves the selected building type, and attempts to place the building on the grid.
        * `def get_grid_coordinates(self, position: Tuple[int, int]) -> Tuple[int, int]:`
            + Docstring: Converts screen coordinates to grid coordinates.
        * `def get_selected_building_type(self) -> Optional[str]:`
            + Docstring: Returns the currently selected building type.
            + Details: Placeholder for building selection logic, to be replaced with actual UI integration.
* **Owner:** Backend_Engineer
* **Version:** 2
* **Status:** VERIFIED

### Status Model & Termination Guard
- Status in one line: use `TODO/DONE/ERROR/VERIFIED`; end only when all are `VERIFIED`.