import json
import re
from typing import List, Tuple, Dict, Any

from MetaFlow.llm.llm import LLM
from MetaFlow.config import Config
from MetaFlow.utils.state import GeneralState
from MetaFlow.utils.graph_serializer import GraphSerializer


class Reflector:
    def __init__(self, config: Config):
        self.config = config
        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE_URL,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_API_MAX_TOKENS,
            temperature=self.config.OPENAI_API_TEMPERATURE,
        )
        self.serializer = GraphSerializer()

    def abstract_skill(self, all_layers: List[Dict[str, GeneralState]], trace_graph: List[Tuple[str, str, float]]) -> Dict[str, Any] | None:
        """
        Abstract the skill into a simpler form based on a rich graph representation, with a retry mechanism.
        """
        graph_representation = self.serializer.serialize_graph(all_layers, trace_graph)
        prompt = self._build_prompt(graph_representation)

        for _ in range(self.config.REFLECTOR_RETRY_TIMES):
            message = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}]
            response = self.llm.chat(message)
            try:
                # Extract JSON from the response, which might be in a markdown block
                json_match = re.search(r'```json\n(.*?)\n```', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    # If no markdown block, assume the whole response is JSON
                    json_str = response
                
                abstract_skill = json.loads(json_str.strip())

                if "skill_name" in abstract_skill and "sub_graph" in abstract_skill:
                    return abstract_skill

                # If the JSON is valid but missing required fields, add a feedback loop to the prompt
                prompt = f"""{prompt}\n\n
                [Previous Attempt Feedback]\n
                Your previous output was not a valid JSON or was missing required fields. 
                Please ensure you output ONLY a single valid JSON object enclosed in ```json tags with 'skill_name' and 'sub_graph' fields."""  

            except (json.JSONDecodeError, TypeError) as e:
                # If parsing fails, provide feedback for retry
                prompt = f"""{prompt}\n\n
                [Previous Attempt Feedback]\n
                Your previous output resulted in a JSON parsing error: {e}. 
                Please correct the format and try again."""

        return None

    def _build_prompt(self, graph_representation: str) -> str:
        """
        Build the prompt for the abstract skill, using a rich Mermaid graph representation.
        """
        return f"""
        You are a top-tier system architect skilled in discovering and abstracting reusable component patterns from complex dependency graphs.

        [Executed Task Trajectory Graph]
        This graph, in Mermaid.js format, shows the layered execution flow of a previous task. It includes the agents at each layer and the connections between them.

        ```mermaid
        {graph_representation}
        ```

        [Your task]
        1.  Analyze this graph to identify the most core, reusable, and fully functional subgraph pattern. A good pattern often represents a complete logical loop or a self-contained multi-step process (e.g., code -> test -> fix -> test).
        2.  Give this subgraph pattern a concise and expressive 'skill_name' (e.g., "CodeDebuggingLoop").
        3.  Briefly describe the function of this skill in the 'description' field.
        4.  Define the structure of this new skill in the 'sub_graph' field. The structure MUST be an **edge list** (a list of lists, where each inner list is `["source_agent", "target_agent"]`).
        5.  The entry node of the subgraph MUST be named 'START'. The subgraph should ideally converge to a single node before connecting to the final 'END' node.

        [Your output]
        Please output **ONLY** a single JSON object enclosed in ```json tags. Do not include any other explanations.

        Example:
        ```json
        {{
            "skill_name": "DebugLoopAgent",
            "description": "A debugging loop skill that automatically writes code, tests, and fixes based on feedback.",
            "sub_graph": [["START", "ProgrammingAgent"], ["ProgrammingAgent", "TestEngineerAgent"], ["TestEngineerAgent", "ProgrammingAgent"], ["TestEngineerAgent", "END"]]
        }}
        ```
        """