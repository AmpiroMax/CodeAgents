# CodeAgents Diff

VS Code / Cursor extension that shows native diffs for edits made by the
CodeAgents agent (`edit_file` tool) and lets you Accept or Reject each edit
without leaving the editor.

## How it works

The agent writes proposed edits as a triple of files into
`.codeagents/pending_edits/`:

- `<id>.original`   — snapshot of the file before the edit
- `<id>.proposed`   — the file content after the edit
- `<id>.json`       — metadata (relative path, absolute path, unified diff, …)

The agent applies the change to disk atomically right away (via
`os.replace`), so the working tree is always consistent. The pending files
just give the IDE a chance to:

1. Open `vscode.diff(original, proposed)` to visualize the change.
2. Confirm via the "Accept" / "Reject" buttons in the notification.
3. On reject, restore the original content from the snapshot.
4. Drop an `<id>.accepted` or `<id>.rejected` marker file so the agent can
   observe the user's decision.

## Build

```bash
cd extensions/codeagents-diff
npm install
npm run compile
```

Then point Cursor / VS Code at this folder via `Developer: Install Extension
from Location`, or symlink it into `~/.vscode/extensions/codeagents-diff/`.

## Settings

- `codeagents.autoOpenDiff` (default `true`) — open the diff view immediately
  when a new pending edit appears. If `false`, only show a notification with
  Accept / Reject / Show diff buttons.

## Commands

- `CodeAgents: Show Latest Pending Edit`
- `CodeAgents: Accept Edit`
- `CodeAgents: Reject Edit`
