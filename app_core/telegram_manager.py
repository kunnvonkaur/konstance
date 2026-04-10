"""
telegram_manager.py - Telegram bot integration for Konstance the Watchdog
Runs a python-telegram-bot v21+ instance in its own thread with a private
asyncio loop so the Tk UI stays responsive. Persists config to
telegram_config.json inside the user data dir.

Config schema (telegram_config.json):
{
    "token": "123456:ABC...",
    "authorized_chat_ids": [123456789, 987654321],
    "debounce_seconds": 60,
    "auto_start": true
}

Public API used by main.py:
    tm = TelegramManager(user_dir, log_callback, app_ref)
    tm.load_config()
    tm.start()                      # non-blocking, safe if no token
    tm.stop()                       # safe to call anytime
    tm.is_running                   # bool property
    tm.send_alert(frame_bgr, caption)   # thread-safe, debounced
    tm.send_test_photo(frame_bgr)   # bypasses debounce
    tm.capture_next_chat_id(callback)   # arms auto-capture
"""

import os
import json
import time
import threading
import asyncio
import traceback
from io import BytesIO

import cv2

try:
    from telegram import (
        Update,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        ReplyKeyboardMarkup,
        KeyboardButton,
        BotCommand,
        InputFile,
    )
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        ContextTypes,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    # Stubs so class-level type hints don't crash at import time.
    # The actual bot code is gated on TELEGRAM_AVAILABLE and will refuse
    # to start() with a clear error message if the package is missing.
    Update = object
    Application = None
    CommandHandler = None
    MessageHandler = None
    CallbackQueryHandler = None
    filters = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    ReplyKeyboardMarkup = None
    KeyboardButton = None
    BotCommand = None
    InputFile = None
    class _StubContextTypes:
        DEFAULT_TYPE = object
    ContextTypes = _StubContextTypes


CONFIG_FILENAME = "telegram_config.json"


class TelegramManager:
    def __init__(self, user_dir, log_callback, app_ref):
        """
        user_dir: str, path to %LOCALAPPDATA%\\KonstanceWatchdog
        log_callback: callable(msg, color="#hex") -> main.log
        app_ref: the CentauriWatchdog instance (weak coupling, used for
                 snapshot / status / trigger_action from bot commands)
        """
        self.user_dir = user_dir
        self.log = log_callback
        self.app = app_ref
        self.config_path = os.path.join(user_dir, CONFIG_FILENAME)

        self.token = ""
        self.authorized_chat_ids = []
        self.debounce_seconds = 60
        self.auto_start = True

        self._application = None
        self._loop = None
        self._thread = None
        self._running = False
        self._last_alert_ts = 0.0
        self._alert_lock = threading.Lock()
        self._muted_until = 0.0  # epoch seconds; alerts suppressed while now() < this

        # Per-chat pending input state for multi-step flows (set temp, preheat wizard)
        # Shape: {chat_id: {"kind": str, "data": dict, "expires": float}}
        self._pending_input = {}
        self._pending_lock = threading.Lock()

        # File browser state per chat:
        # {chat_id: {"page": int, "files": [...], "selected": str, "leveling": bool, "timelapse": bool}}
        self._file_state = {}
        self._file_state_lock = threading.Lock()
        self._file_list_waiters = []  # callbacks waiting for next 258 response

        # Auto-capture state: when armed, next message from an unknown
        # chat_id gets piped to this callback (UI side shows confirm prompt)
        self._capture_callback = None
        self._capture_lock = threading.Lock()

    # -------- properties --------
    @property
    def is_running(self):
        return self._running

    @property
    def has_token(self):
        return bool(self.token and self.token.strip())

    # -------- config persistence --------
    def load_config(self):
        if not os.path.exists(self.config_path):
            return False
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.token = data.get("token", "")
            self.authorized_chat_ids = list(data.get("authorized_chat_ids", []))
            self.debounce_seconds = int(data.get("debounce_seconds", 60))
            self.auto_start = bool(data.get("auto_start", True))
            return True
        except Exception as e:
            self.log(f"Telegram: failed to load config: {e}", "#da3633")
            return False

    def save_config(self):
        try:
            data = {
                "token": self.token,
                "authorized_chat_ids": self.authorized_chat_ids,
                "debounce_seconds": self.debounce_seconds,
                "auto_start": self.auto_start,
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            self.log(f"Telegram: failed to save config: {e}", "#da3633")
            return False

    # -------- lifecycle --------
    def start(self):
        """Start the bot in a background thread. Safe to call if already running."""
        if not TELEGRAM_AVAILABLE:
            self.log("Telegram: python-telegram-bot not installed.", "#da3633")
            return False
        if self._running:
            return True
        if not self.has_token:
            self.log("Telegram: no token configured, bot not started.", "#d29922")
            return False

        self._thread = threading.Thread(target=self._run_bot, daemon=True)
        self._thread.start()
        # Wait briefly for startup feedback (non-blocking overall)
        for _ in range(30):
            if self._running:
                return True
            time.sleep(0.1)
        return self._running

    def stop(self):
        """Stop the bot cleanly. Safe to call anytime."""
        if not self._running or self._loop is None:
            self._running = False
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
            fut.result(timeout=5)
        except Exception:
            pass
        self._running = False

    async def _shutdown_async(self):
        if self._application is not None:
            try:
                await self._application.updater.stop()
            except Exception:
                pass
            try:
                await self._application.stop()
            except Exception:
                pass
            try:
                await self._application.shutdown()
            except Exception:
                pass

    def _run_bot(self):
        """Entry point for the bot thread. Owns its own asyncio loop."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._bot_main())
        except Exception as e:
            self.log(f"Telegram: bot thread crashed: {e}", "#da3633")
            try:
                self.app.after(0, lambda err=e: self.log(
                    f"Telegram: bot crashed: {err}", "#da3633"))
            except Exception:
                pass
            self._running = False
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _bot_main(self):
        self._application = Application.builder().token(self.token).build()

        # Register handlers
        self._application.add_handler(CommandHandler("start", self._cmd_start))
        self._application.add_handler(CommandHandler("help", self._cmd_help))
        self._application.add_handler(CommandHandler("status", self._cmd_status))
        self._application.add_handler(CommandHandler("snapshot", self._cmd_snapshot))
        self._application.add_handler(CommandHandler("pause", self._cmd_pause))
        self._application.add_handler(CommandHandler("resume", self._cmd_resume))
        self._application.add_handler(CommandHandler("stop", self._cmd_stop_print))
        self._application.add_handler(CommandHandler("menu", self._cmd_menu))
        self._application.add_handler(CommandHandler("temps", self._cmd_temps))
        self._application.add_handler(CommandHandler("files", self._cmd_files))
        self._application.add_handler(CommandHandler("mute", self._cmd_mute))
        self._application.add_handler(CommandHandler("unmute", self._cmd_unmute))
        self._application.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self._application.add_handler(CommandHandler("myid", self._cmd_myid))

        # Inline button callbacks (tap on an alert button)
        self._application.add_handler(CallbackQueryHandler(self._on_callback_query))

        # G-code document uploads (must come BEFORE the catch-all text handler)
        self._application.add_handler(
            MessageHandler(filters.Document.ALL, self._on_document)
        )

        # Catch-all for auto-capture of chat IDs AND reply-keyboard button presses
        self._application.add_handler(MessageHandler(filters.ALL, self._on_any_message))

        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling(drop_pending_updates=True)

        self._running = True

        # Auto-configure bot branding (name, description, commands, picture)
        try:
            await self._apply_bot_branding()
        except Exception as e:
            try:
                self.app.after(0, lambda err=e: self.log(
                    f"Telegram: branding skipped: {err}", "#d29922"))
            except Exception:
                pass

        try:
            self.app.after(0, lambda: self.log(
                "Telegram: bot is live.", "#3fb950"))
        except Exception:
            pass

        # Block this coroutine until stop() tears it down
        while self._running:
            await asyncio.sleep(0.5)

    # -------- authorization --------
    def _is_authorized(self, chat_id):
        return int(chat_id) in [int(x) for x in self.authorized_chat_ids]

    def add_chat_id(self, chat_id):
        cid = int(chat_id)
        if cid not in [int(x) for x in self.authorized_chat_ids]:
            self.authorized_chat_ids.append(cid)
            self.save_config()
            return True
        return False

    def remove_chat_id(self, chat_id):
        cid = int(chat_id)
        self.authorized_chat_ids = [
            int(x) for x in self.authorized_chat_ids if int(x) != cid
        ]
        self.save_config()

    def capture_next_chat_id(self, callback):
        """Arm auto-capture. callback(chat_id, username) called from bot thread.
        UI should re-dispatch to Tk main thread with self.app.after(0, ...)."""
        with self._capture_lock:
            self._capture_callback = callback

    def cancel_capture(self):
        with self._capture_lock:
            self._capture_callback = None

    # -------- keyboards (built once per call, cheap) --------
    def _reply_keyboard(self):
        """Persistent keyboard at bottom of chat. Always visible."""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("📊 Status"), KeyboardButton("📷 Snapshot")],
                [KeyboardButton("⏸️ Pause"), KeyboardButton("▶️ Resume"), KeyboardButton("⏹️ Stop")],
                [KeyboardButton("📁 Files"), KeyboardButton("📋 All Controls")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )

    def _alert_inline_kb(self, auto_paused: bool):
        """Inline keyboard attached to an alert photo.
        If auto_paused=True, show Resume + Stop (print was already paused).
        Otherwise show Pause + Stop (warn mode, print still running)."""
        if auto_paused:
            row1 = [
                InlineKeyboardButton("▶️ Resume", callback_data="act:resume"),
                InlineKeyboardButton("⏹️ Stop", callback_data="act:stop"),
            ]
        else:
            row1 = [
                InlineKeyboardButton("⏸️ Pause", callback_data="act:pause"),
                InlineKeyboardButton("⏹️ Stop", callback_data="act:stop"),
            ]
        row2 = [
            InlineKeyboardButton("📋 All Controls", callback_data="menu:open"),
            InlineKeyboardButton("🔕 Mute 1h", callback_data="mute:3600"),
        ]
        return InlineKeyboardMarkup([row1, row2])

    def _full_menu_inline_kb(self):
        """The 'All Controls' expanded menu."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Status", callback_data="act:status"),
                InlineKeyboardButton("📷 Snapshot", callback_data="act:snapshot"),
            ],
            [
                InlineKeyboardButton("⏸️ Pause", callback_data="act:pause"),
                InlineKeyboardButton("▶️ Resume", callback_data="act:resume"),
                InlineKeyboardButton("⏹️ Stop", callback_data="act:stop"),
            ],
            [
                InlineKeyboardButton("🌡️ Temps", callback_data="act:temps"),
                InlineKeyboardButton("📁 Files", callback_data="act:files"),
            ],
            [
                InlineKeyboardButton("🔕 Mute 1h", callback_data="mute:3600"),
                InlineKeyboardButton("🔕 Mute 4h", callback_data="mute:14400"),
                InlineKeyboardButton("🔔 Unmute", callback_data="mute:0"),
            ],
            [
                InlineKeyboardButton("✖️ Close menu", callback_data="menu:close"),
            ],
        ])

    # -------- command handlers --------
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self._is_authorized(chat_id):
            await update.message.reply_text(
                "🔒 You are not authorized for this bot.\n"
                f"Your chat ID is: {chat_id}\n"
                "Ask the printer owner to add you via the Telegram Remote window."
            )
            return
        await update.message.reply_text(
            "👋 *Konstance Watchdog connected.*\n\n"
            "Use the buttons below — no typing needed.\n"
            "Tap *📋 All Controls* for more options.\n\n"
            "You'll get an alert photo with action buttons whenever the AI detects an anomaly.",
            parse_mode="Markdown",
            reply_markup=self._reply_keyboard(),
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await update.message.reply_text(
            "*Commands:*\n"
            "/status — printer + AI status\n"
            "/snapshot — live camera photo\n"
            "/pause — pause print\n"
            "/resume — resume print\n"
            "/stop — stop print\n"
            "/temps — nozzle/bed/chamber temps\n"
            "/menu — open all controls\n"
            "/mute — silence alerts for 1 hour\n"
            "/unmute — re-enable alerts\n"
            "/myid — show your chat ID",
            parse_mode="Markdown",
            reply_markup=self._reply_keyboard(),
        )

    async def _cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await update.message.reply_text(
            "📋 *All Controls*",
            parse_mode="Markdown",
            reply_markup=self._full_menu_inline_kb(),
        )

    async def _cmd_myid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await update.message.reply_text(f"Your chat ID: `{chat_id}`", parse_mode="Markdown")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._send_status_reply(update.effective_chat.id)

    async def _send_status_reply(self, chat_id):
        try:
            status = getattr(self.app, "last_status", "UNKNOWN")
            strikes = getattr(self.app, "strike_counter", 0)
            total_anom = getattr(self.app, "stat_total_anomalies", 0)
            auto_pauses = getattr(self.app, "stat_auto_pauses", 0)
            konstance_on = getattr(self.app, "konstance_active", False)
            mute_str = ""
            if self._muted_until > time.time():
                mins = int((self._muted_until - time.time()) / 60)
                mute_str = f"\n🔕 Alerts muted for {mins} more min"
            preheat_str = ""
            try:
                if getattr(self.app, "preheat_active", False):
                    mins = self.app.preheat_remaining_minutes()
                    preheat_str = f"\n🔥 Preheat active — {mins} min remaining"
            except Exception:
                pass
            msg = (
                f"🖨️ Printer: *{status}*\n"
                f"🤖 Konstance AI: {'ON' if konstance_on else 'OFF'}\n"
                f"⚡ Current strikes: {strikes}\n"
                f"📊 Total anomalies: {total_anom}\n"
                f"⏸️ Auto-pauses: {auto_pauses}"
                f"{preheat_str}"
                f"{mute_str}"
            )
            await self._application.bot.send_message(
                chat_id=chat_id, text=msg, parse_mode="Markdown"
            )
        except Exception as e:
            try:
                await self._application.bot.send_message(chat_id=chat_id, text=f"Error: {e}")
            except Exception:
                pass

    async def _cmd_temps(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._send_temps_reply(update.effective_chat.id)

    # -------- pending-input state (multi-step flows) --------
    PENDING_TTL = 120  # seconds before pending input expires

    def _set_pending(self, chat_id, kind, data=None):
        with self._pending_lock:
            self._pending_input[chat_id] = {
                "kind": kind,
                "data": data or {},
                "expires": time.time() + self.PENDING_TTL,
            }

    def _get_pending(self, chat_id):
        with self._pending_lock:
            p = self._pending_input.get(chat_id)
            if p is None:
                return None
            if time.time() > p["expires"]:
                del self._pending_input[chat_id]
                return None
            return p

    def _clear_pending(self, chat_id):
        with self._pending_lock:
            self._pending_input.pop(chat_id, None)

    async def _handle_pending_input(self, chat_id, text):
        """Returns True if the message was consumed by a pending flow."""
        pending = self._get_pending(chat_id)
        if pending is None:
            return False

        if text.strip().lower() in ("/cancel", "cancel", "abort"):
            self._clear_pending(chat_id)
            await self._application.bot.send_message(
                chat_id=chat_id, text="❌ Cancelled."
            )
            return True

        kind = pending["kind"]
        data = pending["data"]

        # Parse number
        try:
            val = float(text.strip().replace(",", "."))
        except ValueError:
            await self._application.bot.send_message(
                chat_id=chat_id,
                text="❌ Not a valid number. Try again or send /cancel.",
            )
            return True

        if kind == "nozzle_temp":
            if not (0 <= val <= 300):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Must be 0-300. Try again or /cancel."
                )
                return True
            self._clear_pending(chat_id)
            self.app.after(0, lambda: self.app.set_target_temp("nozzle", val))
            await self._application.bot.send_message(
                chat_id=chat_id, text=f"✅ Nozzle target set to {val:.0f}°C."
            )
            return True

        if kind == "bed_temp":
            if not (0 <= val <= 120):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Must be 0-120. Try again or /cancel."
                )
                return True
            self._clear_pending(chat_id)
            self.app.after(0, lambda: self.app.set_target_temp("bed", val))
            await self._application.bot.send_message(
                chat_id=chat_id, text=f"✅ Bed target set to {val:.0f}°C."
            )
            return True

        if kind == "preheat_nozzle":
            if not (0 <= val <= 300):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Nozzle must be 0-300. Try again or /cancel."
                )
                return True
            data["nozzle"] = val
            self._set_pending(chat_id, "preheat_bed", data=data)
            await self._application.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ Nozzle: {val:.0f}°C\n\n"
                    "🔥 *Preheat Wizard* — step 2 of 3\n\n"
                    "Reply with target *bed* temperature (0-120°C)."
                ),
                parse_mode="Markdown",
            )
            return True

        if kind == "preheat_bed":
            if not (0 <= val <= 120):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Bed must be 0-120. Try again or /cancel."
                )
                return True
            data["bed"] = val
            self._set_pending(chat_id, "preheat_duration", data=data)
            await self._application.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ Nozzle: {data['nozzle']:.0f}°C\n"
                    f"✅ Bed: {val:.0f}°C\n\n"
                    "🔥 *Preheat Wizard* — step 3 of 3\n\n"
                    "Reply with *duration* in minutes (1-120)."
                ),
                parse_mode="Markdown",
            )
            return True

        if kind == "preheat_duration":
            minutes = int(val)
            if not (1 <= minutes <= 120):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Duration must be 1-120 minutes. Try again or /cancel."
                )
                return True
            data["minutes"] = minutes
            # Move to confirmation state
            self._set_pending(chat_id, "preheat_confirm", data=data)
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔥 Start Preheat", callback_data="preheat:confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="preheat:abort"),
                ],
            ])
            await self._application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔥 *Confirm Preheat*\n\n"
                    f"Nozzle: `{data['nozzle']:.0f}°C`\n"
                    f"Bed: `{data['bed']:.0f}°C`\n"
                    f"Duration: `{minutes} min`\n\n"
                    "_Sequence:_\n"
                    "1️⃣ Home XYZ\n"
                    "2️⃣ Drop bed 200mm (after 15s)\n"
                    "3️⃣ Start heating (after 3s)\n"
                    "4️⃣ Auto cool-down when duration expires\n\n"
                    "Tap *Start Preheat* to begin."
                ),
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return True

        return False

    # -------- notifications triggered by main.py preheat state changes --------
    def notify_preheat_started(self, nozzle, bed, minutes):
        """Called from Tk thread when preheat sequence actually starts heating."""
        if not self._running or self._loop is None or not self.authorized_chat_ids:
            return
        msg = (
            f"🔥 *Preheat active*\n"
            f"Nozzle: `{nozzle:.0f}°C`\n"
            f"Bed: `{bed:.0f}°C`\n"
            f"Auto cool-down in {minutes} min"
        )
        recipients = list(self.authorized_chat_ids)
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(msg, recipients), self._loop
        )

    def notify_preheat_ended(self, reason):
        if not self._running or self._loop is None or not self.authorized_chat_ids:
            return
        msg = f"❄️ Preheat ended ({reason}). Temps set to 0°."
        recipients = list(self.authorized_chat_ids)
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(msg, recipients), self._loop
        )

    def notify_preheat_ready(self, nozzle_cur, bed_cur):
        """Called when both target temps have been reached."""
        if not self._running or self._loop is None or not self.authorized_chat_ids:
            return
        msg = (
            f"✅ *Preheat ready!*\n"
            f"Nozzle: `{nozzle_cur:.1f}°C`\n"
            f"Bed: `{bed_cur:.1f}°C`\n"
            f"Chamber fan circulating at 40%."
        )
        recipients = list(self.authorized_chat_ids)
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(msg, recipients), self._loop
        )

    async def _async_broadcast(self, text, recipients):
        for cid in recipients:
            try:
                await self._application.bot.send_message(
                    chat_id=cid, text=text, parse_mode="Markdown"
                )
            except Exception:
                pass

    async def _send_temps_reply(self, chat_id):
        try:
            nozzle_cur = getattr(self.app, "temp_current_nozzle", 0.0)
            nozzle_tgt = getattr(self.app, "temp_target_nozzle", 0.0)
            bed_cur = getattr(self.app, "temp_current_bed", 0.0)
            bed_tgt = getattr(self.app, "temp_target_bed", 0.0)
            chamber_cur = getattr(self.app, "temp_current_chamber", 0.0)

            lines = ["🌡️ *Temperatures*"]
            lines.append(f"🔥 Nozzle: `{nozzle_cur:.1f}° / {nozzle_tgt:.0f}°`")
            lines.append(f"🛏️ Bed: `{bed_cur:.1f}° / {bed_tgt:.0f}°`")
            lines.append(f"📦 Chamber: `{chamber_cur:.1f}°`")

            # Show active preheat status
            try:
                if getattr(self.app, "preheat_active", False):
                    mins = self.app.preheat_remaining_minutes()
                    lines.append(f"\n🔥 *Preheat active — {mins} min remaining*")
            except Exception:
                pass

            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔥 Set Nozzle", callback_data="temp:set_nozzle"),
                    InlineKeyboardButton("🛏️ Set Bed", callback_data="temp:set_bed"),
                ],
                [
                    InlineKeyboardButton("🔥 Preheat", callback_data="preheat:start"),
                    InlineKeyboardButton("❄️ Cool Down", callback_data="temp:cooldown"),
                ],
            ])

            await self._application.bot.send_message(
                chat_id=chat_id, text="\n".join(lines),
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception as e:
            try:
                await self._application.bot.send_message(chat_id=chat_id, text=f"Error: {e}")
            except Exception:
                pass

    # -------- File browser --------
    FILES_PER_PAGE = 5
    MAX_UPLOAD_MB = 20  # Telegram Bot API hard limit for bot-downloadable files

    async def _cmd_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._send_files_reply(update.effective_chat.id, fresh=True)

    async def _send_files_reply(self, chat_id, fresh=False):
        """Fetch (or re-use cached) file list and render page 0.
        fresh=True forces a new cmd 258 fetch. Otherwise uses app.cached_file_list."""
        try:
            if fresh:
                # Arm a waiter so we get notified when the list arrives
                await self._application.bot.send_message(
                    chat_id=chat_id, text="📁 Fetching file list..."
                )
                # Register a one-shot waiter that will fire _render_files_page
                async def _on_received():
                    files = list(getattr(self.app, "cached_file_list", []) or [])
                    with self._file_state_lock:
                        self._file_state[chat_id] = {
                            "page": 0, "files": files,
                            "selected": None, "leveling": False, "timelapse": False,
                            "plate_type": 0,
                        }
                    await self._render_files_page(chat_id)

                # Schedule: bot-thread-friendly one-shot
                self._file_list_waiters.append((chat_id, _on_received))
                # Trigger the fetch on Tk thread
                self.app.after(0, lambda: self.app.request_file_list_for_telegram("/local/"))
                return

            # Use cached list
            files = list(getattr(self.app, "cached_file_list", []) or [])
            if not files:
                # No cache yet — fetch fresh
                await self._send_files_reply(chat_id, fresh=True)
                return
            with self._file_state_lock:
                self._file_state[chat_id] = {
                    "page": 0, "files": files,
                    "selected": None, "leveling": False, "timelapse": False,
                    "plate_type": 0,
                }
            await self._render_files_page(chat_id)
        except Exception as e:
            try:
                await self._application.bot.send_message(chat_id=chat_id, text=f"Error: {e}")
            except Exception:
                pass

    def on_file_list_received(self, files):
        """Called from main.py's 258 handler (Tk thread). Fires any pending waiters
        on the bot loop so Telegram can render the list."""
        if self._loop is None or not self._file_list_waiters:
            return
        # Drain all waiters — each one was registered for a specific chat_id,
        # but by now cached_file_list is populated so any waiter can just render.
        waiters = list(self._file_list_waiters)
        self._file_list_waiters.clear()
        for chat_id, coro_fn in waiters:
            try:
                asyncio.run_coroutine_threadsafe(coro_fn(), self._loop)
            except Exception:
                pass

    async def _render_files_page(self, chat_id):
        """Show the current page of files as an inline keyboard."""
        with self._file_state_lock:
            state = self._file_state.get(chat_id)
        if not state:
            await self._application.bot.send_message(
                chat_id=chat_id, text="📁 No file list. Try /files again."
            )
            return

        files = state["files"]
        # Filter to only .gcode files (folders hidden for simplicity)
        gcode_files = [f for f in files if f.get("type", 1) == 1 and f.get("name", "").lower().endswith(".gcode")]

        if not gcode_files:
            await self._application.bot.send_message(
                chat_id=chat_id,
                text="📁 No G-code files found on the printer.",
            )
            return

        page = state["page"]
        total = len(gcode_files)
        total_pages = max(1, (total + self.FILES_PER_PAGE - 1) // self.FILES_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        state["page"] = page

        start = page * self.FILES_PER_PAGE
        end = min(start + self.FILES_PER_PAGE, total)
        page_files = gcode_files[start:end]

        rows = []
        for idx, f in enumerate(page_files):
            name = f.get("name", "unknown")
            display = name.split("/")[-1]
            short = display if len(display) <= 30 else display[:27] + "..."
            # Index into the filtered list (file:<global_idx>)
            global_idx = start + idx
            rows.append([InlineKeyboardButton(f"📄 {short}", callback_data=f"file:{global_idx}")])

        # Pagination row
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"files:page:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="files:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"files:page:{page+1}"))
        rows.append(nav)
        rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="files:refresh")])

        # Store the filtered list so tap handlers index correctly
        state["filtered"] = gcode_files

        await self._application.bot.send_message(
            chat_id=chat_id,
            text=f"📁 *Printer files* ({total} G-code files)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _show_file_detail(self, chat_id, file_idx):
        """Show a single file's details with Start Print / Delete / Back buttons."""
        with self._file_state_lock:
            state = self._file_state.get(chat_id)
            if not state:
                await self._application.bot.send_message(
                    chat_id=chat_id, text="📁 Session expired. Send /files again."
                )
                return
            files = state.get("filtered", [])
            if file_idx < 0 or file_idx >= len(files):
                await self._application.bot.send_message(
                    chat_id=chat_id, text="📁 File index out of range."
                )
                return
            f = files[file_idx]
            state["selected"] = f.get("name", "")

        name = f.get("name", "unknown")
        display = name.split("/")[-1]
        size = f.get("size", 0)
        size_mb = size / (1024 * 1024) if size else 0
        ctime = f.get("CreateTime", 0)
        time_str = ""
        if isinstance(ctime, (int, float)) and ctime > 0:
            try:
                from datetime import datetime as _dt
                time_str = _dt.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        lines = [f"📄 *{display}*"]
        if size_mb > 0:
            lines.append(f"Size: `{size_mb:.1f} MB`")
        if time_str:
            lines.append(f"Created: `{time_str}`")
        lines.append(f"Path: `{name}`")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖨️ Start Print", callback_data=f"print:options:{file_idx}")],
            [
                InlineKeyboardButton("🗑️ Delete", callback_data=f"file:delete:{file_idx}"),
                InlineKeyboardButton("◀️ Back", callback_data="files:back"),
            ],
        ])
        await self._application.bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode="Markdown", reply_markup=kb,
        )

    async def _show_print_options(self, chat_id, file_idx):
        """Show the Leveling/Timelapse/Plate toggle screen before starting a print."""
        with self._file_state_lock:
            state = self._file_state.get(chat_id)
            if not state:
                await self._application.bot.send_message(
                    chat_id=chat_id, text="📁 Session expired. Send /files again."
                )
                return
            files = state.get("filtered", [])
            if file_idx < 0 or file_idx >= len(files):
                return
            f = files[file_idx]
            lev = state.get("leveling", False)
            tl = state.get("timelapse", False)
            plate = state.get("plate_type", 0)

        display = f.get("name", "").split("/")[-1]

        lev_mark = "✅ ON" if lev else "⬜ OFF"
        tl_mark = "✅ ON" if tl else "⬜ OFF"
        plate_label = "🧱 Textured PEI" if plate == 0 else "🪞 Smooth PEI"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Leveling: {lev_mark}", callback_data=f"print:toggle:leveling:{file_idx}")],
            [InlineKeyboardButton(f"Timelapse: {tl_mark}", callback_data=f"print:toggle:timelapse:{file_idx}")],
            [InlineKeyboardButton(f"Plate: {plate_label}", callback_data=f"print:toggle:plate:{file_idx}")],
            [
                InlineKeyboardButton("✅ Start Now", callback_data=f"print:confirm:{file_idx}"),
                InlineKeyboardButton("❌ Cancel", callback_data="files:back"),
            ],
        ])
        await self._application.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🖨️ *Start Print*\n\n"
                f"File: `{display}`\n\n"
                f"Tap to toggle options, then Start Now."
            ),
            parse_mode="Markdown",
            reply_markup=kb,
        )

    # -------- G-code upload via Telegram --------
    async def _on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle G-code file uploads from Telegram."""
        if not self._is_authorized(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        doc = update.message.document if update.message else None
        if doc is None:
            return

        fname = doc.file_name or "upload.gcode"
        if not fname.lower().endswith(".gcode"):
            await update.message.reply_text(
                "❌ Only .gcode files are supported."
            )
            return

        size_mb = (doc.file_size or 0) / (1024 * 1024)
        if (doc.file_size or 0) > self.MAX_UPLOAD_MB * 1024 * 1024:
            await update.message.reply_text(
                f"❌ File too big ({size_mb:.1f} MB). "
                f"Telegram bot API limits uploads to {self.MAX_UPLOAD_MB} MB.\n\n"
                "For larger files, use the Konstance app directly."
            )
            return

        await update.message.reply_text(
            f"📥 Receiving `{fname}` ({size_mb:.1f} MB)...",
            parse_mode="Markdown",
        )

        try:
            # Download to temp file
            tg_file = await doc.get_file()
            tmp_path = os.path.join(self.user_dir, f"_tg_upload_{fname}")
            await tg_file.download_to_drive(tmp_path)
        except Exception as e:
            await update.message.reply_text(f"❌ Download from Telegram failed: {e}")
            return

        await update.message.reply_text("📤 Uploading to printer...")

        # Now push it to the printer via file_manager
        def _on_upload_done(success, err_msg):
            if self._loop is None:
                return
            if success:
                async def _reply_ok():
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🖨️ Start Print", callback_data=f"upload:print:{fname}")],
                        [InlineKeyboardButton("✖️ Done", callback_data="upload:done")],
                    ])
                    await self._application.bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ Uploaded `{fname}` to printer.",
                        parse_mode="Markdown",
                        reply_markup=kb,
                    )
                asyncio.run_coroutine_threadsafe(_reply_ok(), self._loop)
            else:
                async def _reply_err():
                    await self._application.bot.send_message(
                        chat_id=chat_id, text=f"❌ Upload to printer failed: {err_msg}"
                    )
                asyncio.run_coroutine_threadsafe(_reply_err(), self._loop)
            # Clean up temp file
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # file_manager runs the upload on its own thread
        try:
            self.app.after(
                0,
                lambda: self.app.file_manager.upload_path_to_printer(tmp_path, on_done=_on_upload_done)
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Dispatch failed: {e}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    async def _cmd_snapshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._send_snapshot_reply(update.effective_chat.id)

    async def _send_snapshot_reply(self, chat_id):
        frame = self._get_latest_frame()
        if frame is None:
            await self._application.bot.send_message(
                chat_id=chat_id, text="📷 No camera frame available."
            )
            return
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            await self._application.bot.send_message(
                chat_id=chat_id, text="📷 Failed to encode frame."
            )
            return
        bio = BytesIO(buf.tobytes())
        bio.name = "snapshot.jpg"
        await self._application.bot.send_photo(
            chat_id=chat_id, photo=bio, caption="📷 Live snapshot",
            reply_markup=self._full_menu_inline_kb(),
        )

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._do_action("pause", update.effective_chat.id)

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._do_action("resume", update.effective_chat.id)

    async def _cmd_stop_print(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        await self._do_action("stop", update.effective_chat.id)

    async def _cmd_mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        self._muted_until = time.time() + 3600
        await update.message.reply_text("🔕 Alerts muted for 1 hour.")

    async def _cmd_unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_chat.id):
            return
        self._muted_until = 0.0
        await update.message.reply_text("🔔 Alerts re-enabled.")

    async def _cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel any in-progress multi-step flow (set temp / preheat wizard)."""
        if not self._is_authorized(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        if self._get_pending(chat_id) is not None:
            self._clear_pending(chat_id)
            await update.message.reply_text("❌ Cancelled.")
        else:
            await update.message.reply_text("Nothing to cancel.")

    async def _do_action(self, action, chat_id, confirmed=False):
        """Bridge an action to the main Tk thread.
        For pause/resume/stop, requires confirmation unless confirmed=True.
        Confirmation is two-tap: first tap shows Yes/No buttons, second tap fires."""
        if action in ("pause", "resume", "stop") and not confirmed:
            # Show confirmation
            labels = {
                "pause": ("⏸️ Confirm pause?", "Pause the current print?"),
                "resume": ("▶️ Confirm resume?", "Resume the paused print?"),
                "stop": ("⏹️ Confirm STOP?", "⚠️ Stop the print? *This cannot be undone.*"),
            }
            title, body = labels[action]
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes", callback_data=f"confirm:{action}"),
                    InlineKeyboardButton("❌ No", callback_data="confirm:cancel"),
                ],
            ])
            try:
                await self._application.bot.send_message(
                    chat_id=chat_id, text=f"*{title}*\n{body}",
                    parse_mode="Markdown", reply_markup=kb,
                )
            except Exception as e:
                try:
                    await self._application.bot.send_message(chat_id=chat_id, text=f"Error: {e}")
                except Exception:
                    pass
            return

        # Confirmed (or non-control action) — actually fire it
        labels = {"pause": "⏸️ Pause sent", "resume": "▶️ Resume sent", "stop": "⏹️ Stop sent"}
        try:
            self.app.after(0, lambda a=action: self.app.trigger_action(a))
            await self._application.bot.send_message(
                chat_id=chat_id, text=labels.get(action, f"{action} sent")
            )
        except Exception as e:
            try:
                await self._application.bot.send_message(chat_id=chat_id, text=f"Error: {e}")
            except Exception:
                pass

    def on_print_start_ack(self, success, label):
        """Called from main.py when cmd 128 ack arrives. Broadcasts result."""
        if not self._running or self._loop is None or not self.authorized_chat_ids:
            return
        icon = "✅" if success else "❌"
        msg = f"{icon} Print start: *{label}*"
        recipients = list(self.authorized_chat_ids)
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(msg, recipients), self._loop
        )

    # -------- inline button callback handler --------
    async def _on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle taps on inline keyboard buttons."""
        query = update.callback_query
        if query is None:
            return
        chat_id = query.message.chat.id if query.message else update.effective_chat.id
        if not self._is_authorized(chat_id):
            await query.answer("🔒 Not authorized", show_alert=True)
            return

        data = query.data or ""
        try:
            # Always ack the tap so Telegram stops showing the loading spinner
            await query.answer()
        except Exception:
            pass

        try:
            if data.startswith("act:"):
                action = data.split(":", 1)[1]
                if action == "status":
                    await self._send_status_reply(chat_id)
                elif action == "snapshot":
                    await self._send_snapshot_reply(chat_id)
                elif action == "temps":
                    await self._send_temps_reply(chat_id)
                elif action == "files":
                    await self._send_files_reply(chat_id, fresh=True)
                elif action in ("pause", "resume", "stop"):
                    await self._do_action(action, chat_id)
            elif data.startswith("files:"):
                sub = data.split(":", 2)
                if len(sub) >= 2 and sub[1] == "page" and len(sub) == 3:
                    try:
                        new_page = int(sub[2])
                        with self._file_state_lock:
                            st = self._file_state.get(chat_id)
                            if st:
                                st["page"] = new_page
                        await self._render_files_page(chat_id)
                    except ValueError:
                        pass
                elif len(sub) >= 2 and sub[1] == "refresh":
                    await self._send_files_reply(chat_id, fresh=True)
                elif len(sub) >= 2 and sub[1] == "back":
                    await self._render_files_page(chat_id)
                # "noop" is a no-op page indicator button
            elif data.startswith("file:"):
                sub = data.split(":", 2)
                if len(sub) >= 2 and sub[1] == "delete" and len(sub) == 3:
                    try:
                        idx = int(sub[2])
                        with self._file_state_lock:
                            st = self._file_state.get(chat_id, {})
                            files = st.get("filtered", [])
                            if 0 <= idx < len(files):
                                fname = files[idx].get("name", "")
                            else:
                                fname = ""
                        if fname:
                            def _do_delete():
                                ok, msg = self.app.delete_file_remote(fname)
                                if self._loop is not None:
                                    asyncio.run_coroutine_threadsafe(
                                        self._application.bot.send_message(
                                            chat_id=chat_id,
                                            text=(f"✅ {msg}" if ok else f"❌ {msg}"),
                                        ),
                                        self._loop,
                                    )
                            self.app.after(0, _do_delete)
                    except ValueError:
                        pass
                else:
                    # file:<idx> — show detail
                    try:
                        idx = int(sub[1])
                        await self._show_file_detail(chat_id, idx)
                    except (ValueError, IndexError):
                        pass
            elif data.startswith("print:"):
                sub = data.split(":", 3)
                if len(sub) >= 3 and sub[1] == "options":
                    try:
                        idx = int(sub[2])
                        await self._show_print_options(chat_id, idx)
                    except ValueError:
                        pass
                elif len(sub) >= 4 and sub[1] == "toggle":
                    key = sub[2]
                    try:
                        idx = int(sub[3])
                        with self._file_state_lock:
                            st = self._file_state.get(chat_id, {})
                            if key == "leveling":
                                st["leveling"] = not st.get("leveling", False)
                            elif key == "timelapse":
                                st["timelapse"] = not st.get("timelapse", False)
                            elif key == "plate":
                                # Cycle 0 (Textured) <-> 1 (Smooth)
                                st["plate_type"] = 1 - st.get("plate_type", 0)
                        await self._show_print_options(chat_id, idx)
                    except ValueError:
                        pass
                elif len(sub) >= 3 and sub[1] == "confirm":
                    try:
                        idx = int(sub[2])
                        with self._file_state_lock:
                            st = self._file_state.get(chat_id, {})
                            files = st.get("filtered", [])
                            if 0 <= idx < len(files):
                                fname = files[idx].get("name", "")
                                lev = st.get("leveling", False)
                                tl = st.get("timelapse", False)
                                plate = st.get("plate_type", 0)
                            else:
                                fname = ""
                                lev = tl = False
                                plate = 0
                        if fname:
                            def _do_start():
                                ok, msg = self.app.start_print_file(
                                    fname, leveling=lev, timelapse=tl, plate_type=plate
                                )
                                if self._loop is not None:
                                    asyncio.run_coroutine_threadsafe(
                                        self._application.bot.send_message(
                                            chat_id=chat_id,
                                            text=(f"✅ {msg}" if ok else f"❌ {msg}"),
                                        ),
                                        self._loop,
                                    )
                            self.app.after(0, _do_start)
                    except ValueError:
                        pass
            elif data.startswith("upload:"):
                sub = data.split(":", 2)
                if len(sub) == 3 and sub[1] == "print":
                    # User uploaded a file and tapped Start Print.
                    # Route through the same options screen as the file browser.
                    fname = sub[2]
                    full_path = f"/local/{fname}"
                    fake_file = {"name": full_path, "type": 1}
                    with self._file_state_lock:
                        self._file_state[chat_id] = {
                            "page": 0,
                            "files": [fake_file],
                            "filtered": [fake_file],
                            "selected": full_path,
                            "leveling": False,
                            "timelapse": False,
                            "plate_type": 0,
                        }
                    await self._show_print_options(chat_id, 0)
                elif len(sub) >= 2 and sub[1] == "done":
                    await self._application.bot.send_message(chat_id=chat_id, text="✅ Done.")
            elif data.startswith("confirm:"):
                action = data.split(":", 1)[1]
                if action == "cancel":
                    try:
                        await query.message.delete()
                    except Exception:
                        await self._application.bot.send_message(
                            chat_id=chat_id, text="❌ Cancelled."
                        )
                elif action in ("pause", "resume", "stop"):
                    # Delete the confirmation message and fire
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await self._do_action(action, chat_id, confirmed=True)
            elif data.startswith("mute:"):
                secs = int(data.split(":", 1)[1])
                if secs == 0:
                    self._muted_until = 0.0
                    await self._application.bot.send_message(
                        chat_id=chat_id, text="🔔 Alerts re-enabled."
                    )
                else:
                    self._muted_until = time.time() + secs
                    hrs = secs // 3600
                    await self._application.bot.send_message(
                        chat_id=chat_id, text=f"🔕 Alerts muted for {hrs} hour(s)."
                    )
            elif data == "menu:open":
                await self._application.bot.send_message(
                    chat_id=chat_id, text="📋 *All Controls*",
                    parse_mode="Markdown",
                    reply_markup=self._full_menu_inline_kb(),
                )
            elif data == "menu:close":
                try:
                    await query.message.delete()
                except Exception:
                    pass
            elif data == "temp:set_nozzle":
                self._set_pending(chat_id, "nozzle_temp")
                await self._application.bot.send_message(
                    chat_id=chat_id,
                    text="🔥 Reply with target *nozzle* temperature (0-300°C).\n_Send /cancel to abort._",
                    parse_mode="Markdown",
                )
            elif data == "temp:set_bed":
                self._set_pending(chat_id, "bed_temp")
                await self._application.bot.send_message(
                    chat_id=chat_id,
                    text="🛏️ Reply with target *bed* temperature (0-120°C).\n_Send /cancel to abort._",
                    parse_mode="Markdown",
                )
            elif data == "temp:cooldown":
                # Cancel any preheat and set everything to 0
                self.app.after(0, lambda: self.app.cancel_preheat(silent=False, reason="manual cool-down"))
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❄️ Cool down: nozzle and bed set to 0°. Preheat cancelled if active.",
                )
            elif data == "preheat:start":
                # Safety check first — block if printing or paused
                status = getattr(self.app, "last_status", "UNKNOWN")
                if status in ("Printing", "Preparing", "Paused", "Pausing"):
                    await self._application.bot.send_message(
                        chat_id=chat_id,
                        text=f"🚫 Cannot preheat: printer is *{status}*.\nFinish or stop the current print first.",
                        parse_mode="Markdown",
                    )
                    return
                self._set_pending(chat_id, "preheat_nozzle", data={})
                await self._application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "🔥 *Preheat Wizard* — step 1 of 3\n\n"
                        "Reply with target *nozzle* temperature (0-300°C).\n"
                        "_Send /cancel to abort at any step._"
                    ),
                    parse_mode="Markdown",
                )
            elif data == "preheat:confirm":
                # User tapped the final "Start Preheat" confirmation
                pending = self._get_pending(chat_id)
                if not pending or pending.get("kind") != "preheat_confirm":
                    await self._application.bot.send_message(
                        chat_id=chat_id, text="⚠️ Preheat session expired. Tap Preheat again to restart."
                    )
                    return
                p = pending.get("data", {})
                nozzle = p.get("nozzle")
                bed = p.get("bed")
                minutes = p.get("minutes")
                self._clear_pending(chat_id)
                # Execute on Tk main thread, capture result via a one-shot callback
                def _fire():
                    ok, msg = self.app.run_preheat_sequence(nozzle, bed, minutes)
                    # Schedule the Telegram reply back on the bot loop
                    if self._loop is not None:
                        asyncio.run_coroutine_threadsafe(
                            self._application.bot.send_message(
                                chat_id=chat_id,
                                text=(f"✅ {msg}" if ok else f"❌ {msg}"),
                            ),
                            self._loop,
                        )
                self.app.after(0, _fire)
            elif data == "preheat:abort":
                self._clear_pending(chat_id)
                await self._application.bot.send_message(
                    chat_id=chat_id, text="❌ Preheat cancelled."
                )
        except Exception as e:
            try:
                await self._application.bot.send_message(
                    chat_id=chat_id, text=f"Button error: {e}"
                )
            except Exception:
                pass

    async def _on_any_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Catch-all: handles auto-capture AND reply-keyboard button taps."""
        if update.effective_chat is None:
            return
        chat_id = update.effective_chat.id
        username = update.effective_chat.username or update.effective_chat.first_name or "unknown"

        # If auto-capture is armed and this is an unknown user, pipe it
        with self._capture_lock:
            cb = self._capture_callback
        if cb is not None and not self._is_authorized(chat_id):
            try:
                cb(chat_id, username)
            except Exception:
                traceback.print_exc()
            try:
                await update.message.reply_text(
                    f"👋 Hi {username}! Your chat ID ({chat_id}) has been sent "
                    "to the Konstance app for approval. The owner needs to confirm you."
                )
            except Exception:
                pass
            return

        # Unauthorized and not capturing -> give them their ID only
        if not self._is_authorized(chat_id):
            if update.message and update.message.text:
                try:
                    await update.message.reply_text(
                        f"🔒 Not authorized. Your chat ID is: {chat_id}"
                    )
                except Exception:
                    pass
            return

        # Authorized user: check if they tapped a reply-keyboard button
        text = (update.message.text if update.message else "") or ""
        text_stripped = text.strip()

        # FIRST: check if they're in the middle of a multi-step flow (set temp / preheat wizard)
        try:
            if await self._handle_pending_input(chat_id, text_stripped):
                return
        except Exception as e:
            try:
                await update.message.reply_text(f"Flow error: {e}")
            except Exception:
                pass
            return

        reply_map = {
            "📊 Status": lambda: self._send_status_reply(chat_id),
            "📷 Snapshot": lambda: self._send_snapshot_reply(chat_id),
            "⏸️ Pause": lambda: self._do_action("pause", chat_id),
            "▶️ Resume": lambda: self._do_action("resume", chat_id),
            "⏹️ Stop": lambda: self._do_action("stop", chat_id),
            "📋 All Controls": lambda: self._application.bot.send_message(
                chat_id=chat_id, text="📋 *All Controls*",
                parse_mode="Markdown",
                reply_markup=self._full_menu_inline_kb(),
            ),
        }
        handler = reply_map.get(text_stripped)
        if handler is not None:
            try:
                await handler()
            except Exception as e:
                try:
                    await update.message.reply_text(f"Error: {e}")
                except Exception:
                    pass

    # -------- auto-branding (one-shot after bot start) --------
    async def _apply_bot_branding(self):
        """Set name, descriptions, command menu, and profile picture.
        Called once per bot start. All calls wrapped individually so one
        failure doesn't kill the others."""
        bot = self._application.bot

        # Command menu (the blue "/" menu in Telegram clients)
        try:
            await bot.set_my_commands([
                BotCommand("status", "Printer + AI status"),
                BotCommand("snapshot", "Live camera photo"),
                BotCommand("pause", "Pause print"),
                BotCommand("resume", "Resume print"),
                BotCommand("stop", "Stop print"),
                BotCommand("temps", "Nozzle/bed/chamber temps"),
                BotCommand("files", "Browse printer files"),
                BotCommand("menu", "Open all controls"),
                BotCommand("mute", "Silence alerts for 1 hour"),
                BotCommand("unmute", "Re-enable alerts"),
                BotCommand("cancel", "Cancel current wizard"),
                BotCommand("myid", "Show your chat ID"),
                BotCommand("help", "Show command list"),
            ])
        except Exception:
            pass

        # Display name (the bold name at the top of the chat)
        try:
            await bot.set_my_name(name="Konstance Watchdog")
        except Exception:
            pass

        # Short description (the line under the bot name in chat header)
        try:
            await bot.set_my_short_description(
                short_description="3D printer AI watchdog — alerts + remote control."
            )
        except Exception:
            pass

        # Full description (shown when users first open the bot)
        try:
            await bot.set_my_description(
                description=(
                    "Konstance the Watchdog sends you AI-detected anomaly alerts "
                    "from your 3D printer and lets you pause, resume, or stop "
                    "prints remotely. Tap the buttons below any message — no "
                    "typing needed.\n\nTap START to begin."
                )
            )
        except Exception:
            pass

        # NOTE on profile picture: the Bot API has NO method for a bot to set
        # its own avatar. Only @BotFather can do that (via /setuserpic in a
        # chat with BotFather). We could guide the user to do this via the
        # setup wizard, but it cannot be automated. Skipping.

    # -------- outbound: alerts and photos --------
    def _get_latest_frame(self):
        """Thread-safe fetch of the most recent annotated frame from main app."""
        lock = getattr(self.app, "latest_frame_lock", None)
        if lock is None:
            return getattr(self.app, "latest_frame", None)
        with lock:
            frame = getattr(self.app, "latest_frame", None)
            return frame.copy() if frame is not None else None

    def send_alert(self, frame_bgr, caption, auto_paused=False):
        """Debounced alert send. Safe to call from the camera thread.
        auto_paused: if True, attached buttons show Resume+Stop; else Pause+Stop.
        Returns True if the send was dispatched, False if debounced/muted/no-op."""
        if not self._running or self._loop is None:
            return False
        if not self.authorized_chat_ids:
            return False
        if time.time() < self._muted_until:
            return False
        with self._alert_lock:
            now = time.time()
            if now - self._last_alert_ts < self.debounce_seconds:
                return False
            self._last_alert_ts = now
        self._dispatch_photo(frame_bgr, caption, with_alert_buttons=True, auto_paused=auto_paused)
        return True

    def send_test_photo(self, frame_bgr):
        """Bypasses debounce and mute. Used by the 'Send test photo' button."""
        if not self._running or self._loop is None:
            self.log("Telegram: bot not running, cannot send test.", "#da3633")
            return False
        if not self.authorized_chat_ids:
            self.log("Telegram: no authorized chat IDs.", "#da3633")
            return False
        self._dispatch_photo(
            frame_bgr,
            "🧪 Test photo from Konstance Watchdog",
            with_alert_buttons=False,
            auto_paused=False,
        )
        return True

    def _dispatch_photo(self, frame_bgr, caption, with_alert_buttons=False, auto_paused=False):
        """Encode on the calling thread (fast), ship coroutine to bot loop."""
        try:
            ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                return
            data = buf.tobytes()
        except Exception as e:
            self.log(f"Telegram: encode failed: {e}", "#da3633")
            return

        recipients = list(self.authorized_chat_ids)
        asyncio.run_coroutine_threadsafe(
            self._async_send_photo(data, caption, recipients, with_alert_buttons, auto_paused),
            self._loop
        )

    async def _async_send_photo(self, data, caption, recipients, with_alert_buttons, auto_paused):
        reply_markup = self._alert_inline_kb(auto_paused) if with_alert_buttons else None
        for cid in recipients:
            try:
                bio = BytesIO(data)
                bio.name = "alert.jpg"
                await self._application.bot.send_photo(
                    chat_id=cid, photo=bio, caption=caption,
                    reply_markup=reply_markup,
                )
            except Exception as e:
                try:
                    self.app.after(0, lambda err=e, c=cid: self.log(
                        f"Telegram: send to {c} failed: {err}", "#da3633"))
                except Exception:
                    pass

    # -------- token test (used by the "Test & Save" button) --------
    def test_token(self, token, callback):
        """Run a non-blocking getMe() call to validate a token.
        callback(success: bool, info: str) is called when done."""
        def _worker():
            try:
                import requests
                r = requests.get(
                    f"https://api.telegram.org/bot{token}/getMe", timeout=10
                )
                if r.status_code == 200 and r.json().get("ok"):
                    bot_info = r.json()["result"]
                    name = bot_info.get("username", "unknown")
                    callback(True, f"@{name}")
                else:
                    callback(False, f"HTTP {r.status_code}: {r.text[:100]}")
            except Exception as e:
                callback(False, str(e))
        threading.Thread(target=_worker, daemon=True).start()
