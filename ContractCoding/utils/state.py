from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# MessageRole = Literal["user", "assistant", "system"]


class GeneralState(BaseModel):
    """
    The state of the metaflow.
    """
    task: str = Field(default="", description="The original user task, remains unchanged")
    sub_task: str = Field(default="", description="The specific sub-task for the current agent")
    
    # Message 
    role: str = Field(default="", description="The role of the agent that produced this state")
    thinking: str = Field(default="", description="The thinking process of the agent")
    output: str = Field(default="", description="The primary output of the agent")
    task_requirements: Optional[Dict[str, Any]] = Field(default=None, description="The requirements for subsequent tasks")
    next_agents: Optional[Union[str, List[str]]] = Field(default_factory=list, description="The next agent(s) to execute")
