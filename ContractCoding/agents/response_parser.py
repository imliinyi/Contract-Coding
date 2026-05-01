from __future__ import annotations

import re
from typing import Any, Optional, Union

from ContractCoding.utils.state import GeneralState


class AgentResponseParser:
    def __init__(self, agent_name: str, logger: Any):
        self.agent_name = agent_name
        self.logger = logger

    def parse_response(
        self,
        response_text: str,
        current_state: GeneralState,
    ) -> GeneralState:
        thinking_match = re.search(r"<thinking>(.*?)</thinking>", response_text, re.DOTALL)
        output_match = re.search(r"<output>(.*?)</output>", response_text, re.DOTALL)

        thinking = thinking_match.group(1).strip() if thinking_match else ""
        raw_output = output_match.group(1).strip() if output_match else response_text

        return GeneralState(
            task=current_state.task,
            sub_task=current_state.sub_task,
            role=self.agent_name,
            thinking=thinking,
            output=raw_output,
            next_agents=[],
            task_requirements={},
        )

    @staticmethod
    def parse_tag_with_json(
        tag_name: str,
        text: str,
        expected_type: Union[type, None] = None,
    ) -> Optional[str]:
        pattern = re.compile(rf"<{tag_name}>(.*?)</{tag_name}>", re.DOTALL)
        match = pattern.search(text)

        if not match:
            markdown_pattern = re.compile(rf"###\s*<{tag_name}>\s*\n```json(.*?)\n```", re.DOTALL)
            match = markdown_pattern.search(text)

        if not match:
            return None

        content_str = match.group(1).strip()
        start_char, end_char = None, None

        if expected_type is list or content_str.startswith("["):
            start_char, end_char = "[", "]"
        elif expected_type is dict or content_str.startswith("{"):
            start_char, end_char = "{", "}"

        if not start_char:
            return content_str

        start_pos = content_str.find(start_char)
        end_pos = content_str.rfind(end_char)
        if start_pos == -1 or end_pos <= start_pos:
            return content_str

        return content_str[start_pos : end_pos + 1]
