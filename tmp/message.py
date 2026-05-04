from typing import Any, List, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, Field

from .content import (
    AudioContent,
    FunctionCallContent,
    FunctionContent,
    ImageContent,
    TextContent,
    ThinkingContent,
    ThinkingFunctionCallContent,
    VideoContent,
    FileContent,
)
from .utils import compute_hash


class BaseMessage(BaseModel):
    """Base message model"""

    id: str = ""
    """Message id"""

    role: str
    """Message role"""

    content: List[Any]
    """Message content"""

    index: int = Field(alias="index", validation_alias=AliasChoices("index", "message_index"))
    """Index message in chat"""

    def model_post_init(self, context: Any):  # pylint: disable=arguments-differ
        if not self.id:
            self.id = compute_hash(self)
        return super().model_post_init(context)


class SystemMessage(BaseMessage):
    """System message model"""

    role: Literal["system"] = "system"
    """Message role"""

    content: List[Union[TextContent, AudioContent, ImageContent]]
    """Message content"""


class UserMessage(BaseMessage):
    """User message model"""

    role: Literal["user"] = "user"
    """Message role"""

    content: List[Union[TextContent, ImageContent, AudioContent, VideoContent, FileContent]]
    """Message content"""

    functions: Optional[List[str]] = None
    """Avaliable functions"""

    thinking_functions: Optional[List[str]] = None
    """Avaliable functions"""


class FunctionMessage(BaseMessage):
    """Function message model"""

    role: Literal["function"] = "function"
    """Message role"""

    content: List[Union[FunctionContent, AudioContent, ImageContent]]
    """Message content"""

    functions: Optional[List[str]] = None
    """Avaliable functions"""

    thinking_functions: Optional[List[str]] = None
    """Avaliable functions"""


class AssistantMessage(BaseMessage):
    """Assistant message model"""

    role: Literal["assistant"] = "assistant"
    """Message role"""

    content: List[Union[TextContent, FunctionCallContent, ThinkingContent, ThinkingFunctionCallContent, AudioContent]]
    """Message content"""
