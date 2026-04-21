from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Union

from ContractCoding.memory.document import DocumentManager
from ContractCoding.utils.state import GeneralState


class AgentResponseParser:
    def __init__(self, agent_name: str, logger: Any):
        self.agent_name = agent_name
        self.logger = logger

    def parse_response(
        self,
        response_text: str,
        document_manager: DocumentManager,
        current_state: GeneralState,
    ) -> GeneralState:
        self.apply_document_actions(response_text, document_manager)

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

    def apply_document_actions(self, response_text: str, document_manager: DocumentManager) -> None:
        action_json_str = self.parse_tag_with_json("document_action", response_text, expected_type=list)
        if not action_json_str:
            return

        try:
            actions = json.loads(action_json_str)
        except (json.JSONDecodeError, TypeError) as exc:
            self.logger.error("Failed to parse document actions: %s", exc)
            return

        processed_actions = []
        for action in actions:
            action_type = action.get("type")
            if action_type in {"add", "update"}:
                action["agent_name"] = self.agent_name
                action["base_version"] = document_manager.get_version()

            if action_type == "add" and action.get("section") is not None and self.agent_name != "Project_Manager":
                self.logger.warning(
                    "Ignored section add by non-PM agent: %s section=%s",
                    self.agent_name,
                    action.get("section"),
                )
                continue

            processed_actions.append(action)

        if not processed_actions:
            return

        if hasattr(document_manager, "is_aggregating") and document_manager.is_aggregating():
            document_manager.queue_actions(processed_actions)
            return

        document_manager.execute_actions(processed_actions)

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
