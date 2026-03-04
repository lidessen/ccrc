# ccrc

Claude Remote Control session manager — a CLI and Telegram bot for managing `claude remote-control` sessions.

## Install

```bash
uv tool install -e .
```

## CLI Usage

```bash
# Start a new session (sandbox mode, max permissions by default)
ccrc new

# Start a session in a specific directory with a custom name
ccrc new ~/projects/myapp --name myapp

# Start a session without sandbox
ccrc new --no-sandbox

# List active sessions
ccrc list

# Stop a session by name
ccrc stop myapp

# Stop a session interactively
ccrc stop

# Stop all sessions
ccrc stop --all
```

## Telegram Bot

Control sessions from Telegram with `/new`, `/list`, `/stop`, `/help`.

### Setup

```bash
# Set your bot token
export TELEGRAM_BOT_TOKEN="your-token-here"

# Authenticate to restrict the bot to your chat
ccrc auth

# Install as a launchd service (macOS)
ccrc install

# Uninstall the service
ccrc uninstall

# Or run directly
ccrc serve
```

### Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Required. Telegram bot API token |
| `TELEGRAM_CHAT_ID` | Optional. Restrict bot to a specific chat (overrides config) |
| `CCRC_WORKSPACES` | Optional. Root directory for workspaces (default: `~/workspaces`) |
| `NO_PROXY` | Set to `*` if behind a proxy that interferes with Telegram API |

## Requirements

- Python >= 3.12
- `claude` CLI installed and available in PATH
