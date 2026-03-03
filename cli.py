from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import which

import click

SESSIONS_DIR = Path.home() / ".ccrc" / "sessions"
LOGS_DIR = Path.home() / ".ccrc" / "logs"


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


def start_session(directory: str, name: str | None = None) -> Session:
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        raise ValueError(f"Directory does not exist: {directory}")
    if which("claude") is None:
        raise ValueError("'claude' command not found in PATH")

    if name is None:
        base = re.sub(r"[^a-zA-Z0-9_\-.]", "_", os.path.basename(directory))
        name = base
        n = 2
        while get_session(name) is not None:
            name = f"{base}-{n}"
            n += 1
    else:
        if not _VALID_NAME.match(name):
            raise ValueError(f"Invalid session name '{name}': only alphanumeric, '_', '-', '.' allowed")
        existing = get_session(name)
        if existing is not None:
            raise ValueError(f"Session '{name}' already exists (PID {existing.pid})")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{name}.log"

    with open(log_file, "a") as log:
        proc = subprocess.Popen(
            ["claude", "remote-control", "--no-sandbox"],
            cwd=directory,
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
def new(directory: str, name: str | None) -> None:
    """Start a new remote-control session."""
    try:
        session = start_session(directory, name)
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
