from typing import Any, List, Optional, Union

from pydantic import BaseModel

from .message import AssistantMessage, FunctionMessage, SystemMessage, UserMessage
from .types import Function
from .utils import compute_hash


class Chat(BaseModel):
    """Chat model"""

    id: str = ""
    """Chat id"""

    messages: List[Union[SystemMessage, UserMessage, AssistantMessage, FunctionMessage]]
    """List of chat messages"""

    meta: Any
    """Chat meta"""

    functions: Optional[List[Function]] = None
    """List of chat available functions"""

    def model_post_init(self, __context: Any) -> Any:  # pylint: disable=arguments-differ
        if not self.id:
            self.id = compute_hash([_.id for _ in self.messages] + [self.functions])
        return super().model_post_init(__context)

    def to_dict(self) -> dict:
        item = self.model_dump(exclude_none=True)
        for message in item["messages"]:
            message["chat_id"] = item["id"]
        return item


class RLChat(Chat):
    """RL chat model"""

    chosen: AssistantMessage
    """Chosen answer"""

    rejected: AssistantMessage
    """Rejected answer"""
