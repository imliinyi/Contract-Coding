import subprocess
import os
import signal
import threading
from typing import Dict

# A dictionary to keep track of running processes, allowing them to be killed by the timer.
_running_processes: Dict[int, subprocess.Popen] = {}

def _kill_process_after_timeout(pid: int):
    """
    Internal function called by a Timer to terminate a process.
    """
    if pid not in _running_processes:
        print(f"Process {pid} already stopped or not found.")
        return

    print(f"Timeout of 60s reached for process {pid}. Terminating...")
    process = _running_processes[pid]
    try:
        # Send SIGTERM for graceful shutdown
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        # Process already dead
        pass
    except Exception:
        # If graceful shutdown fails, force kill
        process.kill()
    finally:
        if pid in _running_processes:
            del _running_processes[pid]

def start_process(command: str, working_directory: str = None) -> dict:
    """
    Starts a new process in the background that will automatically terminate after 60 seconds.
    Returns a process ID (PID).

    Args:
        command: The command to execute (e.g., "python -m http.server 8000").
        working_directory: The directory to run the command in. Defaults to the current directory.
    
    Returns:
        A dictionary containing the status and the process ID (PID).
    """
    try:
        process = subprocess.Popen(
            command.split(),
            cwd=working_directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        _running_processes[process.pid] = process

        # Set a timer to kill the process after 60 seconds
        timer = threading.Timer(120.0, _kill_process_after_timeout, args=[process.pid])
        timer.daemon = True  # Allows the main program to exit even if the timer is active
        timer.start()

        return {
            "status": "success",
            "pid": process.pid,
            "message": f"Process started with command '{command}' and PID {process.pid}. It will automatically terminate in 60 seconds."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- OpenAI Tool Schema ---
start_process_schema = {
    "type": "function",
    "function": {
        "name": "start_process",
        "description": "Starts a background process (like a web server) that auto-terminates after 60 seconds.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run, e.g., 'python backend/app.py'"},
                "working_directory": {"type": "string", "description": "The directory to run the command from."}
            },
            "required": ["command"]
        }
    }
}
