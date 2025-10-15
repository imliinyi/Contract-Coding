import re
from typing import List, Tuple, Dict, Any

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.utils.state import Message, GeneralState
from MetaFlow.utils.coding.python_executor import execute_code_get_return
from MetaFlow.utils.math.get_predict import get_predict


class AgentRunner:
    def __init__(self, agents: Dict[str, BaseAgent]):
        self.agents = agents

    def run(self, agent_name: str, state: GeneralState, test_cases: List[str],
            next_available_agents: List[str]) -> Tuple[Message, str, str]:
        """
        Run a single agent, process its output, and return the message, code, and answer.
        """
        agent = self.agents.get(agent_name, None)
        if not agent:
            raise ValueError(f"Agent {agent_name} not found.")

        message = agent._execute_agent(
            state=state,
            test_cases=test_cases,
            next_available_agents=next_available_agents
        )

        # Extract code from the message output
        code_pattern = r'```python\n(.*?)```'
        code_match = re.search(code_pattern, message.output, re.DOTALL)
        code = code_match.group(1).strip() if code_match else ''

        if code:
            answer = execute_code_get_return(code)
        else:
            answer = get_predict(message.output)

        return message, code, answer
