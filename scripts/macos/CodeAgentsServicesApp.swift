import Cocoa

final class AppDelegate: NSObject, NSApplicationDelegate, NSTabViewDelegate {
    private let root: String
    private let statusLabel = NSTextField(labelWithString: "Checking…")
    private var overviewTV  = NSTextView()
    private var modelsTV    = NSTextView()
    private var profilingTV = NSTextView()
    private var logsTV      = NSTextView()
    private var activityTV  = NSTextView()
    private var window: NSWindow?
    private var timer: Timer?
    private var stopping = false
    private var refreshing = false
    private let serviceQueue = DispatchQueue(label: "codeagents.services", qos: .userInitiated)

    override init() {
        self.root = Bundle.main.object(forInfoDictionaryKey: "CodeAgentsRoot") as? String
            ?? FileManager.default.currentDirectoryPath
        super.init()
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        buildUI()
        log("App started")
        log("root: \(root)")
        log("binary: \(binaryPath)")
        refreshAll()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // Async-safe shutdown: tell macOS we'll terminate later, run stop in background.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if stopping { return .terminateNow }
        stopping = true
        timer?.invalidate()
        log("Stopping services…")
        serviceQueue.async { [weak self] in
            guard let self else { return }
            _ = self.runProcess(["stop"])
            DispatchQueue.main.async {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        // Hard fallback: if stop hangs, terminate after 5s anyway.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    private var binaryPath: String {
        Bundle.main.resourceURL?.appendingPathComponent("ca-services").path ?? "(missing)"
    }

    // MARK: - UI

    private func buildUI() {
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1020, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        w.title = "CodeAgents Services"; w.center()
        w.isReleasedWhenClosed = false; w.minSize = NSSize(width: 700, height: 500)

        let title    = makeLabel("CodeAgents Services", 22, .semibold)
        let subtitle = makeLabel("Manage Ollama + CodeAgents API. Closing quits all services.", 12, .regular, .secondaryLabelColor)
        let project  = makeLabel("Project: \(root)", 11, .regular, .tertiaryLabelColor)
        project.lineBreakMode = .byTruncatingMiddle
        statusLabel.font = NSFont.systemFont(ofSize: 14, weight: .medium)

        let sep = NSBox(); sep.boxType = .separator
        let btns = NSStackView(views: [
            makeButton("▶ Start All", #selector(doStart), primary: true),
            makeButton("■ Stop All",  #selector(doStop), danger: true),
            makeButton("↺ Restart",   #selector(doRestart)),
            makeButton("⟳ Refresh",   #selector(doRefresh)),
        ])
        btns.orientation = .horizontal; btns.spacing = 8

        let header = NSStackView(views: [title, subtitle, project, sep, statusLabel, btns])
        header.orientation = .vertical; header.spacing = 6; header.alignment = .leading
        header.setCustomSpacing(12, after: title)

        overviewTV  = makeTextView()
        modelsTV    = makeTextView()
        profilingTV = makeTextView()
        logsTV      = makeTextView()
        activityTV  = makeTextView()

        let tabs = NSTabView()
        tabs.delegate = self
        for (name, tv) in [
            ("Overview",  overviewTV),
            ("Models",    modelsTV),
            ("Profiling", profilingTV),
            ("Logs",      logsTV),
            ("Activity",  activityTV),
        ] {
            let sv = NSScrollView()
            sv.documentView = tv
            sv.hasVerticalScroller = true
            sv.autohidesScrollers = true
            sv.autoresizingMask = [.width, .height]

            let item = NSTabViewItem()
            item.label = name
            item.view = sv
            tabs.addTabViewItem(item)
        }

        let root = NSStackView(views: [header, tabs])
        root.orientation = .vertical; root.spacing = 14; root.alignment = .leading
        root.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 18, right: 18)
        root.translatesAutoresizingMaskIntoConstraints = false

        let content = NSView()
        content.addSubview(root)
        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: content.topAnchor),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            tabs.heightAnchor.constraint(greaterThanOrEqualToConstant: 460),
        ])

        w.contentView = content
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = w
    }

    private func makeTextView() -> NSTextView {
        let tv = NSTextView()
        tv.isEditable = false
        tv.isSelectable = true
        tv.isRichText = false
        tv.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        tv.textContainerInset = NSSize(width: 10, height: 10)
        tv.backgroundColor = .textBackgroundColor
        tv.textColor = .labelColor
        tv.isVerticallyResizable = true
        tv.isHorizontallyResizable = false
        tv.autoresizingMask = [.width]
        tv.textContainer?.widthTracksTextView = true
        tv.textContainer?.containerSize = NSSize(
            width: 0, height: CGFloat.greatestFiniteMagnitude)
        tv.minSize = NSSize(width: 0, height: 0)
        tv.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                            height: CGFloat.greatestFiniteMagnitude)
        return tv
    }

    private func makeLabel(_ text: String, _ size: CGFloat, _ weight: NSFont.Weight,
                           _ color: NSColor = .labelColor) -> NSTextField {
        let f = NSTextField(labelWithString: text)
        f.font = NSFont.systemFont(ofSize: size, weight: weight); f.textColor = color; return f
    }

    private func makeButton(_ title: String, _ action: Selector,
                            primary: Bool = false, danger: Bool = false) -> NSButton {
        let b = NSButton(title: title, target: self, action: action)
        b.bezelStyle = .rounded
        if primary { b.keyEquivalent = "\r" }
        if danger  { b.contentTintColor = .systemRed }
        return b
    }

    // MARK: - Tab delegate

    // NSTabView only gives a real frame to the *selected* tab; hidden tabs have
    // (0,0,0,0).  When content was set while a tab was hidden, the text container
    // has width 0 and layout is empty.  Invalidate layout when a tab appears.
    func tabView(_ tabView: NSTabView, didSelect tabViewItem: NSTabViewItem?) {
        guard let sv = tabViewItem?.view as? NSScrollView,
              let tv = sv.documentView as? NSTextView,
              let tc = tv.textContainer,
              let lm = tv.layoutManager else { return }
        let inset = tv.textContainerInset.width
        let w = sv.contentSize.width - inset * 2
        if w > 0 {
            tc.containerSize = NSSize(width: w, height: CGFloat.greatestFiniteMagnitude)
            let range = NSRange(location: 0, length: tv.textStorage?.length ?? 0)
            lm.invalidateLayout(forCharacterRange: range, actualCharacterRange: nil)
        }
        tv.needsDisplay = true
    }

    // MARK: - Helpers

    private func setContent(_ tv: NSTextView, _ text: String) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        tv.textStorage?.setAttributedString(NSAttributedString(string: text, attributes: attrs))
    }

    private func appendContent(_ tv: NSTextView, _ text: String) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]
        let sep = (tv.textStorage?.length ?? 0) > 0 ? "\n" : ""
        tv.textStorage?.append(NSAttributedString(string: sep + text, attributes: attrs))
        tv.scrollToEndOfDocument(nil)
    }

    private func log(_ msg: String) {
        let ts = { let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f.string(from: Date()) }()
        DispatchQueue.main.async { self.appendContent(self.activityTV, "[\(ts)] \(msg)") }
    }

    // MARK: - Actions

    @objc private func doStart()   { exec(["start"],   "Start") { [weak self] o in self?.log(o); self?.refreshAll() } }
    @objc private func doStop()    {
        // Don't gate stop on status refresh — let it run regardless.
        exec(["stop"], "Stop") { [weak self] o in
            self?.log(o)
            self?.refreshStatus()
        }
    }
    @objc private func doRestart() { exec(["restart"], "Restart") { [weak self] o in self?.log(o); self?.refreshAll() } }
    @objc private func doRefresh() { refreshAll() }

    // MARK: - Refresh

    private func refreshAll() {
        if refreshing { return }
        refreshing = true
        refreshStatus()
        refreshModels()
        refreshLogs()
        refreshChats()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.refreshing = false
        }
    }

    private func refreshStatus() {
        if stopping { return }
        exec(["status"], "Status", quiet: true) { [weak self] out in
            guard let self else { return }
            let olOK  = out.contains("ollama: running")
            let apiOK = out.contains("codeagents-api: running")
            switch (olOK, apiOK) {
            case (true, true):   self.statusLabel.stringValue = "● All services running";  self.statusLabel.textColor = .systemGreen
            case (true, false):  self.statusLabel.stringValue = "◐ Ollama up, API down";   self.statusLabel.textColor = .systemOrange
            case (false, true):  self.statusLabel.stringValue = "◐ API up, Ollama down";   self.statusLabel.textColor = .systemOrange
            case (false, false): self.statusLabel.stringValue = "○ All stopped";            self.statusLabel.textColor = .secondaryLabelColor
            }
            self.setContent(self.overviewTV, """
SERVICE MAP
───────────────────────────────────────
Ollama runtime   http://127.0.0.1:11434/v1
  Local model inference (GGUF weights)
  Owns: loaded model weights in unified memory

CodeAgents API   http://127.0.0.1:8765
  Chat/inference routing, request logging, model registry
  Owns: Python process, in-flight requests

Logs: \(self.root)/.codeagents/
  inference.jsonl        — structured inference
  runtime_requests.jsonl — requests to Ollama
  service_requests.jsonl — API requests handled

───────────────────────────────────────
STATUS
\(out)
""")
            self.setContent(self.profilingTV, """
MEMORY AND PROFILING
───────────────────────────────────────
Apple Silicon unified memory — no separate GPU pool.
RSS = total physical memory mapped by the process.
When Ollama loads a model, RSS grows to model size.
Stopping services frees that memory.

WHO USES WHAT
  ollama          → weights + KV-cache + inference
  codeagents-api  → HTTP server, logs (~50 MB idle)

───────────────────────────────────────
LIVE SNAPSHOT  (auto-refreshes every 6s)
\(out)
""")
        }
    }

    private func refreshModels() {
        exec(["models"], "Models", quiet: true) { [weak self] out in
            self?.setContent(self!.modelsTV, "MODELS\n───────────────────────────────────────\n\n\(out)")
        }
    }

    private func refreshLogs() {
        exec(["logs", "--limit", "120"], "Logs", quiet: true) { [weak self] out in
            self?.setContent(self!.logsTV, "LOGS\n───────────────────────────────────────\n\n\(out)")
        }
    }

    private func refreshChats() {
        exec(["chats"], "Chats", quiet: true) { [weak self] out in
            guard let self else { return }
            let registry = NSHomeDirectory() + "/.codeagents/chat_registry.jsonl"
            let recentLines = self.tailFile(registry, limit: 30)
            self.setContent(self.activityTV, """
\(out)

───────────────────────────────────────
RECENT ACTIVITY (last 30 events from global registry)
───────────────────────────────────────
\(recentLines)
""")
        }
    }

    private func tailFile(_ path: String, limit: Int) -> String {
        guard let data = FileManager.default.contents(atPath: path),
              let text = String(data: data, encoding: .utf8) else {
            return "(no file at \(path))"
        }
        let lines = text.components(separatedBy: .newlines).filter { !$0.isEmpty }
        let start = max(0, lines.count - limit)
        return lines[start...].joined(separator: "\n")
    }

    // MARK: - Process runner

    private func exec(_ args: [String], _ label: String,
                      quiet: Bool = false, done: @escaping (String) -> Void) {
        if !quiet { log("▶ \(label): ca-services \(args.joined(separator: " "))") }
        if !quiet {
            statusLabel.stringValue = "\(label)…"
            statusLabel.textColor = .secondaryLabelColor
        }
        serviceQueue.async { [weak self] in
            guard let self else { return }
            let out = self.runProcess(args)
            DispatchQueue.main.async {
                if out.contains("error") && !quiet { self.log("⚠ \(label): \(out)") }
                done(out)
            }
        }
    }

    private func runProcess(_ args: [String], timeout: TimeInterval = 8.0) -> String {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("ca-services"),
              FileManager.default.fileExists(atPath: url.path) else {
            return "error: ca-services binary missing from \(Bundle.main.resourcePath ?? "?")"
        }
        let p = Process(); p.executableURL = url
        p.arguments = ["--root", root] + args
        p.environment = [
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": NSHomeDirectory(),
        ]
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        do {
            try p.run()
        } catch {
            return "error: \(error)"
        }

        // Wait with timeout — kill if it hangs so refreshes don't pile up.
        let deadline = Date().addingTimeInterval(timeout)
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if p.isRunning {
            p.terminate()
            Thread.sleep(forTimeInterval: 0.2)
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            return "error: ca-services \(args.joined(separator: " ")) timed out after \(Int(timeout))s"
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "(no output)"
    }
}

func buildMainMenu() {
    let mainMenu = NSMenu()

    let appMenuItem = NSMenuItem()
    mainMenu.addItem(appMenuItem)
    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "About CodeAgents Services",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                    keyEquivalent: "")
    appMenu.addItem(.separator())
    let hideItem = NSMenuItem(title: "Hide CodeAgents Services",
                              action: #selector(NSApplication.hide(_:)),
                              keyEquivalent: "h")
    appMenu.addItem(hideItem)
    let hideOthers = NSMenuItem(title: "Hide Others",
                                action: #selector(NSApplication.hideOtherApplications(_:)),
                                keyEquivalent: "h")
    hideOthers.keyEquivalentModifierMask = [.command, .option]
    appMenu.addItem(hideOthers)
    appMenu.addItem(withTitle: "Show All",
                    action: #selector(NSApplication.unhideAllApplications(_:)),
                    keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "Quit CodeAgents Services",
                    action: #selector(NSApplication.terminate(_:)),
                    keyEquivalent: "q")
    appMenuItem.submenu = appMenu

    let windowMenuItem = NSMenuItem()
    mainMenu.addItem(windowMenuItem)
    let windowMenu = NSMenu(title: "Window")
    windowMenu.addItem(withTitle: "Minimize",
                       action: #selector(NSWindow.performMiniaturize(_:)),
                       keyEquivalent: "m")
    windowMenu.addItem(withTitle: "Zoom",
                       action: #selector(NSWindow.performZoom(_:)),
                       keyEquivalent: "")
    windowMenuItem.submenu = windowMenu

    NSApp.mainMenu = mainMenu
    NSApp.windowsMenu = windowMenu
}

let app = NSApplication.shared
let d = AppDelegate()
app.delegate = d
app.setActivationPolicy(.regular)
buildMainMenu()
app.run()
