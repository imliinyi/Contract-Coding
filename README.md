# ContractCoding

ContractCoding is a contract-driven multi-agent collaboration paradigm that enables parallel programming and understands the operating principles of the entire system.

## Project Structure

- **ContractCoding/**: The core framework code.
  - `agents/`: Agent implementations.
  - `engine.py`: The main engine that orchestrates the workflow.
  - `runner.py`: Executes individual agents.
  - `traverser.py`: Manages the graph traversal.
  - `memory/`: Memory and document management.
  - `prompts/`: System and agent prompts.
  - `tools/`: Available tools for agents.
  - `llm/`: LLM client and utilities.
- **examples/**: Example scripts and usage.
- **main.py**: CLI entry point.

## Installation

```bash
pip install -r requirements.txt
```

## Add Agent

To add a new agent, create a new file in the `agents/` directory. The agent should inherit from the `BaseAgent` class and implement the `run` method.

```python
from ContractCoding.agents.base import BaseAgent

class MyAgent(BaseAgent):
    def run(self, task: str) -> str:
        # Implement the agent's logic here
        return f"Executing task: {task}"
```

### Register Agent

Add the agent to the `register_agent` function in `ContractCoding/engine.py`.

```python
from ContractCoding.config import Config
from ContractCoding.orchestration.engine import Engine
from ContractCoding.agents.forge import AgentForge

config = Config()
agent_forge = AgentForge(config)
contractcoding = Engine(config)

agent_forge.create_agent("My_Agent", MyAgent)
contractcoding.register_agent("My_Agent", MyAgent)
```

### Run Engine

```python
result = contractcoding.run("Your task description")
print(result)
```

## Usage

To run the ContractCoding engine, use the following command:

```bash
python main.py --task "Your task description"
```

Replace `"Your task description"` with the actual task you want the system to perform.

### Example

Run the engine with a task:

```bash
python main.py --task "Write a Gomoku program with AI that allows players to play against AI"
```
