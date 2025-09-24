import re
import logging
from typing import List, Dict

from langgraph.graph import END, MessageGraph

from DAGAgent.utils.state import Message
from DAGAgent.llm.llm import LLM
from DAGAgent.config import Config
from DAGAgent.utils.coding.python_executor import PyExecutor


logger = logging.getLogger(__name__)

class BaseAgent:
    """
    BaseAgent class for the DAGAgent.
    """
    def __init__(self, agent_name: str, config: Config):
        self.agent_name = agent_name
        self.config = config
        self.llm = LLM(self.config)
        self.salaries : Dict[str, float] = self.config.get('salaries', {})

        self.success = 0
        self.trails = 0
        self.success_rate = 0.0
        self.test_cases = None

    @staticmethod
    def validate_state(state: Message | None) -> bool:
        if not state:
            logger.error("State is None")
            return False
        
        try:
            if not state.content:
                logger.error("State content is empty")
                return False
            return True
        except Exception as e:
            logger.error(f"Error validating state: {e}")
            return False

    @staticmethod
    def get_prompt(sys_prompt: str, state: Message, next_available_agents: List[str], 
                    agent_details: Dict[str, str]) -> List[Message]:
        avail_agents_datails = ', '.join(f"{agent_name}: {agent_details.get(agent_name, 'N/A')};\n" 
                                for agent_name in next_available_agents)
        return [{
                "role": "system", 
                "content": [
                    {"type": "text", "text": sys_prompt.format(avail_agents_datails=avail_agents_datails)}
                    ]
            },
            {"role": "user", "content": [{"type": "text", "text": state.output}]}
        ]

    def update_success_rate(self) -> None:
        """
        Update the success rate of the agent.
        """
        self.success += 1
        self.success_rate = self.success / self.trails if self.trails > 0 else self.success_rate

    def get_next_agents(self, last_message: Message) -> List[str] | str:
        if not last_message or not self.validate_state(last_message):
            return END

        if 'FINAL_ANSWER:' in last_message.output.upper() or 'END' in last_message.output.upper():
            return END

        comment_pattern = r'/\*.*?\*/'
        comment_match = re.search(comment_pattern, last_message.output, re.DOTALL)
        comment = comment_match.group(0) if comment_match else ''
        
        next_agents = []
        for agent in self.salaries.keys():
            if agent.upper() in comment.upper():
                next_agents.append(agent)

        return next_agents if not next_agents else END

    def extract_example(self, prompt: str) -> str:
        lines = (line.strip() for line in prompt.split('\n') if line.strip())

        results = []
        lines_iter = iter(lines)
        for line in lines_iter:
            if line.startswith('>>>'):
                function_call = line[4:]
                expected_output = next(lines_iter, None)
                if expected_output:
                    results.append(f"assert {function_call} == {expected_output}")

        self.test_cases = results

    def run_test(self, code: str) -> tuple[bool, str, Message]:
        is_solved, feedback, state = PyExecutor().execute(code, self.test_cases, timeout=10)
        return is_solved, feedback, state

    def _execute_agent(self, state: Message | List[Message], test_cases: List[str], next_available_agents: List[str]) -> Message:
        """
        Executes the agent's logic.

        This method can receive a single Message or a list of Messages, depending on the number of
        predecessor nodes in the graph. Subclasses should implement the logic to handle both cases.

        Args:
            state: A Message object or a list of Message objects from predecessor agents.

        Returns:
            A Message object representing the output of the agent.
        """
        raise NotImplementedError("This method should be implemented by subclass")

    