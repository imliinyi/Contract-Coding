from abc import ABC, abstractmethod
import ast
import json
import re
import threading
from typing import Any, Dict, List, Optional, Tuple, Union

from MetaFlow.config import Config
from MetaFlow.llm.client import LLM
from MetaFlow.memory.document import DocumentManager
from MetaFlow.memory.processor import MemoryProcessor
from MetaFlow.prompts.agents_prompt import AGENT_DETAILS, get_agent_prompt
from MetaFlow.prompts.system_prompt import CORE_SYSTEM_PROMPT
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState


class BaseAgent(ABC):
    """
    Abstract BaseAgent class for the DAGAgent.
    It defines the common interface for all agents.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: Optional[List] = None, config: Config = None):
        self.agent_name = agent_name
        self.agent_prompt = agent_prompt
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self._llm_local = threading.local()

        self.system_prompt = self.get_system_prompt()
        self.custom_tools = custom_tools or []

    @property
    def llm(self) -> LLM:
        llm = getattr(self._llm_local, "llm", None)
        if llm is None:
            llm = LLM(
                deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
                api_key=self.config.OPENAI_API_KEY,
                api_base=self.config.OPENAI_API_BASE_URL,
                max_tokens=self.config.OPENAI_API_MAX_TOKENS,
                temperature=self.config.OPENAI_API_TEMPERATURE,
            )
            self._llm_local.llm = llm
        return llm

    @staticmethod
    def validate_state(state: GeneralState | None) -> bool:
        if not state:
            self.logger.error("State is None")
            return False
        
        try:
            if not state.output:
                self.logger.error("State output is empty")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error validating state: {e}")
            return False

    @staticmethod
    def get_system_prompt() -> str:
        """
        Get the system prompt for the agent.
        """
        return CORE_SYSTEM_PROMPT

    def get_agent_prompt(self) -> str:
        """
        Get the agent prompt for the agent.
        """
        return get_agent_prompt(self.agent_name)

    def get_prompt(self, task_description: str, prompt: str, 
            next_available_agents: List[str]) -> List[Dict[str, Union[str, List]]]:
        # Include dynamic skills even if not in AGENT_DETAILS, with a generic description
        def _describe(agent_name: str) -> str:
            return f"{agent_name}: {AGENT_DETAILS.get(agent_name, '')}"
        available_agents = ', '.join(_describe(agent_name) for agent_name in next_available_agents)
        system_prompt = self.system_prompt

        if self.agent_name == "Project_Manager":
            system_prompt = system_prompt + f"""
                # Available Agents: {available_agents}  
            """
        
        prompt_template = """
        # User Overall Task
        {task_description}

        # Current Task
        {prompt}
        """

        return [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": f"# Your Role Guideline:\n {self.agent_prompt}"},
            {"role": "user", "content": prompt_template.format(task_description=task_description, prompt=prompt)}
        ]

    @abstractmethod
    def _execute_agent(self, state: GeneralState, document_manager: DocumentManager, memory_processor: MemoryProcessor, 
                next_available_agents: List[str]) -> GeneralState:
        """
        Executes the agent's logic. This method MUST be implemented by all concrete subclasses.
        """
        raise NotImplementedError("This method should be implemented by a subclass.")

    def _extract_tag_blocks(self, tag_name: str, text: str) -> List[str]:
        pattern = re.compile(rf"<{tag_name}>([\s\S]*?)</{tag_name}>", re.IGNORECASE)
        return [m.group(1).strip() for m in pattern.finditer(text or "")]

    def _extract_balanced_json(self, s: str, expected_type: Union[type, None] = None) -> Optional[str]:
        raw = (s or "").strip()
        if not raw:
            return None

        start_candidates: List[str]
        if expected_type is list:
            start_candidates = ['[']
        elif expected_type is dict:
            start_candidates = ['{']
        else:
            start_candidates = ['[', '{']

        start_idx = -1
        start_ch = ''
        for ch in start_candidates:
            i = raw.find(ch)
            if i != -1 and (start_idx == -1 or i < start_idx):
                start_idx = i
                start_ch = ch
        if start_idx == -1:
            return None

        end_ch = ']' if start_ch == '[' else '}'

        in_str = False
        esc = False
        depth = 0
        for i in range(start_idx, len(raw)):
            c = raw[i]
            if in_str:
                if esc:
                    esc = False
                    continue
                if c == '\\':
                    esc = True
                    continue
                if c == '"':
                    in_str = False
                continue

            if c == '"':
                in_str = True
                continue

            if c == start_ch:
                depth += 1
                continue
            if c == end_ch:
                depth -= 1
                if depth == 0:
                    return raw[start_idx : i + 1]

        return None

    def _parse_tag_with_json(self, tag_name: str, text: str, expected_type: Union[type, None] = None) -> Optional[str]:
        blocks = self._extract_tag_blocks(tag_name, text)
        if not blocks:
            pattern_md = re.compile(rf"###\s*<{tag_name}>\s*\n```json([\s\S]*?)\n```", re.IGNORECASE)
            m = pattern_md.search(text or "")
            if m:
                blocks = [m.group(1).strip()]
        if not blocks:
            return None

        extracted = self._extract_balanced_json(blocks[0], expected_type=expected_type)
        return extracted or blocks[0]

    def _parse_document_action(self, response_text: str, document_manager: DocumentManager):
        """
        Parses the <document_action> tag and executes the actions using the DocumentManager.
        """
        blocks = self._extract_tag_blocks("document_action", response_text)
        if not blocks:
            return

        all_actions: List[Dict[str, Any]] = []
        last_err: Optional[Exception] = None
        for raw_block in blocks:
            payload = self._extract_balanced_json(raw_block, expected_type=list) or raw_block
            if not payload.strip():
                continue

            parsed = None
            try:
                parsed = json.loads(payload)
            except Exception as e:
                last_err = e
                try:
                    parsed = ast.literal_eval(payload)
                except Exception as e2:
                    last_err = e2
                    parsed = None

            if isinstance(parsed, dict):
                parsed = [parsed]

            if not isinstance(parsed, list):
                continue

            for a in parsed:
                if isinstance(a, dict):
                    all_actions.append(a)

        if not all_actions:
            if last_err is not None:
                raise last_err
            return

        processed_actions = []
        for action in all_actions:
            action_type = action.get('type')

            if action_type in ('add', 'update'):
                action['agent_name'] = self.agent_name
                action['base_version'] = document_manager.get_version()

            if (
                action_type == 'add'
                and action.get('section') is not None
                and self.agent_name not in ('Project_Manager', 'Architect')
            ):
                self.logger.warning(
                    f"Ignored section add by non-PM agent: {self.agent_name} section={action.get('section')}"
                )
                continue

            processed_actions.append(action)

        if processed_actions:
            if hasattr(document_manager, 'is_aggregating') and document_manager.is_aggregating():
                document_manager.queue_actions(processed_actions)
            else:
                document_manager.execute_actions(processed_actions)

    def _parse_response(self, response_text: str, document_manager: DocumentManager, current_state: GeneralState) -> GeneralState:
        """
        Parses the raw response from the agent's execution and packages it into a new GeneralState object.
        """

        self._parse_document_action(response_text, document_manager)

        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output_match = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)
        
        thinking = thinking_match.group(1).strip() if thinking_match else ""
        raw_output = output_match.group(1).strip() if output_match else response_text

        task_requirements = None
        # We don't rely on task_requirements for scheduling anymore, but we keep it optional for logging if agents still output it.
        # But we remove the strict validation.
        
        return GeneralState(
            task=current_state.task,
            sub_task=current_state.sub_task,
            role=self.agent_name,
            thinking=thinking,
            output=raw_output,
            next_agents=[], # No longer determined by LLM directly
            task_requirements={}, # Optional
        )

  
