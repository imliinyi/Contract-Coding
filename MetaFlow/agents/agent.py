import json
import os
import re
import time
from typing import Dict, List

from pydantic import ValidationError
from selenium import webdriver
from selenium.webdriver.common.by import By

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.prompt.agent_prompt import GUI_PROMPT
from MetaFlow.tools.file_tool import file_tree
from MetaFlow.tools.process_tool import start_process
from MetaFlow.tools.backend_tool import start_backend_auto, start_static_preview
from MetaFlow.tools.browser_tool import capture_with_console
from MetaFlow.utils.exception import EmptyTaskRequirementsError
from MetaFlow.utils.gui.operator_utils import (
    exec_action_click,
    exec_action_scroll,
    exec_action_type,
)
from MetaFlow.utils.gui.utils import (
    clip_message_and_obs,
    encode_image,
    extract_information,
    get_web_element_rect,
)
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

            Current Project Structure:\n {file_tree('.')}

            Current Collaborative Document:\n {document_manager.get()}
            
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
        # inputs = memory_history + system_inputs
        inputs = system_inputs
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


class StaticAuditAgent(BaseAgent):
    """
    LLM-driven static validation agent that asks the model to provide the target URL,
    calls tools to start backend or static preview if needed, then captures screenshot + console logs.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: List[Dict[str, str]] = None, config: Config = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}

            Current Project Structure:\n {file_tree('.')}

            Current Collaborative Document:\n {document_manager.get()}

            IMPORTANT:
            - Decide the target URL to validate (prefer Server URL, then Preview URL, else propose one).
            - If backend is not running, call start_backend_auto; if it fails, call start_static_preview.
            - Finally call capture_with_console(url) to obtain screenshot + console logs for static validation.
            - Then provide a complete summary with <thinking>, <output>, and <task_requirements>.
        """

        inputs = self.get_prompt(
            task_description=state.task,
            prompt=prompt,
            next_available_agents=next_available_agents
        )

        tools = self.custom_tools or [start_backend_auto, start_static_preview, capture_with_console]
        raw_response = self.llm.chat_with_tools(messages=inputs, tools=tools)
        self.logger.info(f"==========StaticAuditAgent {self.agent_name} output: {raw_response}")

        output_state = self._parse_response(raw_response, document_manager, state)
        return output_state


class GUIAgent(BaseAgent):
    """
    GUIAgent drives a browser to verify UI and interact with pages.
    When initial navigation fails, it can invoke tools (e.g., start_process) to start the backend
    and then retry navigation.
    """
    def __init__(self, agent_name: str, agent_prompt: str, config: Config = None, custom_tools: List[Dict[str, str]] | None = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}

            Current Project Structure:\n {file_tree('.')}

            Current Collaborative Document: {document_manager.get()}
            
            IMPORTANT: When using tools, focus on the technical implementation. 
            After tool execution, the system will automatically generate a complete summary 
            including all required output sections (<thinking>, <output>, <task_requirements>, etc.).
        """
        # Get the standard prompt components
        system_inputs = self.get_prompt(
            task_description=state.task,
            prompt=prompt, 
            next_available_agents=next_available_agents
        )

        self.driver = webdriver.Chrome()
        self.driver.set_window_size(1920, 1080)
        try:
            # Fast initial attempt
            self.driver.set_page_load_timeout(6)
            self.driver.get("http://localhost:5000")
        except Exception as e:
            # Attempt to start backend via tools and retry
            self.logger.error(f"Initial navigation failed: {e}. Attempting to start backend via tools.")
            if self.custom_tools:
                startup_messages = [
                    {
                        "role": "system",
                        "content": "You MUST start the backend server on port 5000 by calling the start_process tool. Prefer uvicorn for FastAPI (module:app) or python app.py for Flask). If unsure, call file_tree/list_directory/read_file to locate entrypoint. Reply only after tool execution."
                    },
                    {
                        "role": "user",
                        "content": "Start backend at http://localhost:5000 now, then confirm briefly."
                    }
                ]
                try:
                    _ = self.llm.chat_with_tools(messages=startup_messages, tools=self.custom_tools)
                except Exception as tool_err:
                    self.logger.error(f"Tool start_process invocation failed: {tool_err}")
            # Fallback: programmatic discovery and direct start if tool path uncertain or not started
            def _discover_backend_entry() -> tuple[str | None, str | None]:
                candidates = [
                    os.path.join('test', 'workspace', 'main.py'),
                    os.path.join('test', 'workspace', 'backend.py'),
                    os.path.join('test', 'backend', 'app.py'),
                    'main.py', 'app.py', 'api.py', 'server.py'
                ]
                for p in candidates:
                    if os.path.exists(p):
                        try:
                            with open(p, 'r', encoding='utf-8') as f:
                                content = f.read()
                            wd = os.path.dirname(p) if os.path.dirname(p) else '.'
                            module = os.path.splitext(os.path.basename(p))[0]
                            if 'FastAPI' in content:
                                import re
                                m = re.search(r"(\w+)\s*=\s*FastAPI\(", content)
                                app_var = m.group(1) if m else 'app'
                                cmd = f"uvicorn {module}:{app_var} --port 5000 --reload"
                                return cmd, wd
                            # Flask fallback
                            if 'Flask(' in content:
                                return f"python {os.path.basename(p)}", wd
                            # Generic fallback to uvicorn
                            return f"uvicorn {module}:app --port 5000 --reload", wd
                        except Exception:
                            continue
                return None, None
            cmd, wd = _discover_backend_entry()
            if cmd:
                try:
                    res = start_process(command=cmd, working_directory=wd)
                    self.logger.info(f"Backend start attempt: {res}")
                except Exception as se:
                    self.logger.error(f"Direct start_process failed: {se}")
            # Retry navigation with short backoff
            for _ in range(3):
                time.sleep(3)
                try:
                    self.driver.get("http://localhost:5000")
                    break
                except Exception as e2:
                    self.logger.error(f"Navigation retry failed: {e2}")
        try:
            self.driver.find_element(By.TAG_NAME, 'body').click()
        except Exception:
            pass
        self.driver.execute_script("""window.onkeydown = function(e) {if(e.keyCode == 32 && e.target.type != 'text' && e.target.type != 'textarea') {e.preventDefault();}};""")
        time.sleep(2)

        # Combine memory with the fresh prompt
        inputs = system_inputs
        gui_inputs = [
            {
                "role": "system",
                "content": GUI_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"The Overall Task: {state.task} \nCurrent Sub-Task: {state.sub_task} \nCurrent Project Structure: {file_tree('.')} \nThe Current Collaborative Document: {document_manager.get()}"
                    }
                ]
            }
        ]
        retry = 0
        fail_obs = ""

        while retry < 5:
            if not fail_obs:
                try:
                    reacts, web_eles, web_eles_text = get_web_element_rect(self.driver)
                except Exception as e:
                    fail_obs = f"Error: {e}"

                img_path = os.path.join('screenshots', f'{self.agent_name}_{retry}.png')
                self.driver.save_screenshot(img_path)

            base64_img = encode_image(img_path)

            browser_inputs = [
                {"role": "user", "content": [
                    {
                        "type": "text",
                        "text": f"The current Web page is shown in the image. Please analyze the attached screenshot and give the Thought and Action. I've provided the tag name of each element and the text it contains (if text exists). Note that <textarea> or <input> may be textbox, but not exactly. Please focus more on the screenshot and then refer to the textual information.\n{web_eles_text}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_img}"
                        }
                    }
                ]}
            ]

            gui_inputs.extend(browser_inputs)
            messages = clip_message_and_obs(gui_inputs, 3)
            
            raw_response = self.llm.chat(messages)
            
            self.logger.info(f"==========GUIAgent {self.agent_name} output: {raw_response}")

            retry += 1
            messages.append({"role": "assistant", "content": raw_response})
            if reacts:
                for react_eles in reacts:
                    self.driver.execute_script(f"arguments[0].remove();", react_eles)
                reacts = []
            pattern = r'Thought:|Action:|Observation:'
            chosen_action = re.split(pattern, raw_response)[2].strip()
            action_key, info = extract_information(chosen_action)

            try:
                window_handle_task = self.driver.current_window_handle
                self.driver.switch_to.window(window_handle_task)

                if action_key == "click":
                    click_ele_number = int(info[0])
                    web_ele = web_eles[click_ele_number]

                    ele_tag_name = web_ele.tag_name.lower()
                    ele_type = web_ele.get_attribute("type")

                    exec_action_click(info, web_ele, self.driver)

                    if ele_tag_name == "button" and ele_type == "submit":
                        time.sleep(5)
                
                elif action_key == "wait":
                    time.sleep(5)

                elif action_key == "type":
                    type_ele_number = int(info['number'])
                    web_ele = web_eles[type_ele_number]

                    warn_obs = exec_action_type(info, web_ele, self.driver)

                elif action_key == "scroll":
                    exec_action_scroll(info, web_eles, self.driver, 1080)
                
                elif action_key == "goback":
                    self.driver.back()
                    time.sleep(2)
                
                elif action_key == "answer":
                    break

                else:
                    break

            except Exception as e:
                self.logger.error(f"Error switching to window handle: {e}")

            self.driver.quit()

        retry = 0
        while retry < 3:
            inputs.extend(messages)
            raw_response = self.llm.chat(inputs)
            
            self.logger.info(f"==========GUIAgent {self.agent_name} output: {raw_response}")
            
            try:
                output_state = self._parse_response(raw_response, document_manager, state)
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1

        return output_state
