use crate::api::{ApiClient, ChatMessage, ContentBlock, StreamEvent, ToolInfo};
use anyhow::{Context, Result};
use crossterm::{
    event::{
        self, Event, KeyCode, KeyEvent, KeyModifiers, KeyboardEnhancementFlags,
        PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
    },
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Terminal,
    backend::CrosstermBackend,
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
};
use serde_json::{Value, json};
use std::{
    io::{self, Stdout},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver},
    },
    thread,
    time::Duration,
};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

// ─── Input mode (vim-style) ─────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
enum Mode {
    Insert,
    Normal,
}

impl Mode {
    fn label(self) -> &'static str {
        match self {
            Self::Insert => "INSERT",
            Self::Normal => "NORMAL",
        }
    }

    fn color(self) -> Color {
        match self {
            Self::Insert => Color::Green,
            Self::Normal => Color::Blue,
        }
    }
}

// ─── Role formatting ────────────────────────────────────────────────

const LABEL_WIDTH: usize = 8; // "you", "thinking", "tool", "sys", "err"

#[derive(Debug, Clone, Copy, PartialEq)]
enum Role {
    User,
    Assistant,
    System,
    Tool,
    FunctionResult,
    Thinking,
    Error,
}

impl Role {
    fn from_str(s: &str) -> Self {
        match s {
            "user" => Self::User,
            "assistant" => Self::Assistant,
            "tool" => Self::Tool,
            "function" | "function_result" => Self::FunctionResult,
            "thinking" => Self::Thinking,
            "error" => Self::Error,
            _ => Self::System,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::User => "you",
            Self::Assistant => "",
            Self::System => "sys",
            Self::Tool => "tool",
            Self::FunctionResult => "result",
            Self::Thinking => "thinking",
            Self::Error => "err",
        }
    }

    fn style(self) -> Style {
        match self {
            Self::User => Style::default()
                .fg(Color::Green)
                .add_modifier(Modifier::BOLD),
            Self::Assistant => Style::default().fg(Color::Cyan),
            Self::System => Style::default().fg(Color::DarkGray),
            Self::Tool => Style::default().fg(Color::Yellow),
            Self::FunctionResult => Style::default().fg(Color::LightBlue),
            Self::Thinking => Style::default().fg(Color::Magenta),
            Self::Error => Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
        }
    }

    fn text_style(self) -> Style {
        match self {
            Self::System => Style::default().fg(Color::DarkGray),
            Self::Error => Style::default().fg(Color::Red),
            Self::Thinking => Style::default()
                .fg(Color::Magenta)
                .add_modifier(Modifier::DIM),
            _ => Style::default(),
        }
    }
}

// ─── Model profiles ─────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct ModelProfile {
    task: String,
    runtime: String,
    display: String,
    note: String,
}

fn default_profiles() -> Vec<ModelProfile> {
    vec![
        ModelProfile {
            task: "code".into(),
            runtime: String::new(),
            display: "Code (default)".into(),
            note: String::new(),
        },
        ModelProfile {
            task: "general".into(),
            runtime: String::new(),
            display: "General".into(),
            note: String::new(),
        },
        ModelProfile {
            task: "reasoning".into(),
            runtime: String::new(),
            display: "Reasoning".into(),
            note: String::new(),
        },
    ]
}

fn load_profiles(api: &ApiClient) -> Vec<ModelProfile> {
    if let Ok(resp) = api.inference_models() {
        let mut seen = std::collections::HashSet::new();
        let mut profiles: Vec<ModelProfile> = Vec::new();

        for m in &resp.models {
            let task = m.profile.as_str();
            if task.is_empty() {
                continue;
            }
            if !seen.insert(task.to_string()) {
                continue;
            }
            let display = m.display_name.as_str();
            let display = if display.is_empty() { task } else { display };
            let runtime = m.runtime_model.as_str();
            let notes = m.notes.as_str();

            let label = if !runtime.is_empty() && runtime != display {
                format!("{display} ({runtime})")
            } else {
                display.to_string()
            };

            profiles.push(ModelProfile {
                task: task.into(),
                runtime: runtime.into(),
                display: label,
                note: notes.into(),
            });
        }
        if !profiles.is_empty() {
            return profiles;
        }
    }
    default_profiles()
}

// ─── Chat line ──────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct ChatLine {
    role: Role,
    text: String,
    chat_visible: bool,
    collapsed: bool,
}

struct PendingResponse {
    receiver: Receiver<StreamEvent>,
    /// Currently open assistant-text block (for appending Delta chunks).
    /// Reset to None whenever the role flips to thinking / tool / notice so
    /// the next Delta starts a fresh block right after.
    assistant_index: Option<usize>,
    /// Currently open thinking block. Same flip semantics as above.
    thinking_index: Option<usize>,
    /// Map: tool call index (from API) → transcript line index for live updates.
    tool_lines: std::collections::HashMap<u32, usize>,
    /// Accumulated args per tool index (raw JSON-as-typed-by-the-model).
    tool_args: std::collections::HashMap<u32, String>,
    /// Tool name per tool index.
    tool_names: std::collections::HashMap<u32, String>,
    cancelled: Arc<AtomicBool>,
    /// When the stream started (for elapsed-time heartbeat in status bar).
    started_at: std::time::Instant,
    /// Last time we observed any event (for "still working" indicator).
    last_event_at: std::time::Instant,
    /// Total characters/tokens received in this turn (for status bar).
    total_chars: usize,
}

// ─── App ────────────────────────────────────────────────────────────

pub struct TuiApp {
    api: ApiClient,
    mode: Mode,
    input: String,
    cursor: usize,
    transcript: Vec<ChatLine>,
    status: String,
    models: Vec<ModelProfile>,
    model_idx: usize,
    cwd: String,
    chat_id: Option<String>,
    chat_title: String,
    pending_tool: Option<(String, Value)>,
    /// Stream-driven approval: (decision_id, name, arguments_raw, remember_supported, warning)
    pending_confirm: Option<(String, String, String, bool, String)>,
    pending_response: Option<PendingResponse>,
    scroll_offset: u16,
    total_visual: usize,
    show_overview: bool,
    thinking_collapsed_global: bool,
    cached_tools: Vec<ToolInfo>,
}

impl TuiApp {
    pub fn new(api: ApiClient, task: String) -> Result<Self> {
        let cwd = std::env::current_dir()
            .context("failed to read current directory")?
            .display()
            .to_string();
        let models = load_profiles(&api);
        let model_idx = models.iter().position(|m| m.task == task).unwrap_or(0);

        let cached_tools = api.tools().map(|t| t.tools).unwrap_or_default();
        let app = Self {
            api,
            mode: Mode::Insert,
            input: String::new(),
            cursor: 0,
            transcript: Vec::new(),
            status: "ready".into(),
            models,
            model_idx,
            cwd,
            chat_id: None,
            chat_title: "unsaved".into(),
            pending_tool: None,
            pending_confirm: None,
            pending_response: None,
            scroll_offset: u16::MAX,
            total_visual: 0,
            show_overview: false,
            thinking_collapsed_global: false,
            cached_tools,
        };
        Ok(app)
    }

    pub fn set_chat(&mut self, chat_id: String, title: String) {
        self.chat_id = Some(chat_id);
        self.chat_title = title.clone();
        self.push_system(&format!(
            "Chat \"{title}\" created. Esc → normal, i → insert. /help for more."
        ));
    }

    pub fn open_chat_by_id(&mut self, chat_id: &str) -> Result<()> {
        self.open_chat(chat_id)?;
        self.push_system(
            "Esc → normal mode (s=stop, t=thinking, j/k=scroll), i → insert mode. /help for more.",
        );
        Ok(())
    }

    fn switch_to_model_by_runtime(&mut self, runtime_name: &str) {
        if let Some(idx) = self.find_model_index(runtime_name) {
            self.model_idx = idx;
        }
    }

    fn find_model_index(&self, query: &str) -> Option<usize> {
        let query = query.trim();
        if query.is_empty() {
            return None;
        }
        let query_lower = query.to_lowercase();
        self.models.iter().position(|m| {
            m.task == query
                || m.runtime == query
                || m.display == query
                || m.task.to_lowercase() == query_lower
                || m.runtime.to_lowercase() == query_lower
                || m.display.to_lowercase() == query_lower
                || m.display.to_lowercase().contains(&query_lower)
        })
    }

    fn cycle_model(&mut self, delta: isize) {
        if self.models.is_empty() {
            return;
        }
        let len = self.models.len() as isize;
        let next = (self.model_idx as isize + delta).rem_euclid(len) as usize;
        self.model_idx = next;
        self.status = format!("model: {}", self.current_display());
        self.push_system(&format!("Switched to {}", self.current_display()));
    }

    fn current_task(&self) -> &str {
        self.models
            .get(self.model_idx)
            .map(|m| m.task.as_str())
            .unwrap_or("code")
    }

    fn current_display(&self) -> &str {
        self.models
            .get(self.model_idx)
            .map(|m| m.display.as_str())
            .unwrap_or("Unknown")
    }

    pub fn run(&mut self) -> Result<()> {
        let mut terminal = TerminalSession::start()?;
        loop {
            self.poll_pending_response();
            let draw_fn = |frame: &mut ratatui::Frame| self.draw(frame);
            terminal.draw(draw_fn)?;
            // While streaming, tick at ~33Hz so the user sees per-chunk animation
            // instead of the whole tool call appearing at once. Idle stays at 10Hz.
            let tick_ms = if self.pending_response.is_some() {
                30
            } else {
                100
            };
            if !event::poll(Duration::from_millis(tick_ms))? {
                continue;
            }
            match event::read()? {
                Event::Key(key) => {
                    if self.handle_key(key)? {
                        break;
                    }
                }
                Event::Mouse(mouse) => {
                    use crossterm::event::MouseEventKind;
                    match mouse.kind {
                        MouseEventKind::ScrollUp => self.scroll_up(3),
                        MouseEventKind::ScrollDown => self.scroll_down(3),
                        _ => {}
                    }
                }
                Event::Paste(text) => self.insert_pasted_text(&text),
                _ => {}
            }
        }
        Ok(())
    }

    // ─── Drawing ────────────────────────────────────────────────────

    fn draw(&mut self, frame: &mut ratatui::Frame) {
        let area = frame.area();
        let input_height = self.input_height(area.width);
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(2),
                Constraint::Min(5),
                Constraint::Length(input_height),
                Constraint::Length(1),
            ])
            .split(area);

        self.draw_header(frame, chunks[0]);
        self.draw_chat(frame, chunks[1]);
        self.draw_input(frame, chunks[2]);
        self.draw_footer(frame, chunks[3]);

        if self.show_overview {
            self.draw_overview(frame, area);
        }
    }

    fn draw_header(&self, frame: &mut ratatui::Frame, area: Rect) {
        let width = area.width as usize;
        let model_str = format!("model: {}", self.current_display());
        let cwd_short = shorten_path(&self.cwd, width.saturating_sub(model_str.len() + 25));

        let mode_label = format!(" {} ", self.mode.label());
        let line1 = Line::from(vec![
            Span::styled(
                &mode_label,
                Style::default()
                    .fg(Color::Black)
                    .bg(self.mode.color())
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                " ca",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(" ── ", Style::default().fg(Color::DarkGray)),
            Span::styled(&model_str, Style::default().fg(Color::Green)),
            Span::styled(" ── ", Style::default().fg(Color::DarkGray)),
            Span::raw(cwd_short),
        ]);

        let line2 = match self.mode {
            Mode::Normal => Line::from(vec![
                Span::styled("     ", Style::default()),
                Span::styled(
                    "i",
                    Style::default()
                        .fg(Color::Green)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" insert  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "s",
                    Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
                ),
                Span::styled(" stop  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "t/T",
                    Style::default()
                        .fg(Color::Magenta)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" thinking  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "j/k",
                    Style::default()
                        .fg(Color::Cyan)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" scroll  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "?",
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" overview  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "q",
                    Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
                ),
                Span::styled(" quit", Style::default().fg(Color::DarkGray)),
            ]),
            Mode::Insert => Line::from(vec![
                Span::styled("     ", Style::default()),
                Span::styled(
                    "Esc",
                    Style::default()
                        .fg(Color::Blue)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" normal  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "Enter",
                    Style::default()
                        .fg(Color::Green)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" send  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "Alt+Enter",
                    Style::default()
                        .fg(Color::DarkGray)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" newline  ", Style::default().fg(Color::DarkGray)),
                Span::styled(
                    "Tab",
                    Style::default()
                        .fg(Color::DarkGray)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(" model", Style::default().fg(Color::DarkGray)),
            ]),
        };

        let header = Paragraph::new(vec![line1, line2]);
        frame.render_widget(header, area);
    }

    fn draw_chat(&mut self, frame: &mut ratatui::Frame, area: Rect) {
        let chat_height = area.height as usize;
        let chat_width = area.width as usize;
        let text_width = chat_width.saturating_sub(LABEL_WIDTH + 3).max(10);

        let lines = self.build_visual_lines(text_width);
        self.total_visual = lines.len();

        let max_scroll = lines.len().saturating_sub(chat_height);
        let offset: u16 = if self.scroll_offset == u16::MAX {
            max_scroll as u16
        } else {
            (self.scroll_offset as usize).min(max_scroll) as u16
        };

        let chat = Paragraph::new(lines)
            .block(
                Block::default()
                    .borders(Borders::TOP)
                    .border_style(Style::default().fg(Color::DarkGray)),
            )
            .scroll((offset, 0));
        frame.render_widget(chat, area);
    }

    fn input_inner_width(&self, total_width: u16) -> u16 {
        total_width.saturating_sub(2).max(1)
    }

    fn input_height(&self, total_width: u16) -> u16 {
        const MIN_LINES: u16 = 1;
        const MAX_LINES: u16 = 10;
        let inner = self.input_inner_width(total_width) as usize;
        let lines = wrap_input_lines(&self.input, inner);
        let needed = lines.len().max(MIN_LINES as usize) as u16;
        needed.min(MAX_LINES) + 2 // +2 for borders
    }

    fn draw_input(&self, frame: &mut ratatui::Frame, area: Rect) {
        let border_color = if self.pending_tool.is_some() || self.pending_confirm.is_some() {
            Color::Magenta
        } else if self.pending_response.is_some() {
            Color::Yellow
        } else {
            match self.mode {
                Mode::Insert => Color::Green,
                Mode::Normal => Color::Blue,
            }
        };

        let title = if self.pending_tool.is_some() {
            " approve? Enter=yes Esc=no ".to_string()
        } else if let Some((_, name, _, remember_supported, _)) = &self.pending_confirm {
            if *remember_supported {
                format!(" approve {name}? Enter/y=once  a=always here  Esc/n=no ")
            } else {
                format!(" approve {name}? Enter/y=once  Esc/n=no ")
            }
        } else if self.pending_response.is_some() {
            match self.mode {
                Mode::Normal => " streaming… s=stop q=quit ".to_string(),
                Mode::Insert => " streaming… Esc→normal, Ctrl+S=stop ".to_string(),
            }
        } else {
            match self.mode {
                Mode::Normal => " NORMAL — press i to type ".to_string(),
                Mode::Insert => String::new(),
            }
        };

        let inner_w = self.input_inner_width(area.width) as usize;
        let inner_h = area.height.saturating_sub(2) as usize;

        let display_text: String = if self.mode == Mode::Normal && self.input.is_empty() {
            String::new()
        } else {
            self.input.clone()
        };

        let lines = wrap_input_lines(&display_text, inner_w);
        let (cursor_row, cursor_col) = cursor_visual_pos(&self.input, self.cursor, inner_w);

        let scroll: u16 = if cursor_row >= inner_h && inner_h > 0 {
            (cursor_row - inner_h + 1) as u16
        } else {
            0
        };

        let body: String = lines.join("\n");

        let input = Paragraph::new(body)
            .block(
                Block::default()
                    .title(title)
                    .title_style(
                        Style::default()
                            .fg(border_color)
                            .add_modifier(Modifier::BOLD),
                    )
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(border_color)),
            )
            .scroll((scroll, 0));
        frame.render_widget(input, area);

        if self.mode == Mode::Insert {
            let visible_row = cursor_row.saturating_sub(scroll as usize);
            let cursor_x = area.x + 1 + (cursor_col as u16);
            let cursor_y = area.y + 1 + (visible_row as u16);
            frame.set_cursor_position((cursor_x, cursor_y));
        }
    }

    fn draw_footer(&self, frame: &mut ratatui::Frame, area: Rect) {
        let scroll_hint = if self.scroll_offset != u16::MAX {
            " [scrolled ↑] "
        } else {
            ""
        };

        let status_color = if self.status == "generating" || self.status == "streaming" {
            Color::Yellow
        } else if self.status == "error" {
            Color::Red
        } else {
            Color::Green
        };

        // Heartbeat: while streaming, show elapsed seconds, char counter, and
        // a stalled marker if the runtime hasn't sent anything for a while.
        let heartbeat = self.pending_response.as_ref().map(|p| {
            let now = std::time::Instant::now();
            let elapsed = now.duration_since(p.started_at).as_secs();
            let idle = now.duration_since(p.last_event_at).as_secs();
            let spinner =
                ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"][(elapsed as usize) % 10];
            let cps = if elapsed > 0 {
                p.total_chars as u64 / elapsed
            } else {
                0
            };
            if idle >= 5 {
                format!(
                    " {spinner} {}s ({} chars, idle {}s — model is thinking…) ",
                    elapsed, p.total_chars, idle
                )
            } else {
                format!(
                    " {spinner} {}s ({} chars, ~{}/s) ",
                    elapsed, p.total_chars, cps
                )
            }
        });

        let mut spans = vec![
            Span::styled(" ", Style::default()),
            Span::styled(&self.chat_title, Style::default().fg(Color::Cyan)),
            Span::styled(" │ ", Style::default().fg(Color::DarkGray)),
            Span::styled(&self.status, Style::default().fg(status_color)),
        ];
        if let Some(hb) = heartbeat {
            spans.push(Span::styled(hb, Style::default().fg(Color::Yellow)));
        }
        spans.extend([
            Span::styled(scroll_hint, Style::default().fg(Color::Yellow)),
            Span::styled(" │ ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                format!(" {} ", self.mode.label()),
                Style::default().fg(Color::Black).bg(self.mode.color()),
            ),
        ]);
        frame.render_widget(Paragraph::new(Line::from(spans)), area);
    }

    fn build_visual_lines(&self, text_width: usize) -> Vec<Line<'static>> {
        let mut out: Vec<Line<'static>> = Vec::new();

        let streaming_idx = self
            .pending_response
            .as_ref()
            .and_then(|p| p.assistant_index);

        for (line_idx, item) in self.transcript.iter().enumerate() {
            let role = item.role;

            if role == Role::Thinking && item.collapsed {
                let line_count = item.text.lines().count();
                let summary = format!("[reasoning: {line_count} lines — t to expand]");
                out.push(Line::from(vec![
                    Span::styled(
                        format!("{:>w$}", role.label(), w = LABEL_WIDTH),
                        role.style(),
                    ),
                    Span::styled(" │ ", Style::default().fg(Color::DarkGray)),
                    Span::styled(
                        summary,
                        Style::default()
                            .fg(Color::Magenta)
                            .add_modifier(Modifier::DIM),
                    ),
                ]));
                out.push(Line::from(""));
                continue;
            }

            let use_md =
                role == Role::Assistant && streaming_idx != Some(line_idx) && !item.text.is_empty();

            // Continuation style: thinking gets gray bar, assistant gets space
            let has_label = !role.label().is_empty();
            let continuation_span = if role == Role::Thinking {
                Span::styled(" │ ", Style::default().fg(Color::DarkGray))
            } else if role == Role::Assistant {
                Span::raw("   ")
            } else {
                Span::styled(" │ ", Style::default().fg(Color::DarkGray))
            };

            let first_sep = if has_label {
                Span::styled(" > ", role.style())
            } else {
                Span::raw("   ")
            };

            let mut in_code_block = false;
            let mut first = true;
            let logical_lines: Vec<&str> = item.text.lines().collect();
            let mut logical_idx = 0usize;

            while logical_idx < logical_lines.len() {
                let logical = logical_lines[logical_idx];
                if use_md && logical.trim_start().starts_with("```") {
                    in_code_block = !in_code_block;
                    let code_line = Line::from(vec![
                        if first {
                            Span::styled(
                                format!("{:>w$}", role.label(), w = LABEL_WIDTH),
                                role.style(),
                            )
                        } else {
                            Span::raw(" ".repeat(LABEL_WIDTH))
                        },
                        if first {
                            first_sep.clone()
                        } else {
                            continuation_span.clone()
                        },
                        Span::styled(logical.to_string(), Style::default().fg(Color::DarkGray)),
                    ]);
                    out.push(code_line);
                    first = false;
                    logical_idx += 1;
                    continue;
                }

                if use_md && !in_code_block {
                    if let Some((table_lines, consumed)) =
                        render_markdown_table(&logical_lines, logical_idx, text_width)
                    {
                        for content_spans in table_lines {
                            let mut line_spans = vec![
                                if first {
                                    Span::styled(
                                        format!("{:>w$}", role.label(), w = LABEL_WIDTH),
                                        role.style(),
                                    )
                                } else {
                                    Span::raw(" ".repeat(LABEL_WIDTH))
                                },
                                if first {
                                    first_sep.clone()
                                } else {
                                    continuation_span.clone()
                                },
                            ];
                            line_spans.extend(content_spans);
                            out.push(Line::from(line_spans));
                            first = false;
                        }
                        logical_idx += consumed;
                        continue;
                    }
                }

                let segments = word_wrap(logical, text_width);
                for seg in segments {
                    let content_spans: Vec<Span<'static>> = if use_md && !in_code_block {
                        render_md_line(&seg, role.text_style())
                    } else if use_md && in_code_block {
                        vec![Span::styled(seg, Style::default().fg(Color::Yellow))]
                    } else {
                        vec![Span::styled(seg, role.text_style())]
                    };

                    let mut line_spans = vec![
                        if first {
                            Span::styled(
                                format!("{:>w$}", role.label(), w = LABEL_WIDTH),
                                role.style(),
                            )
                        } else {
                            Span::raw(" ".repeat(LABEL_WIDTH))
                        },
                        if first {
                            first_sep.clone()
                        } else {
                            continuation_span.clone()
                        },
                    ];
                    line_spans.extend(content_spans);
                    out.push(Line::from(line_spans));
                    first = false;
                }
                logical_idx += 1;
            }
            out.push(Line::from(""));
        }
        out
    }

    // ─── Keyboard ───────────────────────────────────────────────────

    fn stop_generation(&mut self) {
        if let Some(pending) = self.pending_response.take() {
            pending.cancelled.store(true, Ordering::Relaxed);
            while pending.receiver.try_recv().is_ok() {}
            // Append [stopped] to whichever block is currently open. If none
            // is open (e.g. stream cut between blocks), push a fresh marker.
            if let Some(idx) = pending.assistant_index {
                if let Some(line) = self.transcript.get_mut(idx) {
                    if line.text.is_empty() {
                        line.text = "[stopped]".into();
                    } else {
                        line.text.push_str("\n\n[stopped]");
                    }
                }
            } else {
                self.transcript.push(ChatLine {
                    role: Role::Assistant,
                    text: "[stopped]".into(),
                    chat_visible: true,
                    collapsed: false,
                });
            }
            self.status = "ready".into();
            self.scroll_offset = u16::MAX;
            self.push_system("Generation stopped.");
        }
    }

    fn handle_key(&mut self, key: KeyEvent) -> Result<bool> {
        // Ctrl+C / Ctrl+D: stop generation if streaming, otherwise quit
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('d'))
        {
            if self.pending_response.is_some() {
                self.stop_generation();
                return Ok(false);
            }
            return Ok(true);
        }

        // Overview panel intercepts all keys when open
        if self.show_overview {
            match key.code {
                KeyCode::Esc | KeyCode::Char('?') | KeyCode::Char('q') | KeyCode::F(1) => {
                    self.show_overview = false;
                }
                _ => {}
            }
            return Ok(false);
        }

        // Tool approval intercepts (CLI /tool flow)
        if self.pending_tool.is_some() {
            return self.handle_approval_key(key);
        }
        // Stream-driven confirmation (model wants to run a dangerous tool)
        if self.pending_confirm.is_some() {
            return self.handle_confirm_key(key);
        }

        // PageUp/Down work in both modes
        match key.code {
            KeyCode::PageUp => {
                self.scroll_up(10);
                return Ok(false);
            }
            KeyCode::PageDown => {
                self.scroll_down(10);
                return Ok(false);
            }
            _ => {}
        }

        match self.mode {
            Mode::Normal => self.handle_key_normal(key),
            Mode::Insert => self.handle_key_insert(key),
        }
    }

    /// Normal mode: single-key commands, no text input
    fn handle_key_normal(&mut self, key: KeyEvent) -> Result<bool> {
        match key.code {
            // Back to insert mode
            KeyCode::Char('i') | KeyCode::Char('a')
                if !key.modifiers.contains(KeyModifiers::CONTROL) =>
            {
                self.mode = Mode::Insert;
                if key.code == KeyCode::Char('a') {
                    self.cursor = self.input.chars().count();
                }
                Ok(false)
            }
            // Also Enter goes to insert mode (intuitive)
            KeyCode::Enter => {
                self.mode = Mode::Insert;
                Ok(false)
            }

            // Quit
            KeyCode::Char('q') => {
                if self.pending_response.is_some() {
                    self.stop_generation();
                    Ok(false)
                } else {
                    Ok(true)
                }
            }

            // Stop generation
            KeyCode::Char('s') => {
                if self.pending_response.is_some() {
                    self.stop_generation();
                }
                Ok(false)
            }

            // Thinking toggle
            KeyCode::Char('t') => {
                self.toggle_last_thinking();
                Ok(false)
            }
            KeyCode::Char('T') => {
                self.toggle_all_thinking();
                Ok(false)
            }

            // Scrolling (vim-style)
            KeyCode::Char('j') => {
                self.scroll_down(3);
                Ok(false)
            }
            KeyCode::Char('k') => {
                self.scroll_up(3);
                Ok(false)
            }
            KeyCode::Char('d') => {
                self.scroll_down(15);
                Ok(false)
            }
            KeyCode::Char('u') => {
                self.scroll_up(15);
                Ok(false)
            }
            KeyCode::Char('G') => {
                self.scroll_offset = u16::MAX;
                Ok(false)
            }
            KeyCode::Char('g') => {
                self.scroll_offset = 0;
                Ok(false)
            }

            // Overview
            KeyCode::Char('?') | KeyCode::F(1) => {
                self.show_overview = !self.show_overview;
                Ok(false)
            }

            // Model switching
            KeyCode::Tab => {
                self.cycle_model(1);
                Ok(false)
            }
            KeyCode::BackTab => {
                self.cycle_model(-1);
                Ok(false)
            }

            // '/' enters insert mode with '/' pre-filled for commands
            KeyCode::Char('/') => {
                self.mode = Mode::Insert;
                self.input.clear();
                self.input.push('/');
                self.cursor = 1;
                Ok(false)
            }

            KeyCode::Esc => Ok(false), // already in Normal
            _ => Ok(false),
        }
    }

    /// Insert mode: text editing, Esc switches to Normal
    fn handle_key_insert(&mut self, key: KeyEvent) -> Result<bool> {
        match key.code {
            KeyCode::Esc => {
                self.mode = Mode::Normal;
                Ok(false)
            }
            KeyCode::F(1) => {
                self.show_overview = !self.show_overview;
                Ok(false)
            }
            KeyCode::Tab => {
                self.cycle_model(1);
                Ok(false)
            }
            KeyCode::BackTab => {
                self.cycle_model(-1);
                Ok(false)
            }
            KeyCode::Enter => {
                if key.modifiers.contains(KeyModifiers::SHIFT)
                    || key.modifiers.contains(KeyModifiers::ALT)
                {
                    self.input_insert('\n');
                    return Ok(false);
                }
                let text = self.input.trim().to_string();
                self.input.clear();
                self.cursor = 0;
                if text.is_empty() {
                    return Ok(false);
                }
                self.scroll_offset = u16::MAX;
                if text.starts_with('/') {
                    self.handle_command(&text)
                } else {
                    self.send_prompt(&text)?;
                    Ok(false)
                }
            }
            KeyCode::Backspace => {
                if key.modifiers.contains(KeyModifiers::ALT) {
                    self.delete_word_back();
                } else if self.cursor > 0 {
                    let byte_pos = char_to_byte(&self.input, self.cursor - 1);
                    let byte_end = char_to_byte(&self.input, self.cursor);
                    self.input.replace_range(byte_pos..byte_end, "");
                    self.cursor -= 1;
                }
                Ok(false)
            }
            KeyCode::Delete => {
                let len = self.input.chars().count();
                if self.cursor < len {
                    let byte_pos = char_to_byte(&self.input, self.cursor);
                    let byte_end = char_to_byte(&self.input, self.cursor + 1);
                    self.input.replace_range(byte_pos..byte_end, "");
                }
                Ok(false)
            }
            KeyCode::Left => {
                if self.cursor > 0 {
                    self.cursor -= 1;
                }
                Ok(false)
            }
            KeyCode::Right => {
                let len = self.input.chars().count();
                if self.cursor < len {
                    self.cursor += 1;
                }
                Ok(false)
            }
            KeyCode::Home => {
                self.cursor = 0;
                Ok(false)
            }
            KeyCode::End => {
                self.cursor = self.input.chars().count();
                Ok(false)
            }
            KeyCode::Char('j') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.input_insert('\n');
                Ok(false)
            }
            KeyCode::Char('a') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.cursor = 0;
                Ok(false)
            }
            KeyCode::Char('e') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.cursor = self.input.chars().count();
                Ok(false)
            }
            KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                let byte_pos = char_to_byte(&self.input, self.cursor);
                self.input.drain(..byte_pos);
                self.cursor = 0;
                Ok(false)
            }
            KeyCode::Char('s') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                if self.pending_response.is_some() {
                    self.stop_generation();
                }
                Ok(false)
            }
            KeyCode::Char(ch) => {
                // Drop Alt+<char> (macOS sends special symbols like ≈, ∂, etc.).
                if key.modifiers.contains(KeyModifiers::ALT) {
                    return Ok(false);
                }
                self.input_insert(ch);
                Ok(false)
            }
            _ => Ok(false),
        }
    }

    fn input_insert(&mut self, ch: char) {
        let byte_pos = char_to_byte(&self.input, self.cursor);
        self.input.insert(byte_pos, ch);
        self.cursor += 1;
    }

    /// Insert pasted text at the cursor verbatim, preserving newlines.
    /// Bypasses the per-key handler so an embedded \n cannot trigger send.
    fn insert_pasted_text(&mut self, text: &str) {
        // Only meaningful in INSERT mode; in NORMAL mode treat as no-op so
        // accidental paste over read-only views doesn't surprise the user.
        if !matches!(self.mode, Mode::Insert) {
            return;
        }
        // Normalize CRLF/CR -> LF so embedded line breaks become real newlines.
        let normalized: String = text.replace("\r\n", "\n").replace('\r', "\n");
        if normalized.is_empty() {
            return;
        }
        let byte_pos = char_to_byte(&self.input, self.cursor);
        self.input.insert_str(byte_pos, &normalized);
        self.cursor += normalized.chars().count();
    }

    fn delete_word_back(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let chars: Vec<char> = self.input.chars().collect();
        let mut i = self.cursor;
        // skip trailing whitespace
        while i > 0 && chars[i - 1].is_whitespace() {
            i -= 1;
        }
        // delete word characters
        while i > 0 && !chars[i - 1].is_whitespace() {
            i -= 1;
        }
        let start_byte = char_to_byte(&self.input, i);
        let end_byte = char_to_byte(&self.input, self.cursor);
        self.input.replace_range(start_byte..end_byte, "");
        self.cursor = i;
    }

    fn scroll_up(&mut self, n: u16) {
        if self.scroll_offset == u16::MAX {
            let bottom = self.total_visual.saturating_sub(1) as u16;
            self.scroll_offset = bottom.saturating_sub(n);
        } else {
            self.scroll_offset = self.scroll_offset.saturating_sub(n);
        }
    }

    fn scroll_down(&mut self, n: u16) {
        if self.scroll_offset == u16::MAX {
            return;
        }
        let next = self.scroll_offset.saturating_add(n);
        let max = self.total_visual.saturating_sub(1) as u16;
        if next >= max {
            self.scroll_offset = u16::MAX;
        } else {
            self.scroll_offset = next;
        }
    }

    fn handle_approval_key(&mut self, key: KeyEvent) -> Result<bool> {
        match key.code {
            KeyCode::Enter => {
                if let Some((name, arguments)) = self.pending_tool.take() {
                    let result = self.api.tool(&name, arguments)?;
                    self.push_tool(&format!("{} -> {}", result.tool, result.result));
                    self.status = "ready".into();
                }
                Ok(false)
            }
            KeyCode::Esc => {
                self.pending_tool = None;
                self.status = "ready".into();
                self.push_system("Tool call rejected.");
                Ok(false)
            }
            _ => Ok(false),
        }
    }

    fn handle_confirm_key(&mut self, key: KeyEvent) -> Result<bool> {
        let (approved, remember) = match key.code {
            KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('Y') => (true, false),
            KeyCode::Char('a') | KeyCode::Char('A') => (true, true),
            KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('N') => (false, false),
            _ => return Ok(false),
        };
        if let Some((decision_id, name, _, remember_supported, warning)) =
            self.pending_confirm.take()
        {
            let remember = remember && remember_supported;
            self.api.confirm_tool(&decision_id, approved, remember)?;
            let label = if approved {
                if remember {
                    "approved forever in this workspace"
                } else {
                    "approved once"
                }
            } else {
                "rejected"
            };
            if !warning.is_empty() {
                self.push_system(&format!("WARNING for {name}: {warning}"));
            }
            self.push_system(&format!("Tool {name} {label}."));
            self.status = if approved {
                if remember {
                    format!("running {name} (remembered)…")
                } else {
                    format!("running {name}…")
                }
            } else {
                "ready".into()
            };
        }
        Ok(false)
    }

    // ─── Commands ───────────────────────────────────────────────────

    fn handle_command(&mut self, command: &str) -> Result<bool> {
        let mut parts = command.splitn(3, ' ');
        let cmd = parts.next().unwrap_or_default();
        match cmd {
            "/quit" | "/exit" | "/q" => Ok(true),
            "/help" | "/h" | "/?" => {
                self.push_system(concat!(
                    "Commands:\n",
                    "  /help          Show this help\n",
                    "  /models        List available models\n",
                    "  /model <name>  Switch model by task name\n",
                    "  /new [title]   Create a new chat\n",
                    "  /chats         List saved chats\n",
                    "  /open <id>     Open a saved chat\n",
                    "  /health        Check API health\n",
                    "  /tools         List available tools\n",
                    "  /index [path]  Build code index\n",
                    "  /quit          Exit\n",
                    "\n",
                    "Modes (vim-style):\n",
                    "  Esc            Switch to NORMAL mode\n",
                    "  i / a          Switch to INSERT mode\n",
                    "\n",
                    "NORMAL mode keys:\n",
                    "  s              Stop generation\n",
                    "  t              Toggle last thinking block\n",
                    "  T              Toggle all thinking blocks\n",
                    "  j / k          Scroll down / up\n",
                    "  d / u          Half-page down / up\n",
                    "  G / g          Scroll to bottom / top\n",
                    "  ?              Show overview panel\n",
                    "  Tab            Cycle models\n",
                    "  /              Start typing a command\n",
                    "  q              Quit (stop gen if streaming)\n",
                    "\n",
                    "INSERT mode keys:\n",
                    "  Enter          Send message\n",
                    "  Alt+Enter      New line in input\n",
                    "  Alt+Backspace  Delete word\n",
                    "  Ctrl+S         Stop generation\n",
                    "  Tab            Cycle models\n",
                    "  Ctrl+C         Quit (stop gen if streaming)",
                ));
                Ok(false)
            }
            "/health" => {
                let health = self.api.health()?;
                self.push_system(&format!(
                    "API health: {}",
                    if health.ok { "OK" } else { "FAIL" }
                ));
                Ok(false)
            }
            "/models" => {
                let mut text = String::from("Available models:\n");
                for (i, m) in self.models.iter().enumerate() {
                    let marker = if i == self.model_idx { " ●" } else { " ○" };
                    text.push_str(&format!("{marker} {}", m.display));
                    if !m.runtime.is_empty() && !m.display.contains(&m.runtime) {
                        text.push_str(&format!("  [{}]", m.runtime));
                    }
                    if !m.note.is_empty() {
                        text.push_str(&format!("  — {}", m.note));
                    }
                    text.push('\n');
                }
                text.push_str("\nTab/Shift+Tab to cycle, or /model <name> to switch.\n");
                text.push_str("Any installed Ollama model can be used by its exact name.");
                self.push_system(&text);
                Ok(false)
            }
            "/tools" => {
                let tools = self.api.tools()?;
                self.push_system(&serde_json::to_string_pretty(&tools.tools)?);
                Ok(false)
            }
            "/chats" => {
                let chats = self.api.chats()?;
                if chats.chats.is_empty() {
                    self.push_system("No saved chats. Use /new to create one.");
                } else {
                    let mut text = String::from("Saved chats:\n");
                    for c in &chats.chats {
                        let id = c.get("id").and_then(Value::as_str).unwrap_or("?");
                        let title = c
                            .get("meta")
                            .and_then(|m| m.get("title"))
                            .and_then(Value::as_str)
                            .unwrap_or("Untitled");
                        text.push_str(&format!("  {id}  {title}\n"));
                    }
                    text.push_str("\nUse /open <id> to continue a chat.");
                    self.push_system(&text);
                }
                Ok(false)
            }
            "/new" => {
                let title = parts.next().unwrap_or("New chat").trim();
                let chat = self.api.create_chat(title, &self.cwd)?.chat;
                self.chat_id = chat.id.clone();
                self.chat_title = title.to_string();
                self.transcript.clear();
                self.push_system(&format!("Created chat \"{}\"", title));
                Ok(false)
            }
            "/open" => {
                let Some(chat_id) = parts.next() else {
                    self.push_system("Usage: /open <chat_id>");
                    return Ok(false);
                };
                self.open_chat(chat_id.trim())?;
                Ok(false)
            }
            "/model" => {
                if let Some(query) = parts.next() {
                    let query = query.trim();
                    if let Some(idx) = self.find_model_index(query) {
                        self.model_idx = idx;
                        self.status = format!("model: {}", self.current_display());
                        self.push_system(&format!("Switched to {}", self.current_display()));
                    } else {
                        let available: Vec<String> = self
                            .models
                            .iter()
                            .map(|m| {
                                if m.runtime.is_empty() {
                                    m.task.clone()
                                } else {
                                    format!("{} / {}", m.task, m.runtime)
                                }
                            })
                            .collect();
                        self.push_error(&format!(
                            "Unknown model \"{query}\". Available: {}",
                            available.join(", ")
                        ));
                    }
                } else {
                    let mut text = String::from("Current: ");
                    text.push_str(self.current_display());
                    text.push_str("\nAvailable models: ");
                    let models: Vec<String> = self
                        .models
                        .iter()
                        .map(|m| {
                            if m.runtime.is_empty() {
                                m.task.clone()
                            } else {
                                format!("{} / {}", m.task, m.runtime)
                            }
                        })
                        .collect();
                    text.push_str(&models.join(", "));
                    self.push_system(&text);
                }
                Ok(false)
            }
            "/index" => {
                let path = parts.next().unwrap_or(".");
                let result = self.api.index(path)?;
                self.push_tool(&format!(
                    "index {path}:\n{}",
                    serde_json::to_string_pretty(&result)?
                ));
                Ok(false)
            }
            "/tool" => {
                let name = parts.next().unwrap_or_default().trim().to_string();
                let raw_args = parts.next().unwrap_or("{}");
                if name.is_empty() {
                    self.push_system("Usage: /tool <name> <json>");
                    return Ok(false);
                }
                let arguments: Value = serde_json::from_str(raw_args)
                    .with_context(|| format!("invalid JSON: {raw_args}"))?;
                self.pending_tool = Some((name.clone(), arguments));
                self.status = format!("approve {name}?");
                self.push_tool(&format!("pending: {name}({raw_args})"));
                Ok(false)
            }
            _ => {
                self.push_error(&format!("Unknown command: {cmd}. Try /help"));
                Ok(false)
            }
        }
    }

    // ─── Chat ───────────────────────────────────────────────────────

    fn send_prompt(&mut self, prompt: &str) -> Result<()> {
        if self.pending_response.is_some() {
            self.push_error("Wait for the current response to finish.");
            return Ok(());
        }
        self.push_chat(Role::User, prompt);
        let messages = self.to_chat_messages();

        // Blocks are created lazily as deltas arrive — that way the order
        // (thinking → text → thinking → tool) matches what the model emits.
        self.status = "streaming".into();

        let api = self.api.clone();
        let task = self.current_task().to_string();
        let cwd = self.cwd.clone();
        let chat_id = self.chat_id.clone();
        let meta = json!({ "title": self.chat_title, "workspace": self.cwd, "client": "ca-tui" });
        let (tx, rx) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let cancel_flag = cancelled.clone();

        thread::spawn(
            move || match api.chat_stream(chat_id, messages, meta, &task, &cwd) {
                Ok(stream) => {
                    for event in stream {
                        if cancel_flag.load(Ordering::Relaxed) {
                            break;
                        }
                        if tx.send(event).is_err() {
                            break;
                        }
                    }
                }
                Err(e) => {
                    let _ = tx.send(StreamEvent::Error {
                        message: e.to_string(),
                    });
                }
            },
        );

        let now = std::time::Instant::now();
        self.pending_response = Some(PendingResponse {
            receiver: rx,
            assistant_index: None,
            thinking_index: None,
            tool_lines: std::collections::HashMap::new(),
            tool_args: std::collections::HashMap::new(),
            tool_names: std::collections::HashMap::new(),
            cancelled,
            started_at: now,
            last_event_at: now,
            total_chars: 0,
        });
        Ok(())
    }

    fn poll_pending_response(&mut self) {
        let Some(pending) = &mut self.pending_response else {
            return;
        };
        let mut got_any = false;

        // Cap events per tick so streaming becomes visible animation instead of
        // a single big jump. Combined with the 30ms tick rate this gives ~200
        // events/sec, which is plenty for token-by-token text but still slow
        // enough that the user sees tool-call args appear progressively.
        let mut events_this_tick = 0usize;
        const MAX_EVENTS_PER_TICK: usize = 6;
        loop {
            if events_this_tick >= MAX_EVENTS_PER_TICK {
                break;
            }
            match pending.receiver.try_recv() {
                Ok(event) => {
                    got_any = true;
                    events_this_tick += 1;
                    pending.last_event_at = std::time::Instant::now();
                    match &event {
                        StreamEvent::Delta { content } | StreamEvent::Thinking { content } => {
                            pending.total_chars += content.chars().count();
                        }
                        StreamEvent::ToolCallDelta { delta, .. } => {
                            pending.total_chars += delta.chars().count();
                        }
                        _ => {}
                    }
                    match event {
                        StreamEvent::Delta { content } => {
                            // Switch role: a delta after thinking starts a new
                            // assistant block, never extends the thinking block.
                            pending.thinking_index = None;
                            let idx = match pending.assistant_index {
                                Some(i) if self.transcript.get(i).is_some() => i,
                                _ => {
                                    let i = self.transcript.len();
                                    self.transcript.push(ChatLine {
                                        role: Role::Assistant,
                                        text: String::new(),
                                        chat_visible: true,
                                        collapsed: false,
                                    });
                                    pending.assistant_index = Some(i);
                                    i
                                }
                            };
                            if let Some(line) = self.transcript.get_mut(idx) {
                                line.text.push_str(&content);
                            }
                        }
                        StreamEvent::Thinking { content } => {
                            // Switch role: thinking after assistant text starts
                            // a new thinking block below the prior reply.
                            pending.assistant_index = None;
                            let idx = match pending.thinking_index {
                                Some(i) if self.transcript.get(i).is_some() => i,
                                _ => {
                                    let i = self.transcript.len();
                                    self.transcript.push(ChatLine {
                                        role: Role::Thinking,
                                        text: String::new(),
                                        chat_visible: false,
                                        collapsed: false,
                                    });
                                    pending.thinking_index = Some(i);
                                    i
                                }
                            };
                            if let Some(line) = self.transcript.get_mut(idx) {
                                line.text.push_str(&content);
                            }
                        }
                        StreamEvent::ToolCallStart { index, name } => {
                            // Tool call ends the current text/thinking block —
                            // do NOT reclassify previous assistant text as
                            // reasoning. The model genuinely answered first.
                            pending.assistant_index = None;
                            pending.thinking_index = None;
                            let line_idx = self.transcript.len();
                            self.transcript.push(ChatLine {
                                role: Role::Tool,
                                text: format!("→ {name} …"),
                                chat_visible: true,
                                collapsed: false,
                            });
                            pending.tool_lines.insert(index, line_idx);
                            pending.tool_args.insert(index, String::new());
                            pending.tool_names.insert(index, name.clone());
                            self.status = format!("calling {name}…");
                        }
                        StreamEvent::ToolCallDelta { index, name, delta } => {
                            // Lazy start (some streams skip start)
                            let line_idx = match pending.tool_lines.get(&index).copied() {
                                Some(i) => i,
                                None => {
                                    let i = self.transcript.len();
                                    self.transcript.push(ChatLine {
                                        role: Role::Tool,
                                        text: format!("→ {name} …"),
                                        chat_visible: true,
                                        collapsed: false,
                                    });
                                    pending.tool_lines.insert(index, i);
                                    pending.tool_args.insert(index, String::new());
                                    pending.tool_names.insert(index, name.clone());
                                    i
                                }
                            };
                            let acc = pending.tool_args.entry(index).or_default();
                            acc.push_str(&delta);
                            let nm = pending.tool_names.get(&index).cloned().unwrap_or(name);
                            let rendered = format_tool_call(&nm, acc);
                            if let Some(line) = self.transcript.get_mut(line_idx) {
                                line.text = rendered;
                            }
                        }
                        StreamEvent::ToolCall { name, arguments } => {
                            // Final tool call: re-render the matching live line if we have one,
                            // otherwise create a new line.
                            let mut placed = false;
                            for (idx, line_idx) in pending.tool_lines.clone().iter() {
                                let nm = pending.tool_names.get(idx).cloned().unwrap_or_default();
                                if nm == name {
                                    if let Some(line) = self.transcript.get_mut(*line_idx) {
                                        line.text = format_tool_call(&name, &arguments);
                                    }
                                    placed = true;
                                    pending.tool_lines.remove(idx);
                                    pending.tool_args.remove(idx);
                                    pending.tool_names.remove(idx);
                                    break;
                                }
                            }
                            if !placed {
                                self.transcript.push(ChatLine {
                                    role: Role::Tool,
                                    text: format_tool_call(&name, &arguments),
                                    chat_visible: true,
                                    collapsed: false,
                                });
                            }
                            pending.assistant_index = None;
                            pending.thinking_index = None;
                            self.status = format!("running {name}…");
                        }
                        StreamEvent::ToolResult { name, result } => {
                            let summary = format_tool_result(&name, &result);
                            self.transcript.push(ChatLine {
                                role: Role::FunctionResult,
                                text: summary,
                                chat_visible: true,
                                collapsed: false,
                            });
                            self.status = format!("{name} done");
                            // Next text/thinking starts a new block — created lazily.
                            pending.assistant_index = None;
                            pending.thinking_index = None;
                        }
                        StreamEvent::ToolPending {
                            decision_id,
                            name,
                            arguments,
                            remember_supported,
                            warning,
                        } => {
                            self.pending_confirm = Some((
                                decision_id,
                                name.clone(),
                                arguments.clone(),
                                remember_supported,
                                warning.clone(),
                            ));
                            let preview = truncate_str(&arguments, 80);
                            let controls = if remember_supported {
                                "Enter/y=once  a=always here  Esc/n=no"
                            } else {
                                "Enter/y=once  Esc/n=no"
                            };
                            let mut text = format!("⚠ approve {name}({preview})? {controls}");
                            if !warning.is_empty() {
                                text.push_str(&format!("\n\nBIG WARNING: {warning}"));
                            }
                            self.transcript.push(ChatLine {
                                role: Role::Tool,
                                text,
                                chat_visible: true,
                                collapsed: false,
                            });
                            self.status = format!("waiting approval: {name}");
                        }
                        StreamEvent::Notice { level, message } => {
                            let prefix = match level.as_str() {
                                "warn" | "warning" => "⚠ ",
                                "error" => "✗ ",
                                _ => "ℹ ",
                            };
                            self.transcript.push(ChatLine {
                                role: Role::Tool,
                                text: format!("{prefix}{message}"),
                                chat_visible: true,
                                collapsed: false,
                            });
                            self.status = message.clone();
                            pending.assistant_index = None;
                            pending.thinking_index = None;
                        }
                        StreamEvent::ModelInfo { model } => {
                            if let Some(idx) =
                                self.models.iter().position(|m| m.display.contains(&model))
                            {
                                self.model_idx = idx;
                            }
                        }
                        StreamEvent::Done {
                            model,
                            stop_reason,
                        } => {
                            // Remove empty trailing assistant bubble (lazy
                            // creation may leave one if the stream ended right
                            // after a tool call without follow-up text).
                            if let Some(idx) = pending.assistant_index {
                                if let Some(line) = self.transcript.get(idx) {
                                    if line.text.is_empty() && line.role == Role::Assistant {
                                        self.transcript.remove(idx);
                                    }
                                }
                            }
                            // Same for an empty thinking placeholder.
                            if let Some(idx) = pending.thinking_index {
                                if let Some(line) = self.transcript.get(idx) {
                                    if line.text.is_empty() && line.role == Role::Thinking {
                                        self.transcript.remove(idx);
                                    }
                                }
                            }
                            self.status = if !stop_reason.is_empty() && stop_reason != "completed" {
                                format!("ready · {stop_reason}")
                            } else if !model.is_empty() {
                                format!("ready · {model}")
                            } else {
                                "ready".into()
                            };
                            self.pending_response = None;
                            self.scroll_offset = u16::MAX;
                            return;
                        }
                        StreamEvent::TerminalOutput { session_id, chunk } => {
                            let prefix = if session_id.is_empty() {
                                "[term] ".to_string()
                            } else {
                                format!("[term {session_id}] ")
                            };
                            self.transcript.push(ChatLine {
                                role: Role::System,
                                text: format!("{prefix}{chunk}"),
                                chat_visible: true,
                                collapsed: false,
                            });
                        }
                        StreamEvent::Error { message } => {
                            self.push_error(&message);
                            self.status = "error".into();
                            self.pending_response = None;
                            return;
                        }
                    }
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    let nothing_received = pending.assistant_index.is_none()
                        && pending.thinking_index.is_none()
                        && pending.tool_lines.is_empty();
                    if nothing_received {
                        self.push_error("Stream ended unexpectedly.");
                    }
                    self.status = "ready".into();
                    self.pending_response = None;
                    return;
                }
            }
        }

        if got_any {
            self.scroll_offset = u16::MAX;
        }
    }

    fn to_chat_messages(&self) -> Vec<ChatMessage> {
        self.transcript
            .iter()
            .filter(|l| l.chat_visible)
            .filter(|l| !is_placeholder(&l.text))
            .enumerate()
            .map(|(i, l)| ChatMessage {
                role: match l.role {
                    Role::User => "user".into(),
                    Role::Assistant => "assistant".into(),
                    _ => "system".into(),
                },
                index: i,
                content: vec![ContentBlock {
                    content_type: "text".into(),
                    text: l.text.clone(),
                }],
            })
            .collect()
    }

    fn push_chat(&mut self, role: Role, text: &str) {
        self.push(role, text, true);
    }
    fn push_system(&mut self, text: &str) {
        self.push(Role::System, text, false);
    }
    fn push_tool(&mut self, text: &str) {
        self.push(Role::Tool, text, false);
    }
    fn push_error(&mut self, text: &str) {
        self.push(Role::Error, text, false);
    }

    fn push(&mut self, role: Role, text: &str, chat_visible: bool) {
        self.transcript.push(ChatLine {
            role,
            text: text.into(),
            chat_visible,
            collapsed: false,
        });
    }

    fn toggle_last_thinking(&mut self) {
        for line in self.transcript.iter_mut().rev() {
            if line.role == Role::Thinking {
                line.collapsed = !line.collapsed;
                return;
            }
        }
    }

    fn toggle_all_thinking(&mut self) {
        self.thinking_collapsed_global = !self.thinking_collapsed_global;
        let target = self.thinking_collapsed_global;
        for line in &mut self.transcript {
            if line.role == Role::Thinking {
                line.collapsed = target;
            }
        }
    }

    fn draw_overview(&self, frame: &mut ratatui::Frame, area: Rect) {
        let popup_w = area.width.min(60).max(30);
        let popup_h = area.height.min(30).max(10);
        let x = area.x + (area.width.saturating_sub(popup_w)) / 2;
        let y = area.y + (area.height.saturating_sub(popup_h)) / 2;
        let popup_area = Rect::new(x, y, popup_w, popup_h);

        frame.render_widget(Clear, popup_area);

        let mut lines: Vec<Line<'static>> = Vec::new();
        lines.push(Line::from(Span::styled(
            " Models",
            Style::default()
                .fg(Color::Green)
                .add_modifier(Modifier::BOLD),
        )));
        lines.push(Line::from(""));
        for (i, m) in self.models.iter().enumerate() {
            let marker = if i == self.model_idx { "● " } else { "○ " };
            let style = if i == self.model_idx {
                Style::default()
                    .fg(Color::Green)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };
            let mut text = format!("{marker}{}", m.display);
            if !m.runtime.is_empty() && !m.display.contains(&m.runtime) {
                text.push_str(&format!("  [{}]", m.runtime));
            }
            if !m.note.is_empty() {
                text.push_str(&format!("  — {}", m.note));
            }
            lines.push(Line::from(Span::styled(text, style)));
        }

        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            " Tools",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )));
        lines.push(Line::from(""));
        for t in &self.cached_tools {
            let name = t.name.as_str();
            let desc = t.description.as_str();
            let enabled = t.enabled;
            let marker = if enabled { "●" } else { "○" };
            let style = if enabled {
                Style::default().fg(Color::Yellow)
            } else {
                Style::default().fg(Color::DarkGray)
            };
            let short_desc = truncate_str(desc, 35);
            lines.push(Line::from(vec![
                Span::styled(format!(" {marker} {name:<16}"), style),
                Span::styled(
                    format!(" {short_desc}"),
                    Style::default().fg(Color::DarkGray),
                ),
            ]));
        }

        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            " Press ? or Esc to close",
            Style::default().fg(Color::DarkGray),
        )));

        let block = Block::default()
            .title(" Overview ")
            .title_style(
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )
            .title_alignment(Alignment::Center)
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::Cyan));

        let paragraph = Paragraph::new(lines)
            .block(block)
            .wrap(Wrap { trim: false });
        frame.render_widget(paragraph, popup_area);
    }

    fn open_chat(&mut self, chat_id: &str) -> Result<()> {
        let chat = self.api.load_chat(chat_id)?.chat;
        self.chat_id = chat.id;
        self.chat_title = chat
            .meta
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("Chat")
            .to_string();

        if let Some(last_model) = chat.meta.get("last_model").and_then(|v| v.as_str()) {
            self.switch_to_model_by_runtime(last_model);
        }

        self.transcript.clear();
        for msg in chat.messages {
            let base_role = Role::from_str(&msg.role);
            for block in &msg.content {
                let ct = block.content_type.as_str();
                match ct {
                    "thinking" => {
                        if !block.text.is_empty() {
                            self.push(Role::Thinking, &block.text, false);
                        }
                    }
                    "function_call" => {
                        // block.text is JSON like {"name":"...","arguments":{...}}
                        let parsed: Value =
                            serde_json::from_str(&block.text).unwrap_or(Value::Null);
                        let nm = parsed.get("name").and_then(Value::as_str).unwrap_or("");
                        let args_val = parsed
                            .get("arguments")
                            .cloned()
                            .unwrap_or(Value::Object(Default::default()));
                        let args_str = serde_json::to_string(&args_val).unwrap_or_default();
                        let pretty = format_tool_call(nm, &args_str);
                        self.push(Role::Tool, &pretty, true);
                    }
                    "function" => {
                        // Saved chat: text holds the raw result JSON. Show
                        // the same compact summary used during streaming.
                        let name = msg
                            .content
                            .iter()
                            .find_map(|b| {
                                if b.content_type == "function_call" {
                                    serde_json::from_str::<Value>(&b.text).ok().and_then(|v| {
                                        v.get("name").and_then(Value::as_str).map(str::to_string)
                                    })
                                } else {
                                    None
                                }
                            })
                            .unwrap_or_else(|| "tool".to_string());
                        let summary = format_tool_result(&name, &block.text);
                        self.push(Role::FunctionResult, &summary, true);
                    }
                    _ => {
                        if !block.text.is_empty() && !is_placeholder(&block.text) {
                            let visible = matches!(base_role, Role::User | Role::Assistant);
                            self.push(base_role, &block.text, visible);
                        }
                    }
                }
            }
        }
        self.push_system(&format!("Opened chat \"{}\"", self.chat_title));
        Ok(())
    }
}

// ─── Utilities ──────────────────────────────────────────────────────

fn char_to_byte(s: &str, char_idx: usize) -> usize {
    s.char_indices()
        .nth(char_idx)
        .map(|(i, _)| i)
        .unwrap_or(s.len())
}

fn word_wrap(text: &str, width: usize) -> Vec<String> {
    if text.is_empty() {
        return vec![String::new()];
    }
    if width == 0 {
        return vec![text.to_string()];
    }
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cur_w: usize = 0;

    for word in text.split_whitespace() {
        let ww = word.chars().count();
        if ww > width && cur.is_empty() {
            let mut chunk = String::new();
            let mut cw: usize = 0;
            for ch in word.chars() {
                if cw >= width {
                    lines.push(std::mem::take(&mut chunk));
                    cw = 0;
                }
                chunk.push(ch);
                cw += 1;
            }
            cur = chunk;
            cur_w = cw;
            continue;
        }
        if cur.is_empty() {
            cur = word.to_string();
            cur_w = ww;
        } else if cur_w + 1 + ww > width {
            lines.push(std::mem::take(&mut cur));
            if ww > width {
                let mut chunk = String::new();
                let mut cw: usize = 0;
                for ch in word.chars() {
                    if cw >= width {
                        lines.push(std::mem::take(&mut chunk));
                        cw = 0;
                    }
                    chunk.push(ch);
                    cw += 1;
                }
                cur = chunk;
                cur_w = cw;
            } else {
                cur = word.to_string();
                cur_w = ww;
            }
        } else {
            cur.push(' ');
            cur.push_str(word);
            cur_w += 1 + ww;
        }
    }
    lines.push(cur);
    lines
}

fn shorten_path(path: &str, max_len: usize) -> String {
    if path.chars().count() <= max_len || max_len < 10 {
        return path.to_string();
    }
    if let Some(home) = std::env::var_os("HOME") {
        let home = home.to_string_lossy();
        if path.starts_with(home.as_ref()) {
            let short = format!("~{}", path.strip_prefix(home.as_ref()).unwrap_or(path));
            if short.chars().count() <= max_len {
                return short;
            }
        }
    }
    let half = (max_len - 3) / 2;
    let prefix: String = path.chars().take(half).collect();
    let suffix: String = path
        .chars()
        .rev()
        .take(half)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    format!("{prefix}…{suffix}")
}

fn is_placeholder(text: &str) -> bool {
    matches!(
        text.trim(),
        "generating" | "generating…" | "thinking…" | "response..."
    )
}

// ─── Markdown rendering ─────────────────────────────────────────────

fn render_md_line(text: &str, base_style: Style) -> Vec<Span<'static>> {
    let trimmed = text.trim_start();

    // Headers
    if trimmed.starts_with("### ") {
        return vec![Span::styled(
            format!("   {}", &trimmed[4..]),
            base_style.fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )];
    }
    if trimmed.starts_with("## ") {
        return vec![Span::styled(
            format!("  {}", &trimmed[3..]),
            base_style.fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )];
    }
    if trimmed.starts_with("# ") {
        return vec![Span::styled(
            trimmed[2..].to_string(),
            base_style
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD | Modifier::UNDERLINED),
        )];
    }

    // Horizontal rule
    if trimmed == "---" || trimmed == "***" || trimmed == "___" {
        return vec![Span::styled(
            "────────────────────────────".to_string(),
            Style::default().fg(Color::DarkGray),
        )];
    }

    // List items — prefix with bullet then parse inline
    let (prefix, rest) = if trimmed.starts_with("- ") || trimmed.starts_with("* ") {
        ("  • ".to_string(), &trimmed[2..])
    } else if let Some(stripped) = strip_numbered_list(trimmed) {
        (
            format!("  {}. ", &trimmed[..trimmed.len() - stripped.len() - 2]),
            stripped,
        )
    } else {
        (String::new(), text)
    };

    let mut spans: Vec<Span<'static>> = Vec::new();
    if !prefix.is_empty() {
        spans.push(Span::styled(prefix, base_style.fg(Color::DarkGray)));
    }
    parse_inline_md(rest, base_style, &mut spans);
    spans
}

fn render_markdown_table(
    lines: &[&str],
    start: usize,
    max_width: usize,
) -> Option<(Vec<Vec<Span<'static>>>, usize)> {
    if start + 1 >= lines.len() {
        return None;
    }
    let header = parse_table_row(lines[start])?;
    let separator = parse_table_separator(lines[start + 1])?;
    if header.len() < 2 || separator.len() != header.len() {
        return None;
    }

    let cols = header.len();
    let mut rows = vec![header];
    let mut consumed = 2usize;
    while start + consumed < lines.len() {
        let Some(row) = parse_table_row(lines[start + consumed]) else {
            break;
        };
        if row.len() != cols {
            break;
        }
        rows.push(row);
        consumed += 1;
    }

    let mut widths = vec![3usize; cols];
    for row in &rows {
        for (idx, cell) in row.iter().enumerate() {
            widths[idx] = widths[idx].max(UnicodeWidthStr::width(cell.as_str()));
        }
    }
    fit_table_width(&mut widths, max_width);

    let mut out: Vec<Vec<Span<'static>>> = Vec::new();
    out.push(vec![Span::styled(
        table_border_line("┌", "┬", "┐", &widths),
        Style::default().fg(Color::DarkGray),
    )]);
    out.push(table_row_line(&rows[0], &widths, true));
    out.push(vec![Span::styled(
        table_border_line("├", "┼", "┤", &widths),
        Style::default().fg(Color::DarkGray),
    )]);
    for row in rows.iter().skip(1) {
        out.push(table_row_line(row, &widths, false));
    }
    out.push(vec![Span::styled(
        table_border_line("└", "┴", "┘", &widths),
        Style::default().fg(Color::DarkGray),
    )]);
    Some((out, consumed))
}

fn parse_table_row(line: &str) -> Option<Vec<String>> {
    let trimmed = line.trim();
    if !trimmed.contains('|') || trimmed.starts_with("```") {
        return None;
    }
    let body = trimmed.trim_matches('|');
    let cells: Vec<String> = body
        .split('|')
        .map(|cell| cell.trim().to_string())
        .collect();
    if cells.len() < 2 || cells.iter().all(|cell| cell.is_empty()) {
        return None;
    }
    Some(cells)
}

fn parse_table_separator(line: &str) -> Option<Vec<()>> {
    let cells = parse_table_row(line)?;
    let mut out = Vec::new();
    for cell in cells {
        let compact: String = cell.chars().filter(|c| !c.is_whitespace()).collect();
        if compact.len() < 3 {
            return None;
        }
        if !compact.chars().all(|c| c == '-' || c == ':') || !compact.contains('-') {
            return None;
        }
        out.push(());
    }
    Some(out)
}

fn fit_table_width(widths: &mut [usize], max_width: usize) {
    if widths.is_empty() {
        return;
    }
    let min_col = 6usize;
    while table_total_width(widths) > max_width && widths.iter().any(|w| *w > min_col) {
        if let Some((idx, _)) = widths.iter().enumerate().max_by_key(|(_, w)| **w) {
            if widths[idx] > min_col {
                widths[idx] -= 1;
            } else {
                break;
            }
        }
    }
}

fn table_total_width(widths: &[usize]) -> usize {
    if widths.is_empty() {
        return 0;
    }
    widths.iter().sum::<usize>() + widths.len() * 3 + 1
}

fn table_border_line(left: &str, mid: &str, right: &str, widths: &[usize]) -> String {
    let mut s = String::from(left);
    for (idx, width) in widths.iter().enumerate() {
        s.push_str(&"─".repeat(width + 2));
        if idx + 1 == widths.len() {
            s.push_str(right);
        } else {
            s.push_str(mid);
        }
    }
    s
}

fn table_row_line(row: &[String], widths: &[usize], header: bool) -> Vec<Span<'static>> {
    let mut spans = vec![Span::styled("│", Style::default().fg(Color::DarkGray))];
    for (idx, width) in widths.iter().enumerate() {
        let cell = row.get(idx).map(String::as_str).unwrap_or("");
        let fitted = fit_cell(cell, *width);
        let pad = width.saturating_sub(UnicodeWidthStr::width(fitted.as_str()));
        let style = if header {
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        spans.push(Span::raw(" "));
        spans.push(Span::styled(format!("{fitted}{}", " ".repeat(pad)), style));
        spans.push(Span::raw(" "));
        spans.push(Span::styled("│", Style::default().fg(Color::DarkGray)));
    }
    spans
}

fn fit_cell(cell: &str, width: usize) -> String {
    if UnicodeWidthStr::width(cell) <= width {
        return cell.to_string();
    }
    if width <= 1 {
        return "…".to_string();
    }
    let mut out = String::new();
    let mut used = 0usize;
    let target = width.saturating_sub(1);
    for ch in cell.chars() {
        let cw = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + cw > target {
            break;
        }
        out.push(ch);
        used += cw;
    }
    out.push('…');
    out
}

fn strip_numbered_list(s: &str) -> Option<&str> {
    let s = s.trim_start();
    let dot = s.find(". ")?;
    if dot > 0 && dot <= 3 && s[..dot].chars().all(|c| c.is_ascii_digit()) {
        Some(&s[dot + 2..])
    } else {
        None
    }
}

fn parse_inline_md(text: &str, base_style: Style, out: &mut Vec<Span<'static>>) {
    let chars: Vec<char> = text.chars().collect();
    let len = chars.len();
    let mut i = 0;
    let mut buf = String::new();

    while i < len {
        // Inline code: `...`
        if chars[i] == '`' {
            if !buf.is_empty() {
                out.push(Span::styled(std::mem::take(&mut buf), base_style));
            }
            i += 1;
            let mut code = String::new();
            while i < len && chars[i] != '`' {
                code.push(chars[i]);
                i += 1;
            }
            if i < len {
                i += 1;
            } // skip closing `
            out.push(Span::styled(code, Style::default().fg(Color::Yellow)));
            continue;
        }

        // Bold: **...**
        if i + 1 < len && chars[i] == '*' && chars[i + 1] == '*' {
            if !buf.is_empty() {
                out.push(Span::styled(std::mem::take(&mut buf), base_style));
            }
            i += 2;
            let mut bold = String::new();
            while i + 1 < len && !(chars[i] == '*' && chars[i + 1] == '*') {
                bold.push(chars[i]);
                i += 1;
            }
            if i + 1 < len {
                i += 2;
            } // skip **
            out.push(Span::styled(bold, base_style.add_modifier(Modifier::BOLD)));
            continue;
        }

        // Italic: *...*  (single star, not at word boundary issues — keep simple)
        if chars[i] == '*' && i + 1 < len && chars[i + 1] != ' ' {
            if let Some(end) = find_closing_star(&chars, i + 1) {
                if !buf.is_empty() {
                    out.push(Span::styled(std::mem::take(&mut buf), base_style));
                }
                i += 1;
                let mut italic = String::new();
                while i < end {
                    italic.push(chars[i]);
                    i += 1;
                }
                i += 1; // skip closing *
                out.push(Span::styled(
                    italic,
                    base_style.add_modifier(Modifier::ITALIC),
                ));
                continue;
            }
        }

        buf.push(chars[i]);
        i += 1;
    }

    if !buf.is_empty() {
        out.push(Span::styled(buf, base_style));
    }
}

fn find_closing_star(chars: &[char], start: usize) -> Option<usize> {
    for i in start..chars.len() {
        if chars[i] == '*' && (i == 0 || chars[i - 1] != ' ') {
            return Some(i);
        }
    }
    None
}

// ─── Terminal session ───────────────────────────────────────────────

struct TerminalSession {
    terminal: Terminal<CrosstermBackend<Stdout>>,
}

impl TerminalSession {
    fn start() -> Result<Self> {
        enable_raw_mode()?;
        let mut stdout = io::stdout();
        execute!(
            stdout,
            EnterAlternateScreen,
            crossterm::event::EnableMouseCapture,
            crossterm::event::EnableBracketedPaste,
        )?;
        let _ = execute!(
            stdout,
            PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES)
        );
        let backend = CrosstermBackend::new(stdout);
        let terminal = Terminal::new(backend)?;
        Ok(Self { terminal })
    }

    fn draw<F>(&mut self, f: F) -> Result<()>
    where
        F: FnOnce(&mut ratatui::Frame),
    {
        self.terminal.draw(f)?;
        Ok(())
    }
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        let _ = execute!(
            self.terminal.backend_mut(),
            PopKeyboardEnhancementFlags,
            crossterm::event::DisableBracketedPaste,
            crossterm::event::DisableMouseCapture,
            LeaveAlternateScreen,
        );
        let _ = disable_raw_mode();
        let _ = self.terminal.show_cursor();
    }
}

/// Render a one-line summary of a tool execution result.
/// Examples:
///   ✓ edit_file → src/main.py
///   ✓ read_file → README.md
///   ✗ edit_file → src/main.py: edit at line 42: old_lines do not match
///   ✓ shell exit=0
fn format_tool_result(name: &str, result_json: &str) -> String {
    let parsed: Value = serde_json::from_str(result_json).unwrap_or(Value::Null);

    // Failure: explicit `error` field, or status == "rejected_by_user", or
    // an exit_code != 0 from shell tools.
    let error_msg = parsed.get("error").and_then(Value::as_str);
    let status = parsed.get("status").and_then(Value::as_str);
    let exit_code = parsed.get("exit_code").and_then(Value::as_i64);
    let failed = error_msg.is_some()
        || matches!(status, Some("rejected_by_user") | Some("error"))
        || matches!(exit_code, Some(c) if c != 0);
    let mark = if failed { "✗" } else { "✓" };

    // Pull a "what" out of the result. For most tools the most useful thing
    // is the file path it touched; tool args were already shown in the call.
    let path = parsed
        .get("path")
        .and_then(Value::as_str)
        .or_else(|| parsed.get("file").and_then(Value::as_str));
    let mut head = format!("{mark} {name}");
    if let Some(p) = path {
        head.push_str(&format!(" → {p}"));
    }

    // For edit_file: show how many edits were applied.
    if let Some(n) = parsed.get("edits_applied").and_then(Value::as_i64) {
        head.push_str(&format!("  ({n} edit{})", if n == 1 { "" } else { "s" }));
    }

    // For shell-style tools: show exit code.
    if let Some(c) = exit_code {
        head.push_str(&format!("  exit={c}"));
    }

    // For list_directory / search-style tools: show count if present.
    if path.is_none() {
        if let Some(c) = parsed.get("count").and_then(Value::as_i64) {
            head.push_str(&format!("  ({c} items)"));
        }
    }

    if failed {
        let detail = error_msg
            .map(str::to_string)
            .or_else(|| status.map(|s| s.to_string()))
            .unwrap_or_default();
        if !detail.is_empty() {
            // Keep it short — full error stays in the JSON if needed.
            let one_line = detail.replace('\n', " ");
            let truncated: String = one_line.chars().take(200).collect();
            head.push_str(&format!(": {truncated}"));
        }
    }

    head
}

/// Render a tool call with friendly preview (especially for write/edit/shell tools).
fn format_tool_call(name: &str, arguments_json: &str) -> String {
    let parsed: Value = serde_json::from_str(arguments_json).unwrap_or(Value::Null);
    let path = parsed.get("path").and_then(Value::as_str);
    let content = parsed.get("content").and_then(Value::as_str);
    let new_text = parsed.get("new_text").and_then(Value::as_str);
    let old_text = parsed.get("old_text").and_then(Value::as_str);
    let command = parsed.get("command").and_then(Value::as_str);
    let query = parsed.get("query").and_then(Value::as_str);
    let pattern = parsed.get("pattern").and_then(Value::as_str);

    let mut header = format!("→ {name}");
    if let Some(p) = path {
        header.push_str(&format!("  {p}"));
    }
    if let Some(cmd) = command {
        header.push_str(&format!("  {cmd}"));
    }
    if let Some(q) = query {
        header.push_str(&format!("  \"{q}\""));
    }
    if let Some(p) = pattern {
        header.push_str(&format!("  {p}"));
    }

    let mut body = String::new();
    if let Some(c) = content {
        body.push_str(&render_text_block(c, "content"));
    }
    if let Some(o) = old_text {
        body.push_str(&render_text_block(o, "old_text"));
    }
    if let Some(n) = new_text {
        if !body.is_empty() {
            body.push('\n');
        }
        body.push_str(&render_text_block(n, "new_text"));
    }

    if body.is_empty() {
        let raw = truncate_str(arguments_json, 200);
        if raw != "{}" {
            header.push_str(&format!("  {raw}"));
        }
        header
    } else {
        format!("{header}\n{body}")
    }
}

fn render_text_block(text: &str, label: &str) -> String {
    const MAX_LINES: usize = 30;
    const MAX_CHARS_PER_LINE: usize = 200;
    let lines: Vec<&str> = text.split('\n').collect();
    let total = lines.len();
    let mut out = String::new();
    out.push_str(&format!("  {label} ({total} line",));
    if total != 1 {
        out.push('s');
    }
    out.push_str("):\n");
    let shown: Vec<&&str> = lines.iter().take(MAX_LINES).collect();
    for line in shown.iter() {
        let l = truncate_str(line, MAX_CHARS_PER_LINE);
        out.push_str("    │ ");
        out.push_str(&l);
        out.push('\n');
    }
    if total > MAX_LINES {
        out.push_str(&format!("    │ … {} more line(s)\n", total - MAX_LINES));
    }
    out
}

fn truncate_str(s: &str, max_chars: usize) -> String {
    let char_count = s.chars().count();
    if char_count <= max_chars {
        return s.to_string();
    }
    let truncated: String = s.chars().take(max_chars).collect();
    format!("{truncated}…")
}

/// Wrap input text into visible lines: respects explicit \n and word-wraps long lines.
fn wrap_input_lines(text: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![text.to_string()];
    }
    let mut out: Vec<String> = Vec::new();
    for raw_line in text.split('\n') {
        if raw_line.is_empty() {
            out.push(String::new());
            continue;
        }
        let mut current = String::new();
        let mut current_chars = 0usize;
        for word in split_keep_spaces(raw_line) {
            let wlen = word.chars().count();
            if wlen >= width {
                if !current.is_empty() {
                    out.push(std::mem::take(&mut current));
                    current_chars = 0;
                }
                let mut chunk = String::new();
                let mut count = 0usize;
                for c in word.chars() {
                    chunk.push(c);
                    count += 1;
                    if count == width {
                        out.push(std::mem::take(&mut chunk));
                        count = 0;
                    }
                }
                if !chunk.is_empty() {
                    current = chunk;
                    current_chars = count;
                }
                continue;
            }
            if current_chars + wlen > width {
                out.push(std::mem::take(&mut current));
                current_chars = 0;
                if word.starts_with(' ') {
                    continue;
                }
            }
            current.push_str(&word);
            current_chars += wlen;
        }
        out.push(current);
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

fn split_keep_spaces(line: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut in_space = false;
    for c in line.chars() {
        let is_space = c == ' ';
        if buf.is_empty() {
            in_space = is_space;
            buf.push(c);
        } else if is_space == in_space {
            buf.push(c);
        } else {
            tokens.push(std::mem::take(&mut buf));
            buf.push(c);
            in_space = is_space;
        }
    }
    if !buf.is_empty() {
        tokens.push(buf);
    }
    tokens
}

/// Convert cursor (char index in flat input) to (row, col) inside the wrapped view.
fn cursor_visual_pos(text: &str, cursor: usize, width: usize) -> (usize, usize) {
    if width == 0 {
        return (0, 0);
    }
    let mut row = 0usize;
    let col = 0usize;
    let mut consumed = 0usize;
    for raw_line in text.split('\n') {
        let line_chars = raw_line.chars().count();
        if cursor <= consumed + line_chars {
            let in_line = cursor - consumed;
            let lines = wrap_input_lines(raw_line, width);
            let mut left = in_line;
            for (i, l) in lines.iter().enumerate() {
                let lc = l.chars().count();
                if left <= lc {
                    return (row + i, left);
                }
                left -= lc;
            }
            return (row + lines.len().saturating_sub(1), 0);
        }
        let lines = wrap_input_lines(raw_line, width);
        row += lines.len();
        consumed += line_chars + 1; // +1 for the \n
    }
    (row, col)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markdown_table_renders_as_box() {
        let lines = [
            "| Источник | Что показывает |",
            "|----------|----------------|",
            "| **Официальный сайт** | биография, список книг |",
            "| Livelib | отзывы, цитаты |",
        ];

        let (rendered, consumed) = render_markdown_table(&lines, 0, 80).expect("table");
        let plain: Vec<String> = rendered
            .into_iter()
            .map(|line| {
                line.into_iter()
                    .map(|span| span.content.to_string())
                    .collect::<String>()
            })
            .collect();

        assert_eq!(consumed, 4);
        assert!(plain[0].starts_with("┌"));
        assert!(plain[1].contains("Источник"));
        assert!(plain[2].starts_with("├"));
        assert!(plain[3].contains("Официальный сайт"));
        assert!(plain[5].starts_with("└"));
    }

    #[test]
    fn shorten_path_handles_utf8_boundaries() {
        let path = "/Users/ampiro/проекты/CodeAgents/очень-длинная-папка";
        let shortened = shorten_path(path, 20);
        assert!(shortened.contains('…'));
        assert!(shortened.starts_with("/Users"));
    }

    #[test]
    fn truncate_str_handles_utf8_boundaries() {
        assert_eq!(truncate_str("модель qwen — тест", 8), "модель q…");
    }
}
