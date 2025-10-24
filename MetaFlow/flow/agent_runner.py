import re
from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.decision_space import logger
from MetaFlow.utils.coding.python_executor import execute_code_get_return
from MetaFlow.utils.math.get_predict import get_predict
from MetaFlow.utils.state import GeneralState, Message


class AgentRunner:
    def __init__(self, agents: Dict[str, BaseAgent]):
        self.agents = agents

    def _normalize_agent_name(self, agent_name: str, all_agents: List[str]) -> str:
        """
        Normalize the agent name to a registered name.
        """
        # Create a mapping from lowercase, underscore-removed names to original names
        normalized_map = {re.sub(r'[^a-z0-9]', '', name.lower()): name for name in all_agents}
        
        # Normalize the input name
        normalized_input = re.sub(r'[^a-z0-9]', '', agent_name.lower())
        
        return normalized_map.get(normalized_input, agent_name) # Return original if not found

    def run(self, agent_name: str, state: GeneralState, test_cases: List[str],
            next_available_agents: List[str]) -> Tuple[Message, str, str, Optional[Dict[str, Any]]]:
        """
        Run a single agent, process its output, and return the message, code, answer, and shared_context.
        """
        agent = self.agents.get(agent_name, None)
        if not agent:
            raise ValueError(f"Agent {agent_name} not found.")

        logger.info(f"==========Running agent {agent_name}")
        message, collaborative_document = agent._execute_agent(
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
        if not answer:
            answer = ""

        # Normalize the next_agents names
        if message.next_agents:
            normalized_next_agents = [self._normalize_agent_name(name, list(self.agents.keys())) for name in message.next_agents]
            message.next_agents = normalized_next_agents

        # print(f"==========Agent {agent_name} output: {message.output + str(message.next_agents)}")

        return message, code, answer, collaborative_document
