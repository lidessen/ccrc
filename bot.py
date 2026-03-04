from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cli import (
    _format_uptime,
    get_session,
    list_sessions,
    start_session,
    stop_session,
    wait_for_session_url,
)

ALLOWED_CHAT_ID: int | None = None


def _authorized(update: Update) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return update.effective_chat is not None and update.effective_chat.id == ALLOWED_CHAT_ID


# --- /list ---


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    sessions = list_sessions()
    if not sessions:
        await update.message.reply_text("No active sessions.")
        return
    lines = []
    for s in sessions:
        uptime = _format_uptime(time.time() - s.started_at)
        lines.append(f"*{s.name}*  PID {s.pid}  up {uptime}\n`{s.directory}`")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


# --- /new ---


def _get_workspaces() -> tuple[Path, list[str]]:
    root = Path(os.environ.get("CCRC_WORKSPACES", str(Path.home() / "workspaces")))
    if not root.is_dir():
        return root, []
    dirs = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    return root, dirs


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    root, dirs = _get_workspaces()
    if not dirs:
        await update.message.reply_text(f"No directories found in `{root}`", parse_mode="Markdown")
        return
    buttons = [[InlineKeyboardButton(name, callback_data=f"new:{name}")] for name in dirs]
    await update.message.reply_text(
        "Pick a workspace:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cb_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update):
        return
    dir_name = query.data.removeprefix("new:")
    root, _ = _get_workspaces()
    full_path = str(root / dir_name)
    try:
        session = start_session(full_path)
        await query.edit_message_text(
            f"Starting *{session.name}*...\n`{session.directory}`",
            parse_mode="Markdown",
        )
        url = await asyncio.get_event_loop().run_in_executor(
            None, wait_for_session_url, session,
        )
        if url:
            await query.edit_message_text(
                f"*{session.name}* ready\n\n{url}",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"*{session.name}* started (PID {session.pid}) but URL not available.\n`{session.directory}`",
                parse_mode="Markdown",
            )
    except ValueError as e:
        await query.edit_message_text(f"Error: {e}")


# --- /stop ---


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    sessions = list_sessions()
    if not sessions:
        await update.message.reply_text("No active sessions to stop.")
        return
    buttons = []
    for s in sessions:
        uptime = _format_uptime(time.time() - s.started_at)
        label = f"{s.name} (up {uptime})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"stop:{s.name}")])
    await update.message.reply_text(
        "Pick a session to stop:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cb_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update):
        return
    name = query.data.removeprefix("stop:")
    session = get_session(name)
    if session is None:
        await query.edit_message_text(f"Session '{name}' no longer exists.")
        return
    stop_session(session)
    await query.edit_message_text(f"Stopped *{name}*.", parse_mode="Markdown")


# --- Auth mode ---


async def _handle_auth_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    expected_token = context.bot_data.get("auth_token")
    if expected_token is None:
        return
    text = (update.message.text or "").strip()
    if text == expected_token:
        chat_id = update.effective_chat.id
        await update.message.reply_text("Authenticated!")
        context.bot_data["auth_chat_id"] = chat_id
        context.application.stop_running()
    else:
        await update.message.reply_text("Invalid token.")


def run_auth(token: str, auth_token: str) -> int | None:
    """Run bot in auth mode. Returns the chat ID on success."""
    app = Application.builder().token(token).build()
    app.bot_data["auth_token"] = auth_token
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_auth_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return app.bot_data.get("auth_chat_id")


# --- Entry point ---


def run_bot(token: str, chat_id: int | None = None) -> None:
    global ALLOWED_CHAT_ID
    ALLOWED_CHAT_ID = chat_id

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(cb_new, pattern=r"^new:"))
    app.add_handler(CallbackQueryHandler(cb_stop, pattern=r"^stop:"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)
