import json
import time
from typing import Dict, List

from pydantic import ValidationError
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.tools.file_tool import file_tree
from MetaFlow.tools.browser_tool import browse_and_capture
from MetaFlow.utils.exception import EmptyTaskRequirementsError
from MetaFlow.utils.state import GeneralState



class LLMAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: List[Dict[str, str]] = None, config: Config = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)
        # self.driver = webdriver.Chrome()

    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}

            Current Project Structure: {file_tree('.')}

            Current Collaborative Document: {document_manager.get()}
            
            IMPORTANT: When using tools, focus on the technical implementation. 
            After tool execution, the system will automatically generate a complete summary 
            including all required output sections (<thinking>, <output>, <task_requirements>, etc.).
        """
        
        # Get managed memory and prepare chat history
        agent_memory = memory_processor.get_memory(self.agent_name)
        memory_history = []
        for msg in agent_memory:
            role = "system" if "<summary" in msg.output else 'assistant'
            memory_history.append({"role": role, "content": msg.output})

        # Get the standard prompt components
        system_inputs = self.get_prompt(
            task_description=state.task,
            prompt=prompt, 
            next_available_agents=next_available_agents
        )

        # Combine memory with the fresh prompt
        inputs = memory_history + system_inputs
        retry = 0
        while retry < 3:
            if self.custom_tools:
                raw_response = self.llm.chat_with_tools(messages=inputs, tools=self.custom_tools)
            else:
                raw_response = self.llm.chat(inputs)
            
            self.logger.info(f"==========LLMAgent {self.agent_name} output: {raw_response}")

            try:
                output_state = self._parse_response(raw_response, document_manager, state)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state


class GUIAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, agent_prompt: str, config: Config = None):
        super().__init__(agent_name, agent_prompt, None, config)
        self.driver = webdriver.Chrome()
        self.driver.set_window_size(1920, 1080)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}

            Current Project Structure: {file_tree('.')}

            Current Collaborative Document: {document_manager.get()}
            
            IMPORTANT: When using tools, focus on the technical implementation. 
            After tool execution, the system will automatically generate a complete summary 
            including all required output sections (<thinking>, <output>, <task_requirements>, etc.).
        """
        
        # Get managed memory and prepare chat history
        agent_memory = memory_processor.get_memory(self.agent_name)
        memory_history = []
        for msg in agent_memory:
            role = "system" if "<summary" in msg.output else 'assistant'
            memory_history.append({"role": role, "content": msg.output})

        # self.driver.get("http://localhost:5000")
        # try:
        #     self.driver.find_element(By.TAG_NAME, 'body').click()
        # except:
        #     pass
        # self.driver.execute_script("""window.onkeydown = function(e) {if(e.keyCode == 32 && e.target.type != 'text' && e.target.type != 'textarea') {e.preventDefault();}};""")
        # time.sleep(5)
        browser_screenshot, browser_html = browse_and_capture("http://localhost:5000")

        # Get the standard prompt components
        system_inputs = self.get_prompt(
            task_description=state.task,
            prompt=prompt, 
            next_available_agents=next_available_agents
        )

        browser_inputs = [
            {"role": "user", "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{browser_screenshot}"
                    }
                }
            ]}
        ]

        # Combine memory with the fresh prompt
        inputs = memory_history + system_inputs + browser_inputs
        retry = 0
        while retry < 3:
            raw_response = self.llm.chat(inputs)
            
            self.logger.info(f"==========GUIAgent {self.agent_name} output: {raw_response}")

            try:
                output_state = self._parse_response(raw_response, document_manager, state)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state
