import re
import os

def count_tokens(log_file_path):
    if not os.path.exists(log_file_path):
        print(f"Error: File not found at {log_file_path}")
        return

    total_prompt = 0
    total_completion = 0

    # Pattern to match: ... Token Usage - Prompt: <number>, Completion: <number>
    # This matches both "Token Usage" and "Summarization Token Usage"
    pattern = re.compile(r"Token Usage - Prompt: (\d+), Completion: (\d+)")

    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    prompt_tokens = int(match.group(1))
                    completion_tokens = int(match.group(2))
                    total_prompt += prompt_tokens
                    total_completion += completion_tokens
        
        print(f"Total Prompt Tokens: {total_prompt}")
        print(f"Total Completion Tokens: {total_completion}")
        print(f"Total Tokens: {total_prompt + total_completion}")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Assuming the script is run from project root or similar, but let's use absolute path or relative to script location
    # The user asked to check `test/workspace/agent.log`
    # We will try to find the file relative to the current working directory first
    
    log_path = 'test/workspace/agent.log'
    if not os.path.exists(log_path):
        # Fallback to absolute path if running from a different location, 
        # or try to find it relative to this script if the script is in test/workspace/
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(script_dir, 'agent.log')
        
    print(f"Reading log file: {log_path}")
    count_tokens(log_path)
