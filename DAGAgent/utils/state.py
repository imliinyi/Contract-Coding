import dataclasses
from typing import List, Union, Literal


MessageRole = Literal["user", "assistant", "system"]


@dataclasses.dataclass
class Message:
    role: MessageRole
    content: List[dict] | str
