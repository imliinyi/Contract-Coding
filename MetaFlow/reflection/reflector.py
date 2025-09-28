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
        Abstract the following skill into a simpler form:
        {trace_graph}
        """
        return prompt