import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

interface PendingEditMeta {
    id: string;
    path: string;
    absolute_path: string;
    original: string;
    proposed: string;
    diff?: string;
    edits_applied?: number;
    created_at?: number;
    tool?: string;
}

const PENDING_DIRNAME = path.join(".codeagents", "pending_edits");

let statusBar: vscode.StatusBarItem | undefined;
const knownEdits = new Map<string, PendingEditMeta>();
let latestEditId: string | undefined;

export function activate(context: vscode.ExtensionContext) {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
        return;
    }

    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBar.command = "codeagents.showLatestEdit";
    context.subscriptions.push(statusBar);

    for (const folder of folders) {
        registerFolder(context, folder);
    }

    context.subscriptions.push(
        vscode.commands.registerCommand("codeagents.acceptEdit", async (editId?: string) => {
            const id = editId ?? latestEditId;
            if (!id) {
                vscode.window.showWarningMessage("No pending CodeAgents edit to accept.");
                return;
            }
            await acceptEdit(id);
        }),
        vscode.commands.registerCommand("codeagents.rejectEdit", async (editId?: string) => {
            const id = editId ?? latestEditId;
            if (!id) {
                vscode.window.showWarningMessage("No pending CodeAgents edit to reject.");
                return;
            }
            await rejectEdit(id);
        }),
        vscode.commands.registerCommand("codeagents.showLatestEdit", async () => {
            const id = latestEditId;
            if (!id) {
                vscode.window.showInformationMessage("No pending CodeAgents edits.");
                return;
            }
            const meta = knownEdits.get(id);
            if (meta) {
                await showDiff(meta);
            }
        })
    );

    refreshStatus();
}

export function deactivate() {
    statusBar?.dispose();
}

function registerFolder(
    context: vscode.ExtensionContext,
    folder: vscode.WorkspaceFolder
) {
    const pendingDir = path.join(folder.uri.fsPath, PENDING_DIRNAME);

    // Ensure the directory exists so the watcher fires reliably.
    try {
        fs.mkdirSync(pendingDir, { recursive: true });
    } catch (err) {
        console.error("CodeAgents: failed to create pending dir", err);
    }

    // Pick up edits that already exist when VS Code starts.
    try {
        for (const name of fs.readdirSync(pendingDir)) {
            if (name.endsWith(".json")) {
                handleNewMeta(path.join(pendingDir, name), { silent: true });
            }
        }
    } catch (err) {
        console.error("CodeAgents: failed to scan pending dir", err);
    }

    const pattern = new vscode.RelativePattern(folder, `${PENDING_DIRNAME}/*.json`);
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    context.subscriptions.push(watcher);

    watcher.onDidCreate((uri) => handleNewMeta(uri.fsPath));
    watcher.onDidChange((uri) => handleNewMeta(uri.fsPath));
    watcher.onDidDelete((uri) => {
        const id = path.basename(uri.fsPath, ".json");
        knownEdits.delete(id);
        if (latestEditId === id) {
            latestEditId = undefined;
        }
        refreshStatus();
    });
}

function handleNewMeta(metaPath: string, opts: { silent?: boolean } = {}) {
    let meta: PendingEditMeta;
    try {
        const raw = fs.readFileSync(metaPath, "utf8");
        meta = JSON.parse(raw) as PendingEditMeta;
    } catch (err) {
        // The agent may still be writing the file. Try once more shortly.
        setTimeout(() => {
            try {
                const raw = fs.readFileSync(metaPath, "utf8");
                handleParsedMeta(JSON.parse(raw) as PendingEditMeta, opts);
            } catch {
                /* give up silently */
            }
        }, 150);
        return;
    }
    handleParsedMeta(meta, opts);
}

function handleParsedMeta(
    meta: PendingEditMeta,
    opts: { silent?: boolean } = {}
) {
    if (!meta?.id) {
        return;
    }
    const isNew = !knownEdits.has(meta.id);
    knownEdits.set(meta.id, meta);
    latestEditId = meta.id;
    refreshStatus();

    if (isNew && !opts.silent) {
        const auto = vscode.workspace
            .getConfiguration("codeagents")
            .get<boolean>("autoOpenDiff", true);
        if (auto) {
            void showDiff(meta);
        } else {
            void notifyNewEdit(meta);
        }
    }
}

async function notifyNewEdit(meta: PendingEditMeta) {
    const choice = await vscode.window.showInformationMessage(
        `CodeAgents proposed an edit to ${meta.path}`,
        "Show diff",
        "Accept",
        "Reject"
    );
    if (choice === "Show diff") {
        await showDiff(meta);
    } else if (choice === "Accept") {
        await acceptEdit(meta.id);
    } else if (choice === "Reject") {
        await rejectEdit(meta.id);
    }
}

async function showDiff(meta: PendingEditMeta) {
    const originalUri = vscode.Uri.file(meta.original);
    const proposedUri = vscode.Uri.file(meta.proposed);
    const title = `CodeAgents: ${meta.path}  (proposed)`;
    try {
        await vscode.commands.executeCommand(
            "vscode.diff",
            originalUri,
            proposedUri,
            title,
            { preview: true }
        );
    } catch (err) {
        vscode.window.showErrorMessage(`CodeAgents: failed to open diff (${err})`);
        return;
    }

    // Offer accept/reject right after showing the diff.
    const choice = await vscode.window.showInformationMessage(
        `Apply edit to ${meta.path}? (${meta.edits_applied ?? 1} change${meta.edits_applied === 1 ? "" : "s"})`,
        { modal: false },
        "Accept",
        "Reject"
    );
    if (choice === "Accept") {
        await acceptEdit(meta.id);
    } else if (choice === "Reject") {
        await rejectEdit(meta.id);
    }
}

async function acceptEdit(editId: string) {
    const meta = knownEdits.get(editId);
    if (!meta) {
        vscode.window.showWarningMessage(`Unknown CodeAgents edit ${editId}`);
        return;
    }
    try {
        // The agent already applied the edit atomically; "accept" is a marker
        // so the agent / TUI can know the user approved.
        const proposed = fs.readFileSync(meta.proposed, "utf8");
        fs.writeFileSync(meta.absolute_path, proposed, "utf8");
        writeMarker(meta, "accepted");
        cleanupPending(meta);
        vscode.window.setStatusBarMessage(`CodeAgents: accepted ${meta.path}`, 4000);
    } catch (err) {
        vscode.window.showErrorMessage(`CodeAgents: failed to accept edit (${err})`);
        return;
    }
    knownEdits.delete(editId);
    if (latestEditId === editId) {
        latestEditId = undefined;
    }
    refreshStatus();
}

async function rejectEdit(editId: string) {
    const meta = knownEdits.get(editId);
    if (!meta) {
        vscode.window.showWarningMessage(`Unknown CodeAgents edit ${editId}`);
        return;
    }
    try {
        // Restore the original content (the agent had already applied the edit).
        const original = fs.readFileSync(meta.original, "utf8");
        fs.writeFileSync(meta.absolute_path, original, "utf8");
        writeMarker(meta, "rejected");
        cleanupPending(meta);
        vscode.window.setStatusBarMessage(`CodeAgents: reverted ${meta.path}`, 4000);
    } catch (err) {
        vscode.window.showErrorMessage(`CodeAgents: failed to reject edit (${err})`);
        return;
    }
    knownEdits.delete(editId);
    if (latestEditId === editId) {
        latestEditId = undefined;
    }
    refreshStatus();
}

function writeMarker(meta: PendingEditMeta, kind: "accepted" | "rejected") {
    const dir = path.dirname(meta.proposed);
    const markerPath = path.join(dir, `${meta.id}.${kind}`);
    try {
        fs.writeFileSync(
            markerPath,
            JSON.stringify(
                {
                    id: meta.id,
                    path: meta.path,
                    decision: kind,
                    decided_at: Date.now() / 1000,
                },
                null,
                2
            ),
            "utf8"
        );
    } catch (err) {
        console.error("CodeAgents: failed to write marker", err);
    }
}

function cleanupPending(meta: PendingEditMeta) {
    for (const filePath of [
        meta.proposed,
        meta.original,
        path.join(path.dirname(meta.proposed), `${meta.id}.json`),
    ]) {
        try {
            fs.unlinkSync(filePath);
        } catch {
            /* ignore — best-effort cleanup */
        }
    }
}

function refreshStatus() {
    if (!statusBar) {
        return;
    }
    const count = knownEdits.size;
    if (count === 0) {
        statusBar.hide();
        return;
    }
    statusBar.text = `$(diff) CodeAgents: ${count} pending`;
    statusBar.tooltip = "Click to view the latest CodeAgents edit";
    statusBar.show();
}
