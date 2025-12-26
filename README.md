# GeneralAgent (MetaFlow)

MetaFlow is a contract-driven multi-agent collaboration paradigm that enables parallel programming and understands the operating principles of the entire system.

## Project Structure

- **MetaFlow/**: The core framework code.
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

## Usage

Run the engine with a task:

```bash
python main.py --task "Create a simple calculator"
```

## Gomoku Example

A Gomoku game implementation is available in `examples/gomoku.py`.
