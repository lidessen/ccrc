# ccrc

Claude Remote Control session manager — a CLI tool for managing `claude remote-control` sessions.

## Install

```bash
uv tool install .
```

## Usage

```bash
# Start a new session in the current directory
ccrc new

# Start a session in a specific directory with a custom name
ccrc new ~/projects/myapp --name myapp

# List active sessions
ccrc list

# Stop a session by name
ccrc stop myapp

# Stop a session interactively
ccrc stop

# Stop all sessions
ccrc stop --all
```

## Requirements

- Python >= 3.14
- `claude` CLI installed and available in PATH
