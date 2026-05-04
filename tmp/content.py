from typing import Any, Literal

from pydantic import BaseModel

from .types import FunctionCall, File, Dict, Optional


class BaseContent(BaseModel):
    trainable: bool = False

    meta: Optional[Dict[str, Any]] = None

    def get_content(self) -> Any:
        raise NotImplementedError


##################################### TEXT #####################################


class TextContent(BaseContent):
    """Message text content model"""

    type: Literal["text"] = "text"
    """Data type"""

    text: str
    """Text content data"""

    def get_content(self) -> str:
        return self.text


################################### FUNCTION ###################################


class FunctionCallContent(BaseContent):
    """Function call content model"""

    type: Literal["function_call"] = "function_call"
    """Data type"""

    function_call: FunctionCall
    """Function call data"""

    def get_content(self) -> FunctionCall:
        return self.function_call


class ThinkingFunctionCallContent(BaseContent):
    """Function call content model"""

    type: Literal["thinking_function_call"] = "thinking_function_call"
    """Data type"""

    function_call: FunctionCall
    """Function call data"""

    def get_content(self) -> FunctionCall:
        return self.function_call


class FunctionContent(BaseContent):
    """Function content model"""

    type: Literal["function"] = "function"
    """Data type"""

    function: str
    """Function data"""

    def get_content(self) -> str:
        return self.function


################################ MULTIMODALITY #################################


class ImageContent(BaseContent):
    """Image content model"""

    type: Literal["image"] = "image"
    """Data type"""

    image: str
    """Image url"""

    def get_content(self) -> str:
        return self.image


class AudioContent(BaseContent):
    """Audio content model"""

    type: Literal["audio"] = "audio"
    """Data type"""

    audio: str
    """Audio url"""

    def get_content(self) -> str:
        return self.audio


class VideoContent(BaseContent):
    """Video content model"""

    type: Literal["video"] = "video"
    """Data type"""

    video: str
    """Video url"""

    def get_content(self) -> str:
        return self.video


################################## ADDITIONAL ##################################


class ThinkingContent(BaseContent):
    """Thinking content model"""

    type: Literal["thinking"] = "thinking"
    """Data type"""

    thinking: str
    """Thinking data"""

    def get_content(self) -> str:
        return self.thinking


class FileContent(BaseContent):
    """Thinking content model"""

    type: Literal["file"] = "file"
    """Data type"""

    file: File
    """Thinking data"""

    def get_content(self) -> str:
        return self.file.model_dump()
