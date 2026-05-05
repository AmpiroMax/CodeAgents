use anyhow::{Context, Result};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct ApiClient {
    base_url: String,
    client: Client,
}

#[derive(Debug, Deserialize)]
pub struct HealthResponse {
    pub ok: bool,
}

#[derive(Debug, Deserialize)]
pub struct ChatResponse {
    pub answer: String,
    #[allow(dead_code)]
    pub chat_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ToolResponse {
    pub tool: String,
    pub confirmation_required: bool,
    pub result: Value,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ConfigModel {
    pub key: String,
    pub name: String,
    pub role: String,
    #[serde(default)]
    pub context_tokens: Option<u64>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ModelsResponse {
    pub models: Vec<ConfigModel>,
}

/// Row from `GET /inference/models` (registry + discovered Ollama models).
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct InferenceModelEntry {
    pub key: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub profile: String,
    #[serde(default)]
    pub runtime_model: String,
    #[serde(default)]
    pub notes: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InferenceModelsResponse {
    pub models: Vec<InferenceModelEntry>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ToolInfo {
    pub name: String,
    pub kind: String,
    pub permission: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub description: String,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ToolsResponse {
    pub tools: Vec<ToolInfo>,
}

#[derive(Debug, Serialize)]
pub struct ChatPayload<'a> {
    pub prompt: &'a str,
    pub task: &'a str,
    pub workspace: &'a str,
}

#[derive(Debug, Clone, Serialize)]
pub struct ContentBlock {
    #[serde(rename = "type")]
    pub content_type: String,
    pub text: String,
}

impl<'de> serde::Deserialize<'de> for ContentBlock {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let raw: Value = Value::deserialize(deserializer)?;
        let content_type = raw
            .get("type")
            .and_then(|v| v.as_str())
            .unwrap_or("text")
            .to_string();
        let text = raw
            .get("text")
            .or_else(|| raw.get("thinking"))
            .or_else(|| raw.get("function"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| {
                if let Some(fc) = raw.get("function_call") {
                    serde_json::to_string(fc).unwrap_or_default()
                } else {
                    String::new()
                }
            });
        Ok(ContentBlock { content_type, text })
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatMessage {
    pub role: String,
    pub index: usize,
    pub content: Vec<ContentBlock>,
}

#[derive(Debug, Serialize)]
pub struct StructuredChat {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub messages: Vec<ChatMessage>,
    pub meta: Value,
}

#[derive(Debug, Deserialize)]
pub struct ChatsResponse {
    pub chats: Vec<Value>,
}

#[derive(Debug, Deserialize)]
pub struct ChatEnvelope {
    pub chat: StoredChat,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StoredChat {
    pub id: Option<String>,
    pub messages: Vec<ChatMessage>,
    pub meta: Value,
}

#[derive(Debug, Serialize)]
pub struct StructuredChatPayload<'a> {
    pub chat: StructuredChat,
    pub task: &'a str,
    pub workspace: &'a str,
}

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Delta {
        content: String,
    },
    Thinking {
        content: String,
    },
    ToolCall {
        name: String,
        arguments: String,
    },
    ToolCallStart {
        index: u32,
        name: String,
    },
    ToolCallDelta {
        index: u32,
        name: String,
        delta: String,
    },
    ToolResult {
        name: String,
        result: String,
    },
    ToolPending {
        decision_id: String,
        name: String,
        arguments: String,
        remember_supported: bool,
        warning: String,
    },
    ModelInfo {
        model: String,
    },
    Notice {
        level: String,
        message: String,
    },
    Done {
        model: String,
        stop_reason: String,
    },
    TerminalOutput {
        session_id: String,
        chunk: String,
    },
    Error {
        message: String,
    },
}

/// Wire-format NDJSON events from the Python agent (matches `stream_events.py`).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type")]
pub enum AgentNdjsonEvent {
    #[serde(rename = "model_info")]
    ModelInfo { model: String },
    #[serde(rename = "delta")]
    Delta {
        #[serde(default)]
        content: String,
    },
    #[serde(rename = "thinking")]
    Thinking {
        #[serde(default)]
        content: String,
    },
    #[serde(rename = "tool_call_start")]
    ToolCallStart {
        index: u64,
        #[serde(default)]
        name: String,
    },
    #[serde(rename = "tool_call_delta")]
    ToolCallDelta {
        index: u64,
        #[serde(default)]
        name: String,
        #[serde(default)]
        delta: String,
    },
    #[serde(rename = "tool_call")]
    ToolCall {
        name: String,
        #[serde(default)]
        arguments: String,
    },
    #[serde(rename = "tool_result")]
    ToolResult {
        name: String,
        #[serde(default)]
        result: String,
    },
    #[serde(rename = "tool_pending")]
    ToolPending {
        decision_id: String,
        name: String,
        #[serde(default)]
        arguments: String,
        #[serde(default)]
        remember_supported: bool,
        #[serde(default)]
        warning: String,
    },
    #[serde(rename = "notice")]
    Notice {
        #[serde(default)]
        level: String,
        #[serde(default)]
        message: String,
    },
    #[serde(rename = "done")]
    Done {
        #[serde(default)]
        model: String,
        #[serde(default)]
        stop_reason: String,
    },
    #[serde(rename = "error")]
    Error { message: String },
    #[serde(rename = "terminal_output")]
    TerminalOutput {
        #[serde(default)]
        session_id: String,
        #[serde(default)]
        chunk: String,
    },
}

impl From<AgentNdjsonEvent> for StreamEvent {
    fn from(ev: AgentNdjsonEvent) -> Self {
        match ev {
            AgentNdjsonEvent::ModelInfo { model } => StreamEvent::ModelInfo { model },
            AgentNdjsonEvent::Delta { content } => StreamEvent::Delta { content },
            AgentNdjsonEvent::Thinking { content } => StreamEvent::Thinking { content },
            AgentNdjsonEvent::ToolCallStart { index, name } => StreamEvent::ToolCallStart {
                index: index as u32,
                name,
            },
            AgentNdjsonEvent::ToolCallDelta { index, name, delta } => {
                StreamEvent::ToolCallDelta {
                    index: index as u32,
                    name,
                    delta,
                }
            }
            AgentNdjsonEvent::ToolCall { name, arguments } => StreamEvent::ToolCall {
                name,
                arguments,
            },
            AgentNdjsonEvent::ToolResult { name, result } => StreamEvent::ToolResult { name, result },
            AgentNdjsonEvent::ToolPending {
                decision_id,
                name,
                arguments,
                remember_supported,
                warning,
            } => StreamEvent::ToolPending {
                decision_id,
                name,
                arguments,
                remember_supported,
                warning,
            },
            AgentNdjsonEvent::Notice { level, message } => StreamEvent::Notice { level, message },
            AgentNdjsonEvent::Done { model, stop_reason } => StreamEvent::Done {
                model,
                stop_reason,
            },
            AgentNdjsonEvent::Error { message } => StreamEvent::Error { message },
            AgentNdjsonEvent::TerminalOutput { session_id, chunk } => {
                StreamEvent::TerminalOutput { session_id, chunk }
            }
        }
    }
}

pub struct NdjsonStream {
    reader: BufReader<reqwest::blocking::Response>,
}

impl Iterator for NdjsonStream {
    type Item = StreamEvent;

    fn next(&mut self) -> Option<StreamEvent> {
        loop {
            let mut line = String::new();
            match self.reader.read_line(&mut line) {
                Ok(0) => return None,
                Err(_) => return None,
                Ok(_) => {}
            }
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(ev) = serde_json::from_str::<AgentNdjsonEvent>(line) else {
                continue;
            };
            return Some(ev.into());
        }
    }
}

impl ApiClient {
    pub fn new(base_url: impl Into<String>) -> Result<Self> {
        let client = Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(180))
            .build()
            .context("failed to build HTTP client")?;
        Ok(Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client,
        })
    }

    pub fn health(&self) -> Result<HealthResponse> {
        self.get("/health")
    }

    pub fn models(&self) -> Result<ModelsResponse> {
        self.get("/models")
    }

    pub fn inference_models(&self) -> Result<InferenceModelsResponse> {
        self.get("/inference/models")
    }

    pub fn tools(&self) -> Result<ToolsResponse> {
        self.get("/tools")
    }

    pub fn chats(&self) -> Result<ChatsResponse> {
        self.get("/chats")
    }

    pub fn load_chat(&self, chat_id: &str) -> Result<ChatEnvelope> {
        self.get(&format!("/chats/{chat_id}"))
    }

    pub fn confirm_tool(&self, decision_id: &str, approved: bool, remember: bool) -> Result<()> {
        let _: Value = self.post(
            "/chat/confirm",
            &json!({ "decision_id": decision_id, "approved": approved, "remember": remember }),
        )?;
        Ok(())
    }

    pub fn create_chat(&self, title: &str, workspace: &str) -> Result<ChatEnvelope> {
        self.post(
            "/chats",
            &json!({
                "title": title,
                "meta": {
                    "workspace": workspace,
                    "client": "ca-tui"
                }
            }),
        )
    }

    pub fn chat(&self, prompt: &str, task: &str, workspace: &str) -> Result<ChatResponse> {
        self.post(
            "/chat",
            &ChatPayload {
                prompt,
                task,
                workspace,
            },
        )
    }

    pub fn chat_stream(
        &self,
        chat_id: Option<String>,
        messages: Vec<ChatMessage>,
        meta: Value,
        task: &str,
        workspace: &str,
    ) -> Result<NdjsonStream> {
        let payload = StructuredChatPayload {
            chat: StructuredChat {
                id: chat_id,
                messages,
                meta,
            },
            task,
            workspace,
        };
        let url = format!("{}/chat/stream", self.base_url);
        // No overall timeout: a single agent turn can take many minutes when
        // the model writes a large file or does a long reasoning trace. We
        // rely on (a) the connect_timeout, (b) per-read deadlines on the
        // underlying TCP socket, and (c) the user's Ctrl+S to stop.
        let stream_client = Client::builder()
            .no_proxy()
            .connect_timeout(Duration::from_secs(15))
            // Total request budget: 30 minutes. Long agent turns (large file
            // generation, multi-step reasoning) easily exceed the old 5-min cap.
            .timeout(Duration::from_secs(1800))
            .build()
            .context("failed to build streaming client")?;
        let response = stream_client
            .post(&url)
            .json(&payload)
            .send()
            .with_context(|| format!("cannot connect to {url} — is 'ca serve' running?"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().unwrap_or_default();
            anyhow::bail!("server returned {status}: {body}");
        }
        Ok(NdjsonStream {
            reader: BufReader::new(response),
        })
    }

    pub fn tool(&self, name: &str, arguments: Value) -> Result<ToolResponse> {
        self.post("/tool", &json!({ "name": name, "arguments": arguments }))
    }

    pub fn index(&self, path: &str) -> Result<Value> {
        self.post("/index", &json!({ "path": path }))
    }

    fn get<T>(&self, path: &str) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        let url = format!("{}{}", self.base_url, path);
        let response = self.client.get(url).send().context("request failed")?;
        decode_response(response)
    }

    fn post<T, B>(&self, path: &str, body: &B) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
        B: Serialize + ?Sized,
    {
        let url = format!("{}{}", self.base_url, path);
        let response = self
            .client
            .post(url)
            .json(body)
            .send()
            .context("request failed")?;
        decode_response(response)
    }
}

fn decode_response<T>(response: reqwest::blocking::Response) -> Result<T>
where
    T: for<'de> Deserialize<'de>,
{
    let status = response.status();
    let body = response.text().context("failed to read response body")?;
    if !status.is_success() {
        anyhow::bail!("server returned {status}: {body}");
    }
    serde_json::from_str(&body).context("failed to decode response")
}
