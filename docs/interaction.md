# Interaction Model

## Interfaces

The MVP has two user-facing interfaces.

`CLI/TUI`
: Main interface for coding work inside a selected workspace. It should support commands like `ask`, `code`, `plan`, `apply`, `test`, `tools`, and `index`.

`Local Chat/API`
: Local HTTP/WebSocket interface for the general assistant and for scripts. This allows a simple chat UI later without changing the agent core.

## Workspace Access

The coding agent always runs against an explicit workspace root. It should not silently read or write outside that root unless the user grants an additional path.

Default mode:

- Read files inside the workspace.
- Search and index the workspace.
- Propose patches.
- Run safe allowlisted commands.
- Ask for confirmation before writing.

## Permission Modes

`read-only`
: The agent can search, index, and read files. It cannot write files or run commands that modify state.

`propose`
: The agent can prepare a patch/diff and explain it, but a user confirmation is required before applying it.

`workspace-write`
: The agent can create and edit files inside the allowed workspace.

`shell-safe`
: The agent can run allowlisted commands such as `rg`, `python -m compileall`, `pytest`, `cargo test`, `git diff`, and read-only diagnostics.

`shell-dangerous`
: Commands that delete files, mutate git history, install packages, access network services, or run unknown scripts require explicit confirmation.

## File Creation And Editing

The agent can create and edit files when either:

- The user explicitly asks it to implement something and confirms the write step.
- A project policy enables `workspace-write` for the current workspace.

All writes should be represented as patches or structured file operations and recorded in the audit log.

## Audit Log

Every tool call should store:

- Timestamp.
- Session id.
- Tool name.
- Permission class.
- Arguments.
- Result summary.
- Whether user confirmation was required.

This makes the agent debuggable and gives the user a clear history of what happened.
