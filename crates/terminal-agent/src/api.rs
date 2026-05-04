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

#[derive(Debug, Deserialize)]
pub struct ModelsResponse {
    pub models: Vec<Value>,
}

#[derive(Debug, Deserialize)]
pub struct ToolsResponse {
    pub tools: Vec<Value>,
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
    Done,
    Error {
        message: String,
    },
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
            let Ok(obj) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            let event_type = obj.get("type").and_then(Value::as_str).unwrap_or("");
            return Some(match event_type {
                "delta" => StreamEvent::Delta {
                    content: obj["content"].as_str().unwrap_or("").to_string(),
                },
                "thinking" => StreamEvent::Thinking {
                    content: obj["content"].as_str().unwrap_or("").to_string(),
                },
                "tool_call" => StreamEvent::ToolCall {
                    name: obj["name"].as_str().unwrap_or("").to_string(),
                    arguments: obj["arguments"].as_str().unwrap_or("{}").to_string(),
                },
                "tool_call_start" => StreamEvent::ToolCallStart {
                    index: obj["index"].as_u64().unwrap_or(0) as u32,
                    name: obj["name"].as_str().unwrap_or("").to_string(),
                },
                "tool_call_delta" => StreamEvent::ToolCallDelta {
                    index: obj["index"].as_u64().unwrap_or(0) as u32,
                    name: obj["name"].as_str().unwrap_or("").to_string(),
                    delta: obj["delta"].as_str().unwrap_or("").to_string(),
                },
                "tool_result" => StreamEvent::ToolResult {
                    name: obj["name"].as_str().unwrap_or("").to_string(),
                    result: obj["result"].as_str().unwrap_or("").to_string(),
                },
                "model_info" => StreamEvent::ModelInfo {
                    model: obj["model"].as_str().unwrap_or("").to_string(),
                },
                "notice" => StreamEvent::Notice {
                    level: obj["level"].as_str().unwrap_or("info").to_string(),
                    message: obj["message"].as_str().unwrap_or("").to_string(),
                },
                "tool_pending" => StreamEvent::ToolPending {
                    decision_id: obj["decision_id"].as_str().unwrap_or("").to_string(),
                    name: obj["name"].as_str().unwrap_or("").to_string(),
                    arguments: obj["arguments"].as_str().unwrap_or("{}").to_string(),
                    remember_supported: obj["remember_supported"].as_bool().unwrap_or(false),
                    warning: obj["warning"].as_str().unwrap_or("").to_string(),
                },
                "done" => StreamEvent::Done,
                "error" => StreamEvent::Error {
                    message: obj["message"]
                        .as_str()
                        .unwrap_or("unknown error")
                        .to_string(),
                },
                _ => continue,
            });
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

    pub fn inference_models(&self) -> Result<ModelsResponse> {
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
