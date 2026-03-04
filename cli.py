from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
from xml.sax.saxutils import escape as xml_escape
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import which

import click

CCRC_DIR = Path.home() / ".ccrc"
SESSIONS_DIR = CCRC_DIR / "sessions"
LOGS_DIR = CCRC_DIR / "logs"
CONFIG_FILE = CCRC_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(config: dict) -> None:
    CCRC_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


# --- Session management ---


@dataclass
class Session:
    name: str
    pid: int
    directory: str
    started_at: float

    @property
    def file(self) -> Path:
        return SESSIONS_DIR / f"{self.name}.json"

    def save(self) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=SESSIONS_DIR, suffix=".tmp")
        closed = False
        try:
            os.write(fd, json.dumps(asdict(self)).encode())
            os.close(fd)
            closed = True
            os.replace(tmp, self.file)
        except BaseException:
            if not closed:
                os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def remove(self) -> None:
        self.file.unlink(missing_ok=True)

    def is_alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False


def load_session(path: Path) -> Session | None:
    try:
        data = json.loads(path.read_text())
        return Session(**data)
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        path.unlink(missing_ok=True)
        return None


def list_sessions(clean: bool = True) -> list[Session]:
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        s = load_session(f)
        if s is None:
            continue
        if not s.is_alive():
            if clean:
                s.remove()
            continue
        sessions.append(s)
    return sessions


def get_session(name: str) -> Session | None:
    path = SESSIONS_DIR / f"{name}.json"
    if not path.exists():
        return None
    s = load_session(path)
    if s and not s.is_alive():
        s.remove()
        return None
    return s


_VALID_NAME = re.compile(r"^[a-zA-Z0-9_\-.]+$")
_SESSION_URL_RE = re.compile(r"(https://\S+)")


def wait_for_session_url(session: Session, timeout: float = 30.0) -> str | None:
    """Poll the session log file for the remote control URL."""
    log_file = LOGS_DIR / f"{session.name}.log"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not session.is_alive():
            return None
        try:
            content = log_file.read_text()
        except OSError:
            pass
        else:
            m = _SESSION_URL_RE.search(content)
            if m:
                return m.group(1)
        time.sleep(0.5)
    return None


def start_session(directory: str, name: str | None = None, *, no_sandbox: bool = False) -> Session:
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        raise ValueError(f"Directory does not exist: {directory}")
    if which("claude") is None:
        raise ValueError("'claude' command not found in PATH")

    if name is None:
        from datetime import datetime

        base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", os.path.basename(directory))
        suffix = datetime.now().strftime("%m%d-%H%M%S")
        name = f"{base}-{suffix}"
    else:
        if not _VALID_NAME.match(name):
            raise ValueError(f"Invalid session name '{name}': only alphanumeric, '_', '-', '.' allowed")
        existing = get_session(name)
        if existing is not None:
            raise ValueError(f"Session '{name}' already exists (PID {existing.pid})")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{name}.log"

    cmd = ["claude", "remote-control", "--permission-mode", "bypassPermissions"]
    if no_sandbox:
        cmd.append("--no-sandbox")
    else:
        cmd.append("--sandbox")
        settings_file = Path(__file__).parent / "sandbox-settings.json"
        if settings_file.exists():
            cmd.extend(["--settings", str(settings_file)])

    with open(log_file, "w") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=directory,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    session = Session(
        name=name,
        pid=proc.pid,
        directory=directory,
        started_at=time.time(),
    )
    session.save()
    return session


def stop_session(session: Session, timeout: float = 5.0) -> None:
    if session.is_alive():
        try:
            os.kill(session.pid, signal.SIGTERM)
        except OSError:
            pass
        else:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and session.is_alive():
                time.sleep(0.1)
            if session.is_alive():
                try:
                    os.kill(session.pid, signal.SIGKILL)
                except OSError:
                    pass
    session.remove()


def stop_all_sessions() -> list[str]:
    stopped = []
    for s in list_sessions(clean=False):
        stop_session(s)
        stopped.append(s.name)
    return stopped


# --- CLI ---


def _format_uptime(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@click.group()
def cli() -> None:
    """Claude Remote Control session manager."""


@cli.command()
@click.argument("directory", default=".")
@click.option("--name", "-n", default=None, help="Session name (defaults to directory name)")
@click.option("--no-sandbox", is_flag=True, help="Disable sandbox (filesystem and network isolation)")
def new(directory: str, name: str | None, no_sandbox: bool) -> None:
    """Start a new remote-control session."""
    try:
        session = start_session(directory, name, no_sandbox=no_sandbox)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Started session '{session.name}' (PID {session.pid}) in {session.directory}")


@cli.command("list")
def list_cmd() -> None:
    """List active sessions."""
    sessions = list_sessions()
    if not sessions:
        click.echo("No active sessions.")
        return
    click.echo(f"{'NAME':<20} {'PID':<10} {'UPTIME':<15} {'DIRECTORY'}")
    for s in sessions:
        uptime = _format_uptime(time.time() - s.started_at)
        click.echo(f"{s.name:<20} {s.pid:<10} {uptime:<15} {s.directory}")


@cli.command()
@click.argument("name", required=False)
@click.option("--all", "stop_all", is_flag=True, help="Stop all sessions")
def stop(name: str | None, stop_all: bool) -> None:
    """Stop a session by name, interactively, or all."""
    if stop_all:
        stopped = stop_all_sessions()
        if stopped:
            click.echo(f"Stopped {len(stopped)} session(s): {', '.join(stopped)}")
        else:
            click.echo("No active sessions to stop.")
        return

    if name:
        session = get_session(name)
        if session is None:
            raise click.ClickException(f"No active session named '{name}'")
        stop_session(session)
        click.echo(f"Stopped session '{name}'")
        return

    # Interactive selection
    sessions = list_sessions()
    if not sessions:
        click.echo("No active sessions.")
        return

    import questionary

    choices = []
    for s in sessions:
        uptime = _format_uptime(time.time() - s.started_at)
        choices.append(questionary.Choice(title=f"{s.name}  (PID {s.pid}, up {uptime})", value=s.name))

    selected = questionary.select("Select session to stop:", choices=choices).ask()
    if selected is None:
        click.echo("Cancelled.")
        return
    session = next(s for s in sessions if s.name == selected)
    stop_session(session)
    click.echo(f"Stopped session '{session.name}'")


@cli.command()
def auth() -> None:
    """Get your Telegram chat ID by sending a one-time token to the bot."""
    import secrets

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise click.ClickException("TELEGRAM_BOT_TOKEN environment variable is required")

    auth_token = secrets.token_hex(8)
    click.echo(f"Send this token to your bot:\n\n  {auth_token}\n")
    click.echo("Waiting for authentication...")

    from bot import run_auth

    chat_id = run_auth(token, auth_token)
    if chat_id is not None:
        config = load_config()
        config["chat_id"] = chat_id
        save_config(config)
        click.echo(f"\nChat ID {chat_id} saved to {CONFIG_FILE}")
    else:
        click.echo("\nAuthentication cancelled.")


@cli.command()
def serve() -> None:
    """Run the Telegram bot service."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise click.ClickException("TELEGRAM_BOT_TOKEN environment variable is required")

    chat_id_str = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id_str:
        chat_id = int(chat_id_str)
    else:
        chat_id = load_config().get("chat_id")

    if chat_id is None:
        click.echo("Warning: No chat_id configured. Bot will respond to ALL chats.", err=True)
        click.echo("Run 'ccrc auth' to authenticate.", err=True)

    click.echo(f"Starting Telegram bot (chat_id restriction: {chat_id or 'none'})...")

    from bot import run_bot

    run_bot(token, chat_id)


# --- launchd service management ---

PLIST_LABEL = "com.ccrc.telegram-bot"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _generate_plist() -> str:
    ccrc_path = which("ccrc")
    if ccrc_path is None:
        raise click.ClickException("'ccrc' command not found in PATH")

    env_vars = {}
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CCRC_WORKSPACES"):
        val = os.environ.get(key)
        if val:
            env_vars[key] = val

    if "TELEGRAM_BOT_TOKEN" not in env_vars:
        raise click.ClickException("TELEGRAM_BOT_TOKEN environment variable is required")

    env_xml = ""
    if env_vars:
        env_entries = "\n".join(f"      <key>{xml_escape(k)}</key>\n      <string>{xml_escape(v)}</string>" for k, v in env_vars.items())
        env_xml = f"""    <key>EnvironmentVariables</key>
    <dict>
{env_entries}
    </dict>"""

    log_path = LOGS_DIR / "bot.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
      <string>{ccrc_path}</string>
      <string>serve</string>
    </array>
{env_xml}
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


@cli.command()
def install() -> None:
    """Install the Telegram bot as a launchd service."""
    if PLIST_PATH.exists():
        raise click.ClickException(f"Service already installed at {PLIST_PATH}. Run 'ccrc uninstall' first.")

    plist_content = _generate_plist()
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)

    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        PLIST_PATH.unlink(missing_ok=True)
        raise click.ClickException(f"Failed to load service: {result.stderr.strip()}")

    click.echo(f"Service installed and started.")
    click.echo(f"  Plist: {PLIST_PATH}")
    click.echo(f"  Logs:  {LOGS_DIR / 'bot.log'}")


@cli.command()
def uninstall() -> None:
    """Uninstall the Telegram bot launchd service."""
    if not PLIST_PATH.exists():
        raise click.ClickException("Service is not installed.")

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True, text=True)
    PLIST_PATH.unlink(missing_ok=True)
    click.echo("Service uninstalled.")
