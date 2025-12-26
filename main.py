import argparse
import sys
from MetaFlow.config import Config
from MetaFlow.engine import Engine

def main():
    parser = argparse.ArgumentParser(description="MetaFlow: Multi-Agent Collaboration Framework")
    parser.add_argument("--task", type=str, help="The task to execute")
    parser.add_argument("--train", action="store_true", help="Run in training mode")
    
    args = parser.parse_args()

    config = Config()
    engine = Engine(config)
    
    if args.task:
        print(f"Starting MetaFlow with task: {args.task}")
        result = engine.run(args.task)
        print("Final Result:", result)
    else:
        print("MetaFlow Engine initialized. Use --task to run a specific task.")
        parser.print_help()

if __name__ == "__main__":
    main()
