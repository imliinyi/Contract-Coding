# version: 1
from backend.game_logic import Game
from frontend.gui import GUI

def main():
    # Initialize the game logic
    game = Game(size=15)  # Standard Gomoku board size is 15x15

    # Initialize the GUI and pass the game instance
    gui = GUI()

    # Start the GUI main loop
    gui.window.mainloop()

if __name__ == "__main__":
    main()