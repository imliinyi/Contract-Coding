from typing import List

from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.config import Config
from MetaFlow.utils.state import GeneralState, Message
from MetaFlow.tools.search_tool import search_web

class TechnicalWriterAgent(LLMAgent):
    """
    The Technical Writer agent specializes in generating clear and concise documentation.
    This includes creating README.md files, writing API documentation, explaining complex code,
    and drafting academic papers.
    """
    def __init__(self, config: Config):
        super().__init__("Technical_Writer", config)


class ResearcherAgent(ActionAgent):
    """
    The Researcher agent is the system's information gatherer. 
    When other agents require external knowledge, it is responsible for searching the web, 
    consulting documentation, or looking up academic materials to provide a basis for decisions.
    """
    def __init__(self, config: Config):
        tools = [search_web]
        super().__init__("Researcher", config, tools)
