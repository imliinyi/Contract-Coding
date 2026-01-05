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

    ProjectManagerAgent = agent_forge.create_agent("Project_Manager", AgentCapability(FILE=True))
    CriticAgent = agent_forge.create_agent("Critic", AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True))
    CodeReviewerAgent = agent_forge.create_agent("Code_Reviewer", AgentCapability(FILE=True, CODE=True))
    TechnicalWriterAgent = agent_forge.create_agent("Technical_Writer", AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True))
    EditingAgent = agent_forge.create_agent("Editing", AgentCapability(FILE=True))
    ResearcherAgent = agent_forge.create_agent("Researcher", AgentCapability(FILE=True, SEARCH=True))
    MathematicianAgent = agent_forge.create_agent("Mathematician", AgentCapability(FILE=True, MATH=True))
    ProofAssistantAgent = agent_forge.create_agent("Proof_Assistant", AgentCapability(FILE=True, MATH=True))
    DataScientistAgent = agent_forge.create_agent("Data_Scientist", AgentCapability(FILE=True, MATH=True, SEARCH=True))
    FrontendEngineerAgent = agent_forge.create_agent("Frontend_Engineer", AgentCapability(FILE=True, CODE=True))
    BackendEngineerAgent = agent_forge.create_agent("Backend_Engineer", AgentCapability(FILE=True, CODE=True))
    AlgorithmEngineerAgent = agent_forge.create_agent("Algorithm_Engineer", AgentCapability(FILE=True, CODE=True))
    TestEngineerAgent = agent_forge.create_agent("Test_Engineer", AgentCapability(FILE=True, CODE=True))
    ArchitectAgent = agent_forge.create_agent("Architect", AgentCapability(FILE=True, CODE=True))

    engine.register_agent("Project_Manager", ProjectManagerAgent, is_start=True)
    engine.register_agent("Critic", CriticAgent)
    engine.register_agent("Code_Reviewer", CodeReviewerAgent)
    engine.register_agent("Technical_Writer", TechnicalWriterAgent)
    engine.register_agent("Editing", EditingAgent)
    engine.register_agent("Researcher", ResearcherAgent)
    engine.register_agent("Mathematician", MathematicianAgent)
    engine.register_agent("Proof_Assistant", ProofAssistantAgent)
    engine.register_agent("Data_Scientist", DataScientistAgent)
    engine.register_agent("Frontend_Engineer", FrontendEngineerAgent)
    engine.register_agent("Backend_Engineer", BackendEngineerAgent)
    engine.register_agent("Algorithm_Engineer", AlgorithmEngineerAgent)
    engine.register_agent("Test_Engineer", TestEngineerAgent)
    engine.register_agent("Architect", ArchitectAgent)
    
    if args.task:
        print(f"Starting MetaFlow with task: {args.task}")
        result = engine.run(args.task)
        print("Final Result:", result)
    else:
        print("MetaFlow Engine initialized. Use --task to run a specific task.")
        parser.print_help()

if __name__ == "__main__":
    main()
