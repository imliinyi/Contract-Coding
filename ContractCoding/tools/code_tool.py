import subprocess
import sys
from typing import Callable

from ContractCoding.tools.file_tool import WorkspaceFS


def build_run_code(workspace_dir: str) -> Callable:
    fs = WorkspaceFS(workspace_dir)
    workspace_path = fs.resolve('.')

    def run_code(code: str, language: str = 'python') -> str:
        if language.lower() not in ['python', 'bash', 'sh']:
            return (
                f"Error: Language '{language}' is not supported. Supported languages are 'python', 'bash', and 'sh'."
            )

        try:
            if language.lower() == 'python':
                process = subprocess.run(
                    [sys.executable, '-c', code],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=workspace_path,
                )
            else:
                process = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=workspace_path,
                    executable='/bin/bash' if language.lower() == 'bash' else '/bin/sh',
                )

            stdout = process.stdout
            stderr = process.stderr

            output = ""
            if stdout:
                output += f"Stdout:\n{stdout}\n"
            if stderr:
                output += f"Stderr:\n{stderr}\n"

            return output or "Code executed successfully with no output."

        except subprocess.TimeoutExpired:
            return "Error: Code execution timed out after 30 seconds."
        except Exception as e:
            return f"An error occurred during code execution: {str(e)}"

    run_code.openai_schema = {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": "Executes code in an isolated environment and returns its stdout/stderr. Runs inside workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to execute."},
                    "language": {"type": "string", "description": "The programming language of the code. Supported: python, bash, sh.", "default": "python"},
                },
                "required": ["code"],
            },
        },
    }

    return run_code
