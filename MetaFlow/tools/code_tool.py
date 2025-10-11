import subprocess
import sys

def run_code(code: str, language: str = 'python') -> str:
    """
    Executes code in an isolated environment and returns its stdout/stderr.

    :param code: The code to execute.
    :param language: The programming language of the code. Defaults to 'python'.
    :return: The output of the code execution (stdout and stderr).
    """
    if language.lower() not in ['python', 'bash', 'sh']:
        return f"Error: Language '{language}' is not supported. Supported languages are 'python', 'bash', and 'sh'."

    try:
        if language.lower() == 'python':
            process = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                text=True,
                timeout=30
            )
        else:  # For bash/sh
            process = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                executable='/bin/bash' if language.lower() == 'bash' else '/bin/sh'
            )

        stdout = process.stdout
        stderr = process.stderr

        output = ""
        if stdout:
            output += f"Stdout:\n{stdout}\n"
        if stderr:
            output += f"Stderr:\n{stderr}\n"
        
        if not output:
            return "Code executed successfully with no output."
            
        return output

    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out after 30 seconds."
    except Exception as e:
        return f"An error occurred during code execution: {str(e)}"

run_code.openai_schema = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": "Executes code in an isolated environment and returns its stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The code to execute."
                },
                "language": {
                    "type": "string",
                    "description": "The programming language of the code. Defaults to 'python'.",
                    "enum": ["python", "bash", "sh"]
                }
            },
            "required": ["code"]
        }
    }
}