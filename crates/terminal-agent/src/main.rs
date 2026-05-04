mod api;
mod tui;

use anyhow::{Context, Result};
use api::ApiClient;
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use tui::TuiApp;

const DEFAULT_API: &str = "http://127.0.0.1:8765";

#[derive(Debug, Parser)]
#[command(name = "ca")]
#[command(about = "Local terminal coding agent powered by CodeAgents")]
struct Cli {
    #[arg(long, default_value = DEFAULT_API, global = true)]
    api: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Create a new chat and open the TUI.
    New {
        /// Chat name.
        name: String,
        #[arg(long, default_value = "code")]
        task: String,
    },
    /// Open an existing chat by name and launch the TUI.
    Open {
        /// Chat name (or id).
        name: String,
        #[arg(long, default_value = "code")]
        task: String,
    },
    /// Ask a one-shot question (no TUI).
    Ask {
        prompt: String,
        #[arg(long, default_value = "general")]
        task: String,
    },
    /// Ask a coding question (no TUI).
    Code { prompt: String },
    /// Check CodeAgents API health.
    Health,
    /// List available models (config + Ollama).
    Models,
    /// Show generation-parameter config files for each model.
    Params {
        /// Optional model name (e.g. "gpt-oss:20b") to print just one config.
        model: Option<String>,
    },
    /// List all saved chats (name + id).
    Chats,
    /// List available tools.
    Tools,
    /// Build a code index through the API.
    Index {
        #[arg(default_value = ".")]
        path: String,
    },
    /// Call a backend tool directly.
    Tool {
        name: String,
        #[arg(default_value = "{}")]
        arguments: String,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let client = ApiClient::new(cli.api)?;
    let cwd = std::env::current_dir()
        .context("failed to read current directory")?
        .display()
        .to_string();

    match cli.command {
        Commands::New { name, task } => {
            let chat = client.create_chat(&name, &cwd)?;
            let chat_id = chat.chat.id.unwrap_or_default();
            let mut app = TuiApp::new(client, task)?;
            app.set_chat(chat_id, name);
            app.run()
        }
        Commands::Open { name, task } => {
            let chat_id = find_chat_by_name(&client, &name)?;
            let mut app = TuiApp::new(client, task)?;
            app.open_chat_by_id(&chat_id)?;
            app.run()
        }
        Commands::Ask { prompt, task } => {
            let response = client.chat(&prompt, &task, &cwd)?;
            println!("{}", response.answer);
            Ok(())
        }
        Commands::Code { prompt } => {
            let response = client.chat(&prompt, "code", &cwd)?;
            println!("{}", response.answer);
            Ok(())
        }
        Commands::Health => {
            let response = client.health()?;
            println!("ok={}", response.ok);
            Ok(())
        }
        Commands::Models => {
            let response = client.models()?;
            println!("Config profiles:");
            print_json(&response.models)?;
            println!();
            if let Ok(inf) = client.inference_models() {
                println!("Ollama models:");
                print_json(&inf.models)?;
            }
            Ok(())
        }
        Commands::Params { model } => {
            let dir = locate_params_dir();
            println!("Model param dir: {}", dir.display());
            if let Some(model) = model {
                let path = dir.join(format!("{}.toml", sanitize_model(&model)));
                println!("File: {}", path.display());
                match std::fs::read_to_string(&path) {
                    Ok(text) => println!("\n{}", text),
                    Err(_) => println!("(missing — start `ca serve` to auto-create it)"),
                }
            } else {
                let entries: Vec<_> = std::fs::read_dir(&dir)
                    .map(|rd| {
                        rd.flatten()
                            .filter(|e| {
                                e.path().extension().and_then(|x| x.to_str()) == Some("toml")
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                if entries.is_empty() {
                    println!(
                        "No model params yet. Run `ca serve` (it auto-creates a config per Ollama model)."
                    );
                } else {
                    for entry in entries {
                        println!("  {}", entry.path().display());
                    }
                }
            }
            Ok(())
        }
        Commands::Chats => {
            let chats = client
                .chats()
                .context("failed to list chats — is ca serve running?")?;
            if chats.chats.is_empty() {
                println!("No chats yet. Create one with: ca new <name>");
            } else {
                println!("{:<30} {}", "NAME", "ID");
                println!("{}", "─".repeat(62));
                for c in &chats.chats {
                    let title = c.get("title").and_then(|v| v.as_str()).unwrap_or("?");
                    let id = c.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                    println!("{:<30} {}", title, id);
                }
            }
            Ok(())
        }
        Commands::Tools => {
            let response = client.tools()?;
            print_json(&response.tools)
        }
        Commands::Index { path } => {
            let response = client.index(&path)?;
            print_json(&response)
        }
        Commands::Tool { name, arguments } => {
            let arguments: Value =
                serde_json::from_str(&arguments).context("tool arguments must be JSON")?;
            let response = client.tool(&name, arguments)?;
            print_json(&json!({
                "tool": response.tool,
                "confirmation_required": response.confirmation_required,
                "result": response.result,
            }))
        }
    }
}

fn sanitize_model(name: &str) -> String {
    name.replace(':', "-").replace('/', "_")
}

fn locate_params_dir() -> std::path::PathBuf {
    // Search upwards from cwd for config/model_params/, fallback to repo default.
    let mut cur = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    for _ in 0..6 {
        let candidate = cur.join("config").join("model_params");
        if candidate.exists() {
            return candidate;
        }
        if !cur.pop() {
            break;
        }
    }
    std::path::PathBuf::from("config/model_params")
}

fn find_chat_by_name(client: &ApiClient, name: &str) -> Result<String> {
    let chats = client
        .chats()
        .context("failed to list chats — is ca serve running?")?;
    for chat in &chats.chats {
        let title = chat.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let id = chat.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if title == name || id == name {
            return Ok(id.to_string());
        }
    }
    anyhow::bail!(
        "chat \"{}\" not found. Available chats:\n{}",
        name,
        chats
            .chats
            .iter()
            .filter_map(|c| {
                let t = c.get("title").and_then(|v| v.as_str())?;
                let id = c.get("id").and_then(|v| v.as_str())?;
                Some(format!("  {} ({})", t, id))
            })
            .collect::<Vec<_>>()
            .join("\n")
    );
}

fn print_json<T>(value: &T) -> Result<()>
where
    T: serde::Serialize,
{
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}
