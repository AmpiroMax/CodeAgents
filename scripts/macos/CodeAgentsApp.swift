import Cocoa
import WebKit

/// Dedicated, *opaque* titlebar strip that lives above the WKWebView (not
/// on top of it). Putting it in its own row makes the drag region a plain
/// NSView region that AppKit can move the window from — overlaying the
/// WebView never worked reliably because WKWebView's hosting layer eats
/// mouse-down events before ``mouseDownCanMoveWindow`` is consulted.
final class TitleBarStripView: NSView {
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { true }
    /// Cosmetic only — the background colour. Drawn here (instead of via
    /// CSS) so the strip is a real, independent view the window manager can
    /// drag from.
    var fillColor: NSColor = NSColor(srgbRed: 0.094, green: 0.094, blue: 0.094, alpha: 1.0)
    var borderColor: NSColor = NSColor(srgbRed: 0.165, green: 0.165, blue: 0.165, alpha: 1.0)

    override func draw(_ dirtyRect: NSRect) {
        fillColor.setFill()
        bounds.fill()
        borderColor.setFill()
        NSRect(x: 0, y: bounds.height - 1, width: bounds.width, height: 1).fill()
    }

    override func mouseDown(with event: NSEvent) {
        // Manual fallback for older macOS versions where
        // mouseDownCanMoveWindow alone isn't honoured for non-titled-bar
        // subviews under fullSizeContentView windows.
        window?.performDrag(with: event)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private let codeagentsRoot: String
    private let webView: WKWebView = {
        let config = WKWebViewConfiguration()
        return WKWebView(frame: .zero, configuration: config)
    }()
    private var window: NSWindow?
    private var stopping = false
    private let serviceQueue = DispatchQueue(label: "codeagents.launcher", qos: .userInitiated)

    override init() {
        self.codeagentsRoot = Bundle.main.object(forInfoDictionaryKey: "CodeAgentsRoot") as? String
            ?? FileManager.default.currentDirectoryPath
        super.init()
    }

    // Workspace defaults to ~/CodeAgents (auto-created). We deliberately
    // avoid $HOME so the agent never wanders into ~/Documents, ~/Desktop or
    // ~/Downloads — those directories are TCC-protected and trigger a wall
    // of "App wants to access ..." prompts on macOS.
    private var workspacePath: String {
        if let s = UserDefaults.standard.string(forKey: "workspacePath"), !s.isEmpty {
            return s
        }
        let dir = NSHomeDirectory() + "/CodeAgents"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        return dir
    }

    func applicationDidFinishLaunching(_: Notification) {
        buildUI()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.exec(["start"]) { [weak self] _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                    self?.loadChatUI()
                }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_: NSApplication) -> NSApplication.TerminateReply {
        if stopping { return .terminateNow }
        stopping = true
        // Best-effort graceful service stop, but never block the user. After
        // ~4s we hard-exit so a stuck Ollama / Python child can never keep
        // the launcher alive.
        serviceQueue.async { [weak self] in
            guard let self else { return }
            _ = self.runProcess(["stop"], timeout: 3.0)
            DispatchQueue.main.async {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            // Reply *and* exit() so we're not at the mercy of any pending
            // run-loop modal (palette / exit dialog / WKWebView popups) that
            // could otherwise hold up the AppKit terminate machinery.
            NSApp.reply(toApplicationShouldTerminate: true)
            exit(0)
        }
        return .terminateLater
    }

    private func buildUI() {
        // ``fullSizeContentView`` lets the WebKit chat extend behind the
        // title bar so traffic-light buttons sit on top of the in-page
        // header. The header CSS reserves left padding (88px) so titles
        // and buttons never collide with the macOS controls. The title
        // bar itself stays draggable thanks to AppKit, and the webpage
        // additionally exposes ``-webkit-app-region: drag`` regions.
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        w.title = "CodeAgents"
        w.center()
        w.isReleasedWhenClosed = false
        w.minSize = NSSize(width: 720, height: 520)
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.styleMask.insert(.titled)
        w.isMovableByWindowBackground = true

        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.uiDelegate = self
        // JS bridge: window.webkit.messageHandlers.codeagents.postMessage(...)
        // The Esc → Exit dialog in the GUI sends {action: "quit"} here;
        // we close the window which then triggers graceful shutdown.
        webView.configuration.userContentController.add(self, name: "codeagents")

        let content = NSView()
        // Titlebar strip lives in its own row above the WebView — NOT on top
        // of it. This is the only configuration where dragging is reliable,
        // because the strip occupies space the WebView never sees.
        let titleStrip = TitleBarStripView()
        titleStrip.translatesAutoresizingMaskIntoConstraints = false
        titleStrip.wantsLayer = true
        content.addSubview(titleStrip)
        content.addSubview(webView)

        NSLayoutConstraint.activate([
            titleStrip.topAnchor.constraint(equalTo: content.topAnchor),
            titleStrip.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            titleStrip.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            titleStrip.heightAnchor.constraint(equalToConstant: 28),

            webView.topAnchor.constraint(equalTo: titleStrip.bottomAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
        ])

        w.contentView = content
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = w
    }

    private func loadChatUI() {
        guard let url = URL(string: "http://127.0.0.1:8765/ui/") else { return }
        webView.load(URLRequest(url: url))
    }

    // The web GUI calls window.close() from its Esc → Exit dialog. By
    // default WKWebView ignores window.close(); implementing this delegate
    // method tears the window down, which (combined with
    // applicationShouldTerminateAfterLastWindowClosed = true) makes the
    // launcher run its `ca-services stop` cleanup and quit the app.
    func webViewDidClose(_ webView: WKWebView) {
        DispatchQueue.main.async { [weak self] in
            self?.window?.performClose(nil)
        }
    }

    // JS bridge entrypoint. Currently handles a single "quit" action; new
    // commands can be added by branching on the message body.
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == "codeagents" else { return }
        let action: String
        if let dict = message.body as? [String: Any], let a = dict["action"] as? String {
            action = a
        } else if let raw = message.body as? String {
            action = raw
        } else {
            return
        }
        switch action {
        case "quit":
            // We deliberately bypass NSApp.terminate(_:) here. Going through
            // AppKit's terminate path was unreliable: WKWebView would tear
            // down the JS context (the GUI shows "Load failed"), the window
            // would be re-created by the run loop, and the app stayed alive
            // until force-quit. Instead we shut services down on a worker
            // thread and call exit() — that always kills our process.
            stopping = true
            serviceQueue.async { [weak self] in
                _ = self?.runProcess(["stop"], timeout: 2.5)
                DispatchQueue.main.async { exit(0) }
            }
            // Hard-cap: even if `ca-services stop` hangs, never leave the
            // launcher alive longer than ~3s after the user said yes.
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { exit(0) }
        default:
            break
        }
    }

    // WKWebView swallows <input type="file"> by default. Implementing this
    // delegate method shows a real NSOpenPanel so the GUI's "Image" button
    // actually works inside the bundled app.
    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.allowedFileTypes = ["png", "jpg", "jpeg", "gif", "webp", "heic", "bmp"]
        panel.begin { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    @objc fileprivate func doStart() {
        exec(["start"]) { [weak self] _ in self?.loadChatUI() }
    }

    @objc fileprivate func doStop() {
        exec(["stop"]) { _ in }
    }

    @objc fileprivate func doRestart() {
        exec(["restart"]) { [weak self] _ in self?.loadChatUI() }
    }

    @objc fileprivate func doRefresh() {
        loadChatUI()
    }

    @objc fileprivate func chooseWorkspace() {
        let p = NSOpenPanel()
        p.canChooseFiles = false
        p.canChooseDirectories = true
        p.allowsMultipleSelection = false
        p.directoryURL = URL(fileURLWithPath: workspacePath)
        if p.runModal() == .OK, let url = p.url {
            UserDefaults.standard.set(url.path, forKey: "workspacePath")
            saveLauncherToml(url.path)
            exec(["restart"]) { [weak self] _ in self?.loadChatUI() }
        }
    }

    private func saveLauncherToml(_ path: String) {
        let dir = NSHomeDirectory() + "/.codeagents"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let body = "workspace = \"\(path.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\""))\"\n"
        let fp = dir + "/launcher.toml"
        try? body.write(toFile: fp, atomically: true, encoding: .utf8)
    }

    private func exec(_ args: [String], done: @escaping (String) -> Void) {
        serviceQueue.async { [weak self] in
            guard let self else { return }
            let out = self.runProcess(args)
            DispatchQueue.main.async { done(out) }
        }
    }

    private func runProcess(_ args: [String], timeout: TimeInterval = 12.0) -> String {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("ca-services"),
              FileManager.default.fileExists(atPath: url.path) else {
            return "error: ca-services binary not found"
        }
        let p = Process()
        p.executableURL = url
        var full = ["--root", codeagentsRoot, "--workspace", workspacePath]
        if let gui = Bundle.main.resourceURL?.appendingPathComponent("gui").path,
           FileManager.default.fileExists(atPath: gui + "/index.html") {
            full.append(contentsOf: ["--gui-dir", gui])
        }
        full.append(contentsOf: args)
        p.arguments = full
        p.environment = [
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": NSHomeDirectory(),
        ]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do {
            try p.run()
        } catch {
            return "error: \(error)"
        }
        let deadline = Date().addingTimeInterval(timeout)
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if p.isRunning {
            p.terminate()
            Thread.sleep(forTimeInterval: 0.2)
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            return "error: timeout \(Int(timeout))s"
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "(no output)"
    }
}

func buildMainMenu(delegate: AppDelegate) {
    let mainMenu = NSMenu()

    let appMenuItem = NSMenuItem()
    mainMenu.addItem(appMenuItem)
    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "About CodeAgents",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                    keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "Hide CodeAgents",
                    action: #selector(NSApplication.hide(_:)),
                    keyEquivalent: "h")
    let hideOthers = NSMenuItem(title: "Hide Others",
                                action: #selector(NSApplication.hideOtherApplications(_:)),
                                keyEquivalent: "h")
    hideOthers.keyEquivalentModifierMask = [.command, .option]
    appMenu.addItem(hideOthers)
    appMenu.addItem(withTitle: "Show All",
                    action: #selector(NSApplication.unhideAllApplications(_:)),
                    keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "Quit CodeAgents",
                    action: #selector(NSApplication.terminate(_:)),
                    keyEquivalent: "q")
    appMenuItem.submenu = appMenu

    // Hidden Services submenu: kept off the visible chat surface but
    // available for power users that need to restart the daemons.
    let servicesItem = NSMenuItem()
    mainMenu.addItem(servicesItem)
    let servicesMenu = NSMenu(title: "Services")
    let startItem = NSMenuItem(title: "Start services",
                               action: #selector(AppDelegate.doStart),
                               keyEquivalent: "")
    startItem.target = delegate
    servicesMenu.addItem(startItem)
    let stopItem = NSMenuItem(title: "Stop services",
                              action: #selector(AppDelegate.doStop),
                              keyEquivalent: "")
    stopItem.target = delegate
    servicesMenu.addItem(stopItem)
    let restartItem = NSMenuItem(title: "Restart services",
                                 action: #selector(AppDelegate.doRestart),
                                 keyEquivalent: "r")
    restartItem.keyEquivalentModifierMask = [.command, .shift]
    restartItem.target = delegate
    servicesMenu.addItem(restartItem)
    let reloadItem = NSMenuItem(title: "Reload chat UI",
                                action: #selector(AppDelegate.doRefresh),
                                keyEquivalent: "r")
    reloadItem.target = delegate
    servicesMenu.addItem(reloadItem)
    servicesMenu.addItem(.separator())
    let workspaceItem = NSMenuItem(title: "Pin workspace folder…",
                                   action: #selector(AppDelegate.chooseWorkspace),
                                   keyEquivalent: "")
    workspaceItem.target = delegate
    servicesMenu.addItem(workspaceItem)
    servicesItem.submenu = servicesMenu

    // Standard Edit menu — without it, macOS doesn't route ⌘C/⌘V/⌘X/⌘A to
    // text inputs (including WKWebView's textareas), so users can't paste
    // into the composer or copy text out of the chat. Wiring the canonical
    // first-responder selectors restores expected behaviour.
    let editItem = NSMenuItem()
    mainMenu.addItem(editItem)
    let editMenu = NSMenu(title: "Edit")
    editMenu.addItem(withTitle: "Undo",
                     action: Selector(("undo:")),
                     keyEquivalent: "z")
    let redo = NSMenuItem(title: "Redo",
                          action: Selector(("redo:")),
                          keyEquivalent: "z")
    redo.keyEquivalentModifierMask = [.command, .shift]
    editMenu.addItem(redo)
    editMenu.addItem(.separator())
    editMenu.addItem(withTitle: "Cut",
                     action: #selector(NSText.cut(_:)),
                     keyEquivalent: "x")
    editMenu.addItem(withTitle: "Copy",
                     action: #selector(NSText.copy(_:)),
                     keyEquivalent: "c")
    editMenu.addItem(withTitle: "Paste",
                     action: #selector(NSText.paste(_:)),
                     keyEquivalent: "v")
    let pasteAndMatch = NSMenuItem(title: "Paste and Match Style",
                                   action: Selector(("pasteAsPlainText:")),
                                   keyEquivalent: "v")
    pasteAndMatch.keyEquivalentModifierMask = [.command, .option, .shift]
    editMenu.addItem(pasteAndMatch)
    editMenu.addItem(withTitle: "Delete",
                     action: #selector(NSText.delete(_:)),
                     keyEquivalent: "")
    editMenu.addItem(withTitle: "Select All",
                     action: #selector(NSText.selectAll(_:)),
                     keyEquivalent: "a")
    editItem.submenu = editMenu

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
buildMainMenu(delegate: d)
app.run()
