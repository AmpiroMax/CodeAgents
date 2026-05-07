use std::{
    collections::BTreeMap,
    env,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::Duration,
};

use anyhow::{Context, Result, anyhow};
use clap::{Parser, Subcommand};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;

const OLLAMA_PORT: u16 = 11434;
const CODEAGENTS_PORT: u16 = 8765;

#[derive(Parser)]
#[command(name = "ca-services")]
#[command(about = "Run and observe local inference services")]
struct Cli {
    #[command(subcommand)]
    command: CommandKind,

    #[arg(long, default_value = "127.0.0.1")]
    host: String,

    #[arg(long, default_value_t = CODEAGENTS_PORT)]
    port: u16,

    /// CodeAgents repository (contains pyproject.toml, .venv, gui/dist).
    #[arg(long, global = true)]
    root: Option<PathBuf>,

    /// Agent workspace (project you edit). Default: ~/.codeagents/launcher.toml or ~/Documents.
    #[arg(long, global = true)]
    workspace: Option<PathBuf>,

    /// Built web UI directory (index.html). Default: <root>/gui/dist when present.
    #[arg(long, global = true)]
    gui_dir: Option<PathBuf>,
}

#[derive(Subcommand)]
enum CommandKind {
    /// Keep services running in a long-lived background process.
    Daemon,
    /// Start Ollama and CodeAgents API in the background.
    Start,
    /// Stop services by their listening ports.
    Stop,
    /// Restart Ollama and CodeAgents API.
    Restart,
    /// Show ports, process ids, and memory usage.
    Status,
    /// Show installed Ollama models and quick-install registry models.
    Models,
    /// Install a registry model with ollama pull.
    Install { key: String },
    /// Send a plain chat request through ca serve.
    Chat {
        prompt: String,
        #[arg(long, default_value = "general")]
        task: String,
    },
    /// Tail service, inference, and runtime request logs.
    Logs {
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// List all chats from the global registry (~/.codeagents/).
    Chats,
}

#[derive(Debug, Deserialize)]
struct Registry {
    models: BTreeMap<String, RegistryModel>,
}

#[derive(Debug, Deserialize)]
struct RegistryModel {
    display_name: String,
    backend: String,
    runtime_model: String,
    profile: String,
    #[serde(default)]
    #[allow(dead_code)]
    weights_path: String,
    #[serde(default)]
    source: String,
    #[serde(default)]
    notes: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct ChatResponse {
    answer: String,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    if let Some(root) = &cli.root {
        env::set_current_dir(root)
            .with_context(|| format!("failed to set project root to {}", root.display()))?;
    }
    let workspace = cli
        .workspace
        .clone()
        .unwrap_or_else(default_workspace_path);
    let gui_dir = cli.gui_dir.as_deref();
    match cli.command {
        CommandKind::Daemon => run_daemon(cli.port, &workspace, gui_dir),
        CommandKind::Start => start_services(cli.port, &workspace, gui_dir),
        CommandKind::Stop => stop_services(cli.port),
        CommandKind::Restart => {
            stop_services(cli.port)?;
            start_services(cli.port, &workspace, gui_dir)
        }
        CommandKind::Status => print_status(cli.port),
        CommandKind::Models => print_models(),
        CommandKind::Install { key } => install_model(&key),
        CommandKind::Chat { prompt, task } => chat(&cli.host, cli.port, &task, &prompt),
        CommandKind::Logs { limit } => print_logs(limit),
        CommandKind::Chats => print_global_chats(),
    }
}

fn default_workspace_path() -> PathBuf {
    let launcher = dirs_home().join(".codeagents").join("launcher.toml");
    if let Ok(raw) = fs::read_to_string(&launcher) {
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some(eq) = line.find('=') {
                let key = line[..eq].trim();
                if key != "workspace" {
                    continue;
                }
                let mut val = line[eq + 1..].trim();
                if val.len() >= 2 {
                    let b = val.as_bytes();
                    if (b[0] == b'"' && b[b.len() - 1] == b'"')
                        || (b[0] == b'\'' && b[b.len() - 1] == b'\'')
                    {
                        val = &val[1..val.len() - 1];
                    }
                }
                let p = PathBuf::from(val);
                if p.is_absolute() {
                    return p;
                }
            }
        }
    }
    dirs_home().join("Documents")
}

fn resolve_gui_dir(explicit: Option<&Path>) -> Option<PathBuf> {
    if let Some(p) = explicit {
        let p = p.to_path_buf();
        if p.join("index.html").is_file() {
            return Some(p);
        }
        return None;
    }
    project_root()
        .ok()
        .map(|r| r.join("gui").join("dist"))
        .filter(|p| p.join("index.html").is_file())
}

fn start_services(port: u16, workspace: &Path, gui_dir: Option<&Path>) -> Result<()> {
    let root = project_root()?;
    let log_dir = root.join(".codeagents").join("services");
    fs::create_dir_all(&log_dir)?;

    if !is_port_busy(OLLAMA_PORT) {
        // Apple-Silicon-optimized environment for Ollama:
        //   OLLAMA_FLASH_ATTENTION=1   — Metal flash attention (≈10–25%
        //                                tokens/sec speedup on most models).
        //   OLLAMA_KV_CACHE_TYPE=q8_0  — half KV-cache memory at negligible
        //                                quality loss; lets us push num_ctx
        //                                higher without spilling to swap.
        //   OLLAMA_KEEP_ALIVE=30m      — keep recently used model warm so
        //                                follow-up requests don't eat the
        //                                multi-second cold-load latency.
        //   OLLAMA_NUM_PARALLEL=1      — one in-flight request per model;
        //                                avoids GPU contention on M-series.
        //   OLLAMA_MAX_LOADED_MODELS=2 — fits comfortably in 36 GB unified.
        let env: &[(&str, &str)] = &[
            ("OLLAMA_FLASH_ATTENTION", "1"),
            ("OLLAMA_KV_CACHE_TYPE", "q8_0"),
            ("OLLAMA_KEEP_ALIVE", "30m"),
            ("OLLAMA_NUM_PARALLEL", "1"),
            ("OLLAMA_MAX_LOADED_MODELS", "2"),
        ];
        spawn_background_env(
            "ollama",
            &["serve"],
            &root,
            &log_dir.join("ollama.log"),
            env,
        )
        .context("failed to start ollama serve")?;
        println!("started ollama serve on :{OLLAMA_PORT} (flash_attention=1, kv_cache=q8_0)");
    } else {
        println!("ollama already listens on :{OLLAMA_PORT}");
    }

    if !is_port_busy(port) {
        let python = root.join(".venv").join("bin").join("python");
        let python_bin = if python.exists() {
            python.to_string_lossy().to_string()
        } else {
            "python3".to_string()
        };
        let gui = resolve_gui_dir(gui_dir);
        let mut argv: Vec<String> = vec![
            "-m".into(),
            "codeagents.cli".into(),
            "serve".into(),
            "--host".into(),
            "127.0.0.1".into(),
            "--port".into(),
            port.to_string(),
            "--workspace".into(),
            workspace.to_string_lossy().into_owned(),
        ];
        if let Some(ref g) = gui {
            argv.push("--gui-dir".into());
            argv.push(g.to_string_lossy().into_owned());
        }
        let argv_ref: Vec<&str> = argv.iter().map(|s| s.as_str()).collect();
        spawn_background(
            &python_bin,
            &argv_ref,
            &root,
            &log_dir.join("ca-serve.log"),
        )
        .context("failed to start ca serve")?;
        println!("started ca serve on :{port} (workspace: {})", workspace.display());
        if let Some(ref g) = gui {
            println!("  web UI: http://127.0.0.1:{port}/ui/  (static dir: {})", g.display());
        }
    } else {
        println!("ca serve already listens on :{port}");
    }
    Ok(())
}

fn stop_services(port: u16) -> Result<()> {
    stop_daemon()?;
    stop_port(port)?;
    stop_port(OLLAMA_PORT)?;
    Ok(())
}

fn run_daemon(port: u16, workspace: &Path, gui_dir: Option<&Path>) -> Result<()> {
    let root = project_root()?;
    let log_dir = root.join(".codeagents").join("services");
    fs::create_dir_all(&log_dir)?;
    let pid_path = daemon_pid_path()?;
    if let Some(pid) = read_pid(&pid_path)? {
        if pid_is_alive(pid) && pid != std::process::id() {
            println!("ca-services daemon already running with pid {pid}");
            return Ok(());
        }
    }
    fs::write(&pid_path, std::process::id().to_string())?;
    println!("ca-services daemon started with pid {}", std::process::id());
    loop {
        if let Err(error) = start_services(port, workspace, gui_dir) {
            append_daemon_log(&format!("daemon start_services error: {error:#}"))?;
        }
        thread::sleep(Duration::from_secs(10));
    }
}

fn stop_daemon() -> Result<()> {
    let pid_path = daemon_pid_path()?;
    let Some(pid) = read_pid(&pid_path)? else {
        return Ok(());
    };
    if pid == std::process::id() {
        return Ok(());
    }
    if pid_is_alive(pid) {
        Command::new("kill")
            .arg("-TERM")
            .arg(pid.to_string())
            .status()
            .ok();
        println!("sent SIGTERM to ca-services daemon pid {pid}");
    }
    fs::remove_file(pid_path).ok();
    Ok(())
}

fn stop_port(port: u16) -> Result<()> {
    let pids = pids_for_port(port)?;
    if pids.is_empty() {
        println!("nothing listens on :{port}");
        return Ok(());
    }
    for pid in pids {
        Command::new("kill").arg("-TERM").arg(&pid).status().ok();
        println!("sent SIGTERM to pid {pid} on :{port}");
    }
    Ok(())
}

fn print_status(port: u16) -> Result<()> {
    println!("services:");
    print_port_status("ollama", OLLAMA_PORT)?;
    print_port_status("codeagents-api", port)?;
    println!();
    println!("unified memory by service process RSS:");
    print_memory_for_service("ollama", OLLAMA_PORT)?;
    print_memory_for_service("codeagents-api", port)?;
    print_vm_stat()?;
    Ok(())
}

fn print_models() -> Result<()> {
    let models_dir = ollama_models_dir();
    println!("ollama models dir: {}", models_dir.display());
    println!(
        "ollama models total disk: {}",
        human_bytes(dir_size(&models_dir).unwrap_or(0))
    );
    println!();
    println!("installed:");
    let output = Command::new("ollama").arg("list").output();
    match output {
        Ok(output) if output.status.success() => {
            io::stdout().write_all(&output.stdout)?;
        }
        Ok(output) => {
            io::stderr().write_all(&output.stderr)?;
        }
        Err(error) => {
            println!("ollama list failed: {error}");
        }
    }
    println!();
    println!("quick install:");
    for (key, model) in load_registry()?.models {
        println!(
            "- {key}: {} | {} | {} | profile={} | {}",
            model.display_name, model.backend, model.runtime_model, model.profile, model.notes
        );
    }
    Ok(())
}

fn install_model(key: &str) -> Result<()> {
    let registry = load_registry()?;
    let model = registry
        .models
        .get(key)
        .ok_or_else(|| anyhow!("unknown model key: {key}"))?;
    if model.backend != "ollama" && !model.source.starts_with("ollama:") {
        return Err(anyhow!(
            "only ollama registry models can be installed by this command"
        ));
    }
    println!("installing {} with ollama pull...", model.runtime_model);
    let status = Command::new("ollama")
        .arg("pull")
        .arg(&model.runtime_model)
        .status()
        .context("failed to run ollama pull")?;
    if !status.success() {
        return Err(anyhow!("ollama pull failed"));
    }
    Ok(())
}

fn chat(host: &str, port: u16, task: &str, prompt: &str) -> Result<()> {
    let client = Client::builder().no_proxy().build()?;
    let response = client
        .post(format!("http://{host}:{port}/chat"))
        .json(&json!({ "task": task, "prompt": prompt }))
        .send()
        .context("failed to call ca serve")?;
    let status = response.status();
    let text = response.text()?;
    if !status.is_success() {
        return Err(anyhow!("ca serve returned {status}: {text}"));
    }
    let parsed: ChatResponse = serde_json::from_str(&text)?;
    println!("{}", parsed.answer);
    Ok(())
}

fn print_logs(limit: usize) -> Result<()> {
    let root = project_root()?;
    for name in [
        "service_requests.jsonl",
        "inference.jsonl",
        "runtime_requests.jsonl",
        "services/ollama.log",
        "services/ca-serve.log",
    ] {
        let path = root.join(".codeagents").join(name);
        println!("== {} ==", path.display());
        for line in tail_lines(&path, limit)? {
            println!("{line}");
        }
        println!();
    }
    // Also show Ollama's native server log (macOS default location)
    let ollama_log = dirs_home().join(".ollama").join("logs").join("server.log");
    if ollama_log.exists() {
        println!("== {} ==", ollama_log.display());
        for line in tail_lines(&ollama_log, limit)? {
            println!("{line}");
        }
        println!();
    }
    Ok(())
}

fn print_global_chats() -> Result<()> {
    let registry_path = dirs_home().join(".codeagents").join("chat_registry.jsonl");
    if !registry_path.exists() {
        println!(
            "No global chat registry found at {}",
            registry_path.display()
        );
        println!("Chats will appear here once you create them with `ca`.");
        return Ok(());
    }
    let raw = fs::read_to_string(&registry_path)?;

    // Build latest state per chat_id
    let mut latest: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
            if let Some(id) = entry.get("chat_id").and_then(|v| v.as_str()) {
                latest.insert(id.to_string(), entry);
            }
        }
    }

    if latest.is_empty() {
        println!("No chats registered yet.");
        return Ok(());
    }

    // Sort by timestamp descending
    let mut entries: Vec<_> = latest.values().collect();
    entries.sort_by(|a, b| {
        let ta = a.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        let tb = b.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        tb.cmp(ta)
    });

    println!("GLOBAL CHATS (all workspaces)");
    println!("───────────────────────────────────────");
    for entry in &entries {
        let id = entry.get("chat_id").and_then(|v| v.as_str()).unwrap_or("?");
        let title = entry
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("Untitled");
        let workspace = entry
            .get("workspace")
            .and_then(|v| v.as_str())
            .unwrap_or("?");
        let msgs = entry
            .get("message_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let ts = entry
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let short_ts = if ts.len() > 19 { &ts[..19] } else { ts };
        println!("  {id}  \"{title}\"  msgs={msgs}  {short_ts}");
        println!("    workspace: {workspace}");
    }
    println!("\nTotal: {} chat(s)", entries.len());
    Ok(())
}

fn spawn_background(command: &str, args: &[&str], cwd: &Path, log_path: &Path) -> Result<()> {
    spawn_background_env(command, args, cwd, log_path, &[])
}

fn spawn_background_env(
    command: &str,
    args: &[&str],
    cwd: &Path,
    log_path: &Path,
    env: &[(&str, &str)],
) -> Result<()> {
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;
    let stderr = File::options().create(true).append(true).open(log_path)?;
    let mut cmd = Command::new(command);
    cmd.args(args)
        .current_dir(cwd)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    for (k, v) in env {
        cmd.env(k, v);
    }
    cmd.spawn()?;
    Ok(())
}

fn print_port_status(name: &str, port: u16) -> Result<()> {
    let pids = pids_for_port(port)?;
    if pids.is_empty() {
        println!("- {name}: stopped (: {port})");
    } else {
        println!("- {name}: running on :{port}, pid(s): {}", pids.join(", "));
    }
    Ok(())
}

fn print_memory_for_service(name: &str, port: u16) -> Result<()> {
    let pids = pids_for_port(port)?;
    if pids.is_empty() {
        println!("- {name}: not running");
        return Ok(());
    }
    for pid in pids {
        let ps = Command::new("ps")
            .args(["-o", "pid=,rss=,command=", "-p", &pid])
            .output()?;
        let line = String::from_utf8_lossy(&ps.stdout);
        if let Some((pid, rss_kb, command)) = parse_ps_line(&line) {
            println!(
                "- {name}: pid={pid} rss={} cmd={command}",
                human_bytes(rss_kb * 1024)
            );
        }
    }
    Ok(())
}

fn print_vm_stat() -> Result<()> {
    let output = Command::new("vm_stat").output();
    let Ok(output) = output else {
        return Ok(());
    };
    if output.status.success() {
        let text = String::from_utf8_lossy(&output.stdout);
        if let Some(free) = text.lines().find(|line| line.starts_with("Pages free:")) {
            println!("- system: {}", free.trim());
        }
        if let Some(inactive) = text
            .lines()
            .find(|line| line.starts_with("Pages inactive:"))
        {
            println!("- system: {}", inactive.trim());
        }
    }
    Ok(())
}

fn parse_ps_line(line: &str) -> Option<(String, u64, String)> {
    let trimmed = line.trim();
    let mut parts = trimmed.split_whitespace();
    let pid = parts.next()?.to_string();
    let rss_kb = parts.next()?.parse().ok()?;
    let command = parts.collect::<Vec<_>>().join(" ");
    Some((pid, rss_kb, command))
}

fn pids_for_port(port: u16) -> Result<Vec<String>> {
    let output = Command::new("lsof")
        .args(["-nP", &format!("-tiTCP:{port}"), "-sTCP:LISTEN"])
        .output();
    let Ok(output) = output else {
        return Ok(Vec::new());
    };
    if !output.status.success() {
        return Ok(Vec::new());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(ToOwned::to_owned)
        .collect())
}

fn is_port_busy(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

fn load_registry() -> Result<Registry> {
    let path = project_root()?.join("config").join("model_registry.toml");
    let raw =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    Ok(toml::from_str(&raw)?)
}

fn project_root() -> Result<PathBuf> {
    let cwd = env::current_dir()?;
    if cwd.join("pyproject.toml").exists() && cwd.join("Cargo.toml").exists() {
        return Ok(cwd);
    }
    for parent in cwd.ancestors() {
        if parent.join("pyproject.toml").exists() && parent.join("Cargo.toml").exists() {
            return Ok(parent.to_path_buf());
        }
    }
    Err(anyhow!("run ca-services from the CodeAgents repository"))
}

fn daemon_pid_path() -> Result<PathBuf> {
    Ok(project_root()?
        .join(".codeagents")
        .join("services")
        .join("ca-services-daemon.pid"))
}

fn read_pid(path: &Path) -> Result<Option<u32>> {
    if !path.exists() {
        return Ok(None);
    }
    let raw = fs::read_to_string(path)?;
    Ok(raw.trim().parse().ok())
}

fn pid_is_alive(pid: u32) -> bool {
    Command::new("kill")
        .arg("-0")
        .arg(pid.to_string())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn append_daemon_log(message: &str) -> Result<()> {
    let path = project_root()?
        .join(".codeagents")
        .join("services")
        .join("daemon.log");
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{message}")?;
    Ok(())
}

fn ollama_models_dir() -> PathBuf {
    if let Ok(path) = env::var("OLLAMA_MODELS") {
        return PathBuf::from(path);
    }
    dirs_home().join(".ollama").join("models")
}

fn dirs_home() -> PathBuf {
    env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn dir_size(path: &Path) -> Result<u64> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0;
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let metadata = entry.metadata()?;
        if metadata.is_dir() {
            total += dir_size(&entry.path())?;
        } else {
            total += metadata.len();
        }
    }
    Ok(total)
}

fn human_bytes(bytes: u64) -> String {
    let units = ["B", "KB", "MB", "GB", "TB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < units.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", units[unit])
}

fn tail_lines(path: &Path, limit: usize) -> Result<Vec<String>> {
    if !path.exists() {
        return Ok(vec!["<missing>".to_string()]);
    }
    let raw = fs::read_to_string(path)?;
    let lines: Vec<_> = raw.lines().map(ToOwned::to_owned).collect();
    let start = lines.len().saturating_sub(limit);
    Ok(lines[start..].to_vec())
}
