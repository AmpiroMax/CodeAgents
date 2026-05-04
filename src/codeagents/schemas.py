from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


def stable_hash(value: Any) -> str:
    raw = json.dumps(_dump(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class FunctionParameter(BaseModel):
    name: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    description: str = ""
    required: bool = True

    model_config = ConfigDict(populate_by_name=True)


class FunctionSpec(BaseModel):
    name: str
    description: str = ""
    parameters: list[FunctionParameter] = Field(default_factory=list)

    def to_json_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in self.parameters:
            properties[parameter.name] = {
                **parameter.schema_,
                "description": parameter.description,
            }
            if parameter.required:
                required.append(parameter.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }


class FunctionCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = stable_hash({"name": self.name, "arguments": self.arguments})


class FileRef(BaseModel):
    path: str
    media_type: str | None = None
    content: str | None = None


class BaseContent(BaseModel):
    trainable: bool = False
    meta: dict[str, Any] | None = None

    def as_text(self) -> str:
        raise NotImplementedError


class TextContent(BaseContent):
    type: Literal["text"] = "text"
    text: str

    def as_text(self) -> str:
        return self.text


class ThinkingContent(BaseContent):
    type: Literal["thinking"] = "thinking"
    thinking: str

    def as_text(self) -> str:
        return self.thinking


class FunctionCallContent(BaseContent):
    type: Literal["function_call"] = "function_call"
    function_call: FunctionCall

    def as_text(self) -> str:
        return json.dumps(self.function_call.model_dump(), ensure_ascii=False)


class ThinkingFunctionCallContent(BaseContent):
    type: Literal["thinking_function_call"] = "thinking_function_call"
    function_call: FunctionCall

    def as_text(self) -> str:
        return json.dumps(self.function_call.model_dump(), ensure_ascii=False)


class FunctionContent(BaseContent):
    type: Literal["function"] = "function"
    function: str

    def as_text(self) -> str:
        return self.function


class ImageContent(BaseContent):
    type: Literal["image"] = "image"
    image: str

    def as_text(self) -> str:
        return self.image


class AudioContent(BaseContent):
    type: Literal["audio"] = "audio"
    audio: str

    def as_text(self) -> str:
        return self.audio


class VideoContent(BaseContent):
    type: Literal["video"] = "video"
    video: str

    def as_text(self) -> str:
        return self.video


class FileContent(BaseContent):
    type: Literal["file"] = "file"
    file: FileRef

    def as_text(self) -> str:
        return self.file.model_dump_json(exclude_none=True)


Content = Annotated[
    TextContent
    | ThinkingContent
    | FunctionCallContent
    | ThinkingFunctionCallContent
    | FunctionContent
    | ImageContent
    | AudioContent
    | VideoContent
    | FileContent,
    Field(discriminator="type"),
]


class BaseMessage(BaseModel):
    id: str = ""
    role: str
    content: list[Content]
    index: int = Field(default=0, validation_alias=AliasChoices("index", "message_index"))

    model_config = ConfigDict(populate_by_name=True)

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = stable_hash(
                {
                    "role": self.role,
                    "content": [item.model_dump(mode="json") for item in self.content],
                    "index": self.index,
                }
            )

    def text(self) -> str:
        return "\n".join(item.as_text() for item in self.content)

    def to_openai(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.text()}


class SystemMessage(BaseMessage):
    role: Literal["system"] = "system"
    content: list[TextContent | AudioContent | ImageContent]


class UserMessage(BaseMessage):
    role: Literal["user"] = "user"
    content: list[TextContent | ImageContent | AudioContent | VideoContent | FileContent]
    functions: list[str] | None = None
    thinking_functions: list[str] | None = None


class AssistantMessage(BaseMessage):
    role: Literal["assistant"] = "assistant"
    content: list[
        TextContent
        | FunctionCallContent
        | ThinkingContent
        | ThinkingFunctionCallContent
        | AudioContent
    ]
    model: str | None = None


class FunctionMessage(BaseMessage):
    role: Literal["function"] = "function"
    content: list[FunctionContent | AudioContent | ImageContent]
    name: str | None = None
    function_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        role = "tool" if self.function_call_id else "function"
        payload: dict[str, Any] = {"role": role, "content": self.text()}
        if self.name:
            payload["name"] = self.name
        if self.function_call_id:
            payload["tool_call_id"] = self.function_call_id
        return payload


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | FunctionMessage,
    Field(discriminator="role"),
]


class Chat(BaseModel):
    id: str = ""
    messages: list[Message]
    meta: dict[str, Any] = Field(default_factory=dict)
    functions: list[FunctionSpec] | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = stable_hash(
                {
                    "messages": [message.id for message in self.messages],
                    "functions": [item.model_dump(mode="json") for item in self.functions or []],
                }
            )

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        *,
        system: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "Chat":
        messages: list[Message] = []
        index = 0
        if system:
            messages.append(
                SystemMessage(index=index, content=[TextContent(text=system)])
            )
            index += 1
        messages.append(UserMessage(index=index, content=[TextContent(text=prompt)]))
        return cls(messages=messages, meta=meta or {})

    def to_openai_messages(self) -> list[dict[str, Any]]:
        return [message.to_openai() for message in self.messages]


class InferenceRequest(BaseModel):
    chat: Chat
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    chat_id: str
    model: str
    assistant: AssistantMessage
    elapsed_seconds: float
    raw: dict[str, Any] = Field(default_factory=dict)


class BatchInferenceRequest(BaseModel):
    requests: list[InferenceRequest]
    meta: dict[str, Any] = Field(default_factory=dict)


class BatchInferenceResponse(BaseModel):
    responses: list[InferenceResponse]


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value
