from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# MessageRole = Literal["user", "assistant", "system"]


class Message(BaseModel):
    """
    A message in the metaflow.
    """
    role: str
    thinking: str = Field(default="", description="The thinking of the agent")
    output: str = Field(default="", description="The answer of the agent")
    task_requirements: Optional[Dict[str, str]] = Field(default=None, description="The requirements for the task")
    next_agents: Optional[Union[str, List[str]]] = Field(default_factory=list, description="The next agents to execute")


class GeneralState(BaseModel):
    """
    The state of the metaflow.
    """
    task: str = Field(default="", description="The original user task, remains unchanged")
    # collaborative_document: Optional[Dict[str, Any]] = Field(default=None, description="A living document for negotiation, containing data and status.")
    sub_task: str = Field(default="", description="The specific sub-task for the current agent")
    code: str = Field(default="", description="The code of the agent")
    answer: str = Field(default="", description="The current answer given")
    message: Message = Field(default_factory=Message, description="The message of the agent")
