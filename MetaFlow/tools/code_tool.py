import os
import subprocess
import sys

from MetaFlow.tools.file_tool import _get_full_path

def run_code(code: str, language: str = 'python') -> str:
    """
    Executes code in an isolated environment and returns its stdout/stderr.

    :param code: The code to execute.
    :param language: The programming language of the code. Defaults to 'python'.
    :return: The output of the code execution (stdout and stderr).
    """
    # 获取工作区目录，但不改变当前目录
    workspace_path = _get_full_path('.')
    if language.lower() not in ['python', 'bash', 'sh']:
        return f"Error: Language '{language}' is not supported. Supported languages are 'python', 'bash', and 'sh'."

    try:
        # 使用cwd参数设置工作目录，而不是改变当前进程的工作目录
        if language.lower() == 'python':
            process = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workspace_path  # 在工作区内执行代码
            )
        else:  # For bash/sh
            process = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workspace_path,  # 在工作区内执行代码
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