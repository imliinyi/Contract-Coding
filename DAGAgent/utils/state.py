import dataclasses
from typing import List, Literal, Dict, Optional, Union
from pydantic import BaseModel, Field


MessageRole = Literal["user", "assistant", "system"]


@dataclasses.dataclass
class Message(BaseModel):
    """
    A message in the DAGAgent.
    """
    role: MessageRole
    thinking: str = Field(default="", description="The thinking of the agent")
    output: str = Field(default="", description="The answer of the agent")

    def model_post_init(self, __context):
        if self.role not in MessageRole.__args__:
            raise ValueError(f"Role {self.role} not in {MessageRole.__args__}")


@dataclasses.dataclass
class GeneralState(BaseModel):
    """
    The state of the DAGAgent.
    """
    task: str = Field(default="", description="The user task")
    code: str = Field(default="", description="The code of the agent")
    answer: str = Field(default="", description="The current answer given")
    message: Message = Field(default_factory=Message, description="The message of the agent")
    next_agents: Optional[Union[str, List[str]]] = Field(default_factory=list, description="The next agents to execute")
