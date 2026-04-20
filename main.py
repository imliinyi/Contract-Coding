import argparse
from ContractCoding.config import Config
from ContractCoding.orchestration.engine import Engine
from ContractCoding.agents.forge import AgentForge



def main():
    parser = argparse.ArgumentParser(description="ContractCoding: Multi-Agent Collaboration Framework")
    parser.add_argument("--task", type=str, help="The task to execute")
    parser.add_argument("--train", action="store_true", help="Run in training mode")
    parser.add_argument("--workspace", type=str, help="Workspace directory for file tools")
    parser.add_argument("--log-path", type=str, help="Path to agent log file")
    parser.add_argument("--max-layers", type=int, help="Maximum orchestration layers")
    
    args = parser.parse_args()

    config_kwargs = {}
    if args.workspace:
        config_kwargs["WORKSPACE_DIR"] = args.workspace
    if args.log_path:
        config_kwargs["LOG_PATH"] = args.log_path
    if args.max_layers is not None:
        config_kwargs["MAX_LAYERS"] = args.max_layers

    config = Config(**config_kwargs)
    engine = Engine(config)
    agent_forge = AgentForge(config)

    for agent_name, agent in agent_forge.create_default_agents().items():
        engine.register_agent(agent_name, agent, is_start=(agent_name == "Project_Manager"))
    
    if args.task:
        print(f"Starting ContractCoding with task: {args.task}")
        result = engine.run(args.task)
        if result is None:
            print("Final Result: None")
        else:
            print("Final Result:", result.output)
    else:
        print("ContractCoding Engine initialized. Use --task to run a specific task.")
        parser.print_help()

if __name__ == "__main__":
    main()
