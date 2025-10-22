from typing import List

from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.prompt.system_prompt import CORE_SYSTEM_PROMPT
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.math_tool import solve_math_expression
from MetaFlow.utils.state import GeneralState, Message


class MathematicianAgent(ActionAgent):
    """
    The Mathematician agent handles tasks requiring deep mathematical knowledge.
    It is equipped with a symbolic math solver tool.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [solve_math_expression]
        # Pass the agent name and its tools to the parent ActionAgent
        super().__init__("Mathematician", config, tools)


class ProofAssistantAgent(LLMAgent):
    """
    The Proof Assistant agent is responsible for assisting in the process of mathematical proof.
    It can generate, verify, and explain mathematical proofs using a symbolic math solver.
    """
    def __init__(self, config: Config):
        # Pass the agent name and its tools to the parent ActionAgent
        super().__init__("Proof_Assistant", config)


class DataScientistAgent(ActionAgent):
    """
    The Data Scientist agent focuses on data analysis, manipulation, and visualization.
    It is equipped with a code execution tool to run Python scripts for data processing
    using libraries like Pandas, NumPy, and Matplotlib.
    """
    def __init__(self, config: Config):
        # Equip the agent with a code execution tool
        self.tools = [run_code]
        super().__init__("Data_Scientist", config, self.tools)

