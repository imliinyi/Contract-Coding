import json
import re
from typing import Any, Dict, List, Tuple

from MetaFlow.config import Config
from MetaFlow.llm.client import LLM
from MetaFlow.utils.graph_serializer import GraphSerializer
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState


class Reflector:
    def __init__(self, config: Config, agents: List[str]):
        self.config = config
        self.agents = agents

        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE_URL,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_API_MAX_TOKENS,
            temperature=self.config.OPENAI_API_TEMPERATURE,
        )
        self.serializer = GraphSerializer()
        self.logger = get_logger(config.LOG_PATH)

    def abstract_skill(self, all_layers: List[Dict[str, GeneralState]], trace_graph: List[Tuple[str, str, float]]) -> Dict[str, Any] | None:
        """
        Abstract the skill into a simpler form based on a rich graph representation, with a retry mechanism.
        """
        graph_representation = self.serializer.serialize_graph(all_layers, trace_graph)
        self.logger.info(f"Graph representation:\n {graph_representation}\n")
        prompt = self._build_prompt(graph_representation)

        for _ in range(self.config.REFLECTOR_RETRY_TIMES):
            message = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}]
            response = self.llm.chat(message)
            self.logger.info(f"Reflector response:\n {response}\n")
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
                    if abstract_skill["skill_name"] is None or abstract_skill["skill_name"].strip().lower() == 'null':
                        return None
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

        [Importance]
        1. The skill name you propose MUST NOT duplicate or overlap with existing agents. Current Existing Agents: {self.agents}.
        2. Only propose a subgraph that is highly generalizable and powerful (reusable across diverse tasks). Avoid trivial, narrow, or role-overlapping flows.
        3. If NO such highly generalizable pattern exists, set "skill_name" to null and DO NOT propose a subgraph.

        ```mermaid
        {graph_representation}
        ```

        [Constraints]
        1. Use ONLY these exact agent names (plus 'START' and 'END'): {self.agents}. Do NOT invent new names.
        2. Do NOT include suffixed names (e.g., 'Agent_X_3'). If you reference nodes from the graph, normalize them to their base names.
        3. Avoid generating flows that duplicate the responsibilities or pipelines of existing agents; prefer compact, capability-rich subgraphs.
        4. The subgraph MUST be expressed strictly as an edge list: [["source","target"], ...]. 'source' may be 'START'; 'target' may be 'END'.
        5.  The entry node of the subgraph MUST be named 'START'. The subgraph should ideally converge to a single node before connecting to the final 'END' node.
        
        [Your task]
        1. Analyze the graph to identify a compact, reusable, and complete multi-step pattern (e.g., write→review→fix→validate) that is NOT redundant with existing roles.
        2. Give this subgraph a concise 'skill_name' that clearly reflects its general capability.
        3. Briefly describe its function in 'description'.
        4. Define 'sub_graph' strictly as an edge list of known agents (normalized), with 'START' as entry and ideally converging before 'END'.

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

    def abstract_skill_from_subgraph(self, sub_graph: List[Tuple[str, str]]) -> Dict[str, Any] | None:
        """
        Given a candidate subgraph (edge list), let the LLM suggest a concise skill_name and description.
        The sub_graph is fixed; the model MUST NOT invent new edges or agent names.
        """
        edges_json = json.dumps(sub_graph, ensure_ascii=False)
        prompt = f"""
        You are a senior system architect. A frequent reusable subgraph was mined from past executions.

        [Constraints]
        1. Use ONLY existing agent names: {self.agents}. Do NOT invent new names.
        2. The subgraph edges are FIXED and MUST be kept exactly as provided. Do NOT modify, add, or remove edges.
        3. Propose a concise, generalizable 'skill_name' that does not duplicate existing agents.
        4. Provide a short 'description' of the capability.

        [Input]
        sub_graph (edge list): {edges_json}

        [Your output]
        Output ONLY a single JSON object enclosed in ```json with fields: skill_name, description, sub_graph.
        The 'sub_graph' field MUST equal the provided edge list verbatim.
        """

        message = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}]
        response = self.llm.chat(message)
        self.logger.info(f"Reflector (from subgraph) response:\n {response}\n")
        try:
            json_match = re.search(r'```json\n(.*?)\n```', response, re.DOTALL)
            json_str = json_match.group(1) if json_match else response
            abstract_skill = json.loads(json_str.strip())

            # Basic validation
            if not isinstance(abstract_skill, dict):
                return None
            if "skill_name" not in abstract_skill or "sub_graph" not in abstract_skill:
                return None
            if abstract_skill["skill_name"] is None or str(abstract_skill["skill_name"]).strip().lower() == 'null':
                return None
            # Ensure sub_graph equals provided
            if abstract_skill.get("sub_graph") != sub_graph:
                abstract_skill["sub_graph"] = sub_graph
            return abstract_skill
        except Exception as e:
            self.logger.error(f"abstract_skill_from_subgraph parse error: {e}")
            return None
