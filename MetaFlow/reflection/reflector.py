import json
from typing import List, Tuple, Dict, Any

from MetaFlow.llm.llm import LLM


class Reflector:
    def __init__(self, config: Config):
        self.config = config
        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_MAX_TOKENS,
            temperature=self.config.OPENAI_TEMPERATURE,
        )

    def abstract_skill(self, trace_graph: List[Tuple[str, str]]) -> Dict[str, Any] | None:
        """
        Abstract the skill into a simpler form.
        """
        prompt = self._build_prompt(trace_graph)
        response = self.llm.chat(prompt)

        try:
            abstract_skill = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return None
        return abstract_skill

    def _build_prompt(self, trace_graph: List[Tuple[str, str]]) -> str:
        """
        Build the prompt for the abstract skill.
        """
        trace_str = ", ".join([f"{agent} -> {next_agent}" for agent, next_agent in trace_graph])

        prompt = f"""
        You are a top tier system architect skilled in discovering and abstracting reusable component patterns from complex dependency graphs.

        [Successful Execution Trajectory Diagram (Edge List)]
        {trace_str}

        [Your task]
        1. Analyze this dependency graph and identify the most core, reusable, and fully functional subgraph patterns.
        2. Give this subgraph pattern a concise and expressive 'skill_name'.
        3. Briefly describe the function of this skill (description).
        4. Define the subgraph structure (sub_graph) of this new skill in the form of an edge list. The entry node of the subgraph should be named 'START'.

        [Your output]
        Please output strictly in JSON format and do not include any other explanations. For example:
        {{
        "skill_name": "DebugLoopAgent",
        "description": "A debugging loop skill that automatically writes code, tests, and fixes based on feedback.",
        "sub_graph": [["START", "CodeAgent"], ["CodeAgent", "TestAgent"], ["TestAgent", "CodeAgent"]]
        }}
        """
        return prompt