"""
This module defines the ReasoningAgent, the core thinking engine for all specialist agents.
"""
import json
import re
from typing import List, Dict

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.config import Config
from MetaFlow.prompt.system_prompt import get_persona_prompt, REACT_SYSTEM_PROMPT
from MetaFlow.utils.state import GeneralState, Message

class ReasoningAgent(BaseAgent):
    """
    The ReasoningAgent is an abstract base class for all specialist agents.
    It uses a specific persona to reason about a task and decide on the next action (tool call).
    """
    def __init__(self, agent_name: str, config: Config, persona_name: str):
        super().__init__(agent_name, config)
        self.persona_name = persona_name
        # Load the specific persona prompt during initialization
        self.persona_prompt = get_persona_prompt(persona_name)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Message:
        """
        Executes the reasoning cycle: combines persona, task, and history to decide the next tool call.
        """
        # 1. Construct the full prompt for the LLM
        # We combine the persona prompt with the ReAct prompt format.
        # Note: In a real implementation, you would pass available_tools and history.
        full_system_prompt = f"""
{self.persona_prompt}

{REACT_SYSTEM_PROMPT}
"""
        
        # For now, we'll use the last message as the main input.
        user_prompt = state.message.output

        inputs = self.get_prompt(
            sys_prompt=full_system_prompt,
            prompt=user_prompt,
            # These would be dynamically filled:
            task_description=state.task,
            available_tools="[tool1, tool2, ...]", # Placeholder
            history="" # Placeholder
        )

        # 2. Call the LLM to get the next action
        response_text = self.llm.chat(inputs)

        # 3. Parse the LLM's response to extract thinking and tool call
        thinking = self._parse_thinking(response_text)
        tool_call_dict = self._parse_tool_call(response_text)

        # 4. Return the structured message, which now contains the tool call
        return Message(
            role=self.agent_name,
            thinking=thinking,
            output=json.dumps(tool_call_dict) # The output is now the structured tool call
        )

    def _parse_thinking(self, response: str) -> str:
        """Extracts content from the <thinking> block."""
        match = re.search(r'<thinking>(.*?)</thinking>', response, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _parse_tool_call(self, response: str) -> Dict:
        """Extracts the JSON object from the <tool_call> block."""
        match = re.search(r'<tool_call>\s*```json\n(.*?)\n```\s*</tool_call>', response, re.DOTALL)
        if not match:
            return {"tool_name": "error", "parameters": {"message": "Invalid tool call format"}}
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            return {"tool_name": "error", "parameters": {"message": "Invalid JSON in tool call"}}