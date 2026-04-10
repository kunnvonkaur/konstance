"""
telegram_window.py - Configuration window for Telegram bot integration.
Mirrors the style/structure of bed_mesh_manager.py (CTkToplevel, centered
on parent, dark theme).
"""

import customtkinter as ctk
import webbrowser


class TelegramWindow(ctk.CTkToplevel):
    def __init__(self, master, telegram_manager, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.tm = telegram_manager
        self.app = master

        self.title("Telegram Remote")
        self.configure(fg_color="#161b22")

        popup_w = 620
        popup_h = 780
        self.update_idletasks()
        app_x = master.winfo_rootx()
        app_y = master.winfo_rooty()
        app_w = master.winfo_width()
        app_h = master.winfo_height()
        center_x = app_x + (app_w // 2) - (popup_w // 2)
        center_y = app_y + (app_h // 2) - (popup_h // 2)
        self.geometry(f"{popup_w}x{popup_h}+{center_x}+{center_y}")

        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

        self._token_visible = False
        self._capture_armed = False

        self._build_ui()
        self._refresh_status()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------- UI construction --------
    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Telegram Remote Control",
            font=ctk.CTkFont(size=18, weight="bold"), text_color="#10D0DE"
        ).pack(pady=(15, 2))
        ctk.CTkLabel(
            self,
            text="Receive AI anomaly alerts on Telegram and control your printer remotely.",
            font=ctk.CTkFont(size=10), text_color="#8b949e"
        ).pack(pady=(0, 10))

        # -------- Section 1: Bot Token --------
        sec1 = self._section("1. Bot Token")

        help_frame = ctk.CTkFrame(sec1, fg_color="transparent")
        help_frame.pack(fill="x", padx=10, pady=(5, 2))
        ctk.CTkLabel(
            help_frame,
            text="Talk to @BotFather on Telegram, create a new bot, and paste its token below.",
            font=ctk.CTkFont(size=10), text_color="#8b949e", justify="left"
        ).pack(side="left", anchor="w")
        botfather_lbl = ctk.CTkLabel(
            help_frame, text="Open @BotFather",
            font=ctk.CTkFont(size=10, underline=True),
            text_color="#1f6feb", cursor="hand2"
        )
        botfather_lbl.pack(side="right", padx=10)
        botfather_lbl.bind(
            "<Button-1>",
            lambda e: webbrowser.open("https://t.me/BotFather")
        )

        token_frame = ctk.CTkFrame(sec1, fg_color="transparent")
        token_frame.pack(fill="x", padx=10, pady=5)

        self.token_entry = ctk.CTkEntry(
            token_frame, placeholder_text="123456789:ABCdefGhIJKlmnOPQRstUVwxYZ",
            show="*", width=380
        )
        self.token_entry.pack(side="left", padx=(0, 5))
        if self.tm.token:
            self.token_entry.insert(0, self.tm.token)

        self.btn_show = ctk.CTkButton(
            token_frame, text="👁", width=30, fg_color="#21262d",
            hover_color="#30363d", command=self._toggle_token_visibility
        )
        self.btn_show.pack(side="left", padx=2)

        self.btn_test = ctk.CTkButton(
            token_frame, text="Test & Save", width=100,
            fg_color="#1f6feb", hover_color="#388bfd",
            command=self._on_test_token
        )
        self.btn_test.pack(side="left", padx=2)

        self.lbl_token_status = ctk.CTkLabel(
            sec1, text="", font=ctk.CTkFont(size=10), text_color="#8b949e"
        )
        self.lbl_token_status.pack(padx=10, pady=(0, 5), anchor="w")

        # -------- Section 2: Authorized Chat IDs --------
        sec2 = self._section("2. Authorized Users (Chat IDs)")

        ctk.CTkLabel(
            sec2,
            text="Only these chat IDs can receive alerts and send commands.",
            font=ctk.CTkFont(size=10), text_color="#8b949e"
        ).pack(padx=10, pady=(5, 2), anchor="w")

        # List display
        self.chat_list_frame = ctk.CTkScrollableFrame(
            sec2, fg_color="#0d1117", height=100
        )
        self.chat_list_frame.pack(fill="x", padx=10, pady=5)

        # Auto-capture row
        cap_frame = ctk.CTkFrame(sec2, fg_color="transparent")
        cap_frame.pack(fill="x", padx=10, pady=2)

        self.btn_capture = ctk.CTkButton(
            cap_frame, text="🎯 Auto-capture next user",
            fg_color="#238636", hover_color="#2ea043",
            command=self._on_toggle_capture
        )
        self.btn_capture.pack(side="left", padx=(0, 5))

        self.lbl_capture_hint = ctk.CTkLabel(
            cap_frame,
            text="(Click, then message your bot on Telegram)",
            font=ctk.CTkFont(size=10), text_color="#8b949e"
        )
        self.lbl_capture_hint.pack(side="left", padx=5)

        # Manual entry row
        man_frame = ctk.CTkFrame(sec2, fg_color="transparent")
        man_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.manual_entry = ctk.CTkEntry(
            man_frame, placeholder_text="Or paste numeric chat ID manually",
            width=350
        )
        self.manual_entry.pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            man_frame, text="Add", width=80,
            fg_color="#1f6feb", hover_color="#388bfd",
            command=self._on_add_manual
        ).pack(side="left", padx=2)

        # -------- Section 3: Alert Settings --------
        sec3 = self._section("3. Alert Settings")

        deb_frame = ctk.CTkFrame(sec3, fg_color="transparent")
        deb_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            deb_frame, text="Min. seconds between alerts:",
            font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 10))

        self.debounce_var = ctk.IntVar(value=self.tm.debounce_seconds)
        self.debounce_slider = ctk.CTkSlider(
            deb_frame, from_=10, to=600, number_of_steps=59,
            variable=self.debounce_var,
            command=self._on_debounce_change
        )
        self.debounce_slider.pack(side="left", padx=5, fill="x", expand=True)

        self.lbl_debounce = ctk.CTkLabel(
            deb_frame, text=f"{self.tm.debounce_seconds}s",
            width=50, font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10D0DE"
        )
        self.lbl_debounce.pack(side="left", padx=5)

        ctk.CTkLabel(
            sec3,
            text="Debounce prevents spam — one print can't send 50 photos in 10 seconds.",
            font=ctk.CTkFont(size=9), text_color="#8b949e"
        ).pack(padx=10, pady=(0, 5), anchor="w")

        auto_frame = ctk.CTkFrame(sec3, fg_color="transparent")
        auto_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.auto_start_switch = ctk.CTkSwitch(
            auto_frame, text="Start bot automatically when app launches",
            command=self._on_auto_start_toggle
        )
        self.auto_start_switch.pack(side="left")
        if self.tm.auto_start:
            self.auto_start_switch.select()
        else:
            self.auto_start_switch.deselect()

        # -------- Section 4: Bot Control --------
        sec4 = self._section("4. Bot Status")

        ctrl_frame = ctk.CTkFrame(sec4, fg_color="transparent")
        ctrl_frame.pack(fill="x", padx=10, pady=10)

        self.status_dot = ctk.CTkLabel(
            ctrl_frame, text="●", font=ctk.CTkFont(size=24),
            text_color="#8b949e", width=30
        )
        self.status_dot.pack(side="left", padx=5)

        self.lbl_status = ctk.CTkLabel(
            ctrl_frame, text="Bot stopped",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.lbl_status.pack(side="left", padx=5)

        self.btn_start_stop = ctk.CTkButton(
            ctrl_frame, text="Start Bot", fg_color="#238636",
            hover_color="#2ea043", command=self._on_start_stop
        )
        self.btn_start_stop.pack(side="right", padx=5)

        self.btn_test_photo = ctk.CTkButton(
            ctrl_frame, text="📸 Send test photo", fg_color="#1f6feb",
            hover_color="#388bfd", command=self._on_test_photo
        )
        self.btn_test_photo.pack(side="right", padx=5)

        # Initial list populate
        self._refresh_chat_list()

    def _section(self, title):
        wrap = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=6)
        wrap.pack(fill="x", padx=15, pady=6)
        ctk.CTkLabel(
            wrap, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#10D0DE"
        ).pack(padx=10, pady=(8, 2), anchor="w")
        return wrap

    # -------- UI event handlers --------
    def _toggle_token_visibility(self):
        self._token_visible = not self._token_visible
        self.token_entry.configure(show="" if self._token_visible else "*")

    def _on_test_token(self):
        token = self.token_entry.get().strip()
        if not token:
            self.lbl_token_status.configure(
                text="❌ Token is empty.", text_color="#da3633"
            )
            return
        self.lbl_token_status.configure(
            text="⏳ Testing token...", text_color="#d29922"
        )
        self.btn_test.configure(state="disabled")

        def _result(success, info):
            self.after(0, lambda: self._on_token_result(success, info, token))

        self.tm.test_token(token, _result)

    def _on_token_result(self, success, info, token):
        self.btn_test.configure(state="normal")
        if success:
            self.tm.token = token
            self.tm.save_config()
            self.lbl_token_status.configure(
                text=f"✅ Token valid. Bot: {info} — starting...",
                text_color="#3fb950"
            )
            # Auto-start the bot now that we have a working token.
            # Without this, users had to manually tap Start Bot and got
            # confused when Auto-capture said "start the bot first".
            if not self.tm.is_running:
                def _do_start():
                    ok = self.tm.start()
                    self.after(0, lambda: self._on_auto_start_after_test(ok, info))
                import threading
                threading.Thread(target=_do_start, daemon=True).start()
            else:
                self._refresh_status()
                if hasattr(self.app, "on_telegram_state_changed"):
                    self.app.on_telegram_state_changed()
        else:
            self.lbl_token_status.configure(
                text=f"❌ Invalid: {info}", text_color="#da3633"
            )

    def _on_auto_start_after_test(self, ok, bot_name):
        if ok:
            self.lbl_token_status.configure(
                text=f"✅ Bot {bot_name} is live. You can add users below.",
                text_color="#3fb950"
            )
        else:
            self.lbl_token_status.configure(
                text="⚠️ Token saved but bot failed to start. Tap Start Bot manually.",
                text_color="#d29922"
            )
        self._refresh_status()
        if hasattr(self.app, "on_telegram_state_changed"):
            self.app.on_telegram_state_changed()

    def _refresh_chat_list(self):
        for w in self.chat_list_frame.winfo_children():
            w.destroy()
        if not self.tm.authorized_chat_ids:
            ctk.CTkLabel(
                self.chat_list_frame, text="No users authorized yet.",
                text_color="#8b949e", font=ctk.CTkFont(size=11)
            ).pack(pady=10)
            return
        for cid in list(self.tm.authorized_chat_ids):
            row = ctk.CTkFrame(self.chat_list_frame, fg_color="#161b22")
            row.pack(fill="x", pady=2, padx=2)
            ctk.CTkLabel(
                row, text=f"👤 {cid}",
                font=ctk.CTkFont(size=11, family="Consolas"), anchor="w"
            ).pack(side="left", padx=10, pady=5)
            ctk.CTkButton(
                row, text="Remove", width=70, height=22,
                fg_color="#a40e26", font=ctk.CTkFont(size=10),
                command=lambda c=cid: self._on_remove(c)
            ).pack(side="right", padx=5, pady=2)

    def _on_remove(self, chat_id):
        self.tm.remove_chat_id(chat_id)
        self._refresh_chat_list()

    def _on_add_manual(self):
        txt = self.manual_entry.get().strip()
        if not txt:
            return
        try:
            cid = int(txt)
        except ValueError:
            self.lbl_token_status.configure(
                text="❌ Chat ID must be a number.", text_color="#da3633"
            )
            return
        if self.tm.add_chat_id(cid):
            self.manual_entry.delete(0, "end")
            self._refresh_chat_list()

    def _on_toggle_capture(self):
        # If the bot isn't running yet, try to start it from whatever's in the token field.
        # Previously this just said "Start the bot first!" which confused users.
        if not self.tm.is_running:
            token_in_field = self.token_entry.get().strip()
            saved_token = (self.tm.token or "").strip()

            if not token_in_field and not saved_token:
                self.lbl_capture_hint.configure(
                    text="⚠️ Enter your bot token first (Section 1).",
                    text_color="#da3633"
                )
                return

            # If user typed a new token but didn't click Test & Save, save it now
            if token_in_field and token_in_field != saved_token:
                self.tm.token = token_in_field
                self.tm.save_config()

            # Try to start the bot silently
            self.lbl_capture_hint.configure(
                text="⏳ Starting bot...", text_color="#d29922"
            )
            import threading
            def _do_start():
                ok = self.tm.start()
                self.after(0, lambda: self._after_autostart_for_capture(ok))
            threading.Thread(target=_do_start, daemon=True).start()
            return

        # Normal path: bot is running, arm or cancel capture
        self._toggle_capture_armed()

    def _after_autostart_for_capture(self, ok):
        """Called after trying to auto-start the bot from the capture button."""
        self._refresh_status()
        if hasattr(self.app, "on_telegram_state_changed"):
            try:
                self.app.on_telegram_state_changed()
            except Exception:
                pass
        if ok:
            # Bot is now running, arm capture immediately so the user doesn't
            # have to tap the button a second time
            self._toggle_capture_armed()
        else:
            self.lbl_capture_hint.configure(
                text="❌ Bot failed to start. Check token in Section 1.",
                text_color="#da3633"
            )

    def _toggle_capture_armed(self):
        """The original arm/disarm logic, factored out so auto-start can call it."""
        if self._capture_armed:
            self.tm.cancel_capture()
            self._capture_armed = False
            self.btn_capture.configure(
                text="🎯 Auto-capture next user", fg_color="#238636"
            )
            self.lbl_capture_hint.configure(
                text="(Click, then message your bot on Telegram)",
                text_color="#8b949e"
            )
        else:
            self.tm.capture_next_chat_id(self._on_captured)
            self._capture_armed = True
            self.btn_capture.configure(
                text="⏳ Waiting... (click to cancel)", fg_color="#d29922"
            )
            self.lbl_capture_hint.configure(
                text="Now open Telegram and send any message to your bot.",
                text_color="#d29922"
            )

    def _on_captured(self, chat_id, username):
        # Called from bot thread — bounce to Tk main thread
        self.after(0, lambda: self._show_capture_confirm(chat_id, username))

    def _show_capture_confirm(self, chat_id, username):
        self._capture_armed = False
        self.tm.cancel_capture()
        self.btn_capture.configure(
            text="🎯 Auto-capture next user", fg_color="#238636"
        )

        dlg = ctk.CTkToplevel(self)
        dlg.title("Authorize user?")
        dlg.configure(fg_color="#161b22")
        dlg.geometry("400x180")
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(
            dlg, text="New user wants access:",
            font=ctk.CTkFont(size=12)
        ).pack(pady=(20, 5))
        ctk.CTkLabel(
            dlg, text=f"@{username}",
            font=ctk.CTkFont(size=14, weight="bold"), text_color="#10D0DE"
        ).pack()
        ctk.CTkLabel(
            dlg, text=f"Chat ID: {chat_id}",
            font=ctk.CTkFont(size=11, family="Consolas"), text_color="#8b949e"
        ).pack(pady=5)

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(pady=10)

        def _approve():
            self.tm.add_chat_id(chat_id)
            self._refresh_chat_list()
            self.lbl_capture_hint.configure(
                text=f"✅ Added {username}!", text_color="#3fb950"
            )
            dlg.destroy()

        def _reject():
            self.lbl_capture_hint.configure(
                text="(Click, then message your bot on Telegram)",
                text_color="#8b949e"
            )
            dlg.destroy()

        ctk.CTkButton(
            btns, text="✅ Approve", fg_color="#238636",
            command=_approve
        ).pack(side="left", padx=10)
        ctk.CTkButton(
            btns, text="❌ Reject", fg_color="#a40e26",
            command=_reject
        ).pack(side="left", padx=10)

    def _on_debounce_change(self, val):
        v = int(float(val))
        self.tm.debounce_seconds = v
        self.lbl_debounce.configure(text=f"{v}s")
        self.tm.save_config()

    def _on_auto_start_toggle(self):
        self.tm.auto_start = bool(self.auto_start_switch.get())
        self.tm.save_config()

    def _on_start_stop(self):
        if self.tm.is_running:
            self.tm.stop()
            self._refresh_status()
        else:
            token = self.token_entry.get().strip()
            if token != self.tm.token:
                self.tm.token = token
                self.tm.save_config()
            ok = self.tm.start()
            if not ok:
                self.lbl_token_status.configure(
                    text="❌ Bot failed to start. Check token.",
                    text_color="#da3633"
                )
            self._refresh_status()
            # Notify main window so it can enable the Telegram Warn switch
            if hasattr(self.app, "on_telegram_state_changed"):
                self.app.on_telegram_state_changed()

    def _on_test_photo(self):
        frame = None
        lock = getattr(self.app, "latest_frame_lock", None)
        if lock is not None:
            with lock:
                if getattr(self.app, "latest_frame", None) is not None:
                    frame = self.app.latest_frame.copy()
        if frame is None:
            self.lbl_token_status.configure(
                text="❌ No camera frame available yet.",
                text_color="#da3633"
            )
            return
        if self.tm.send_test_photo(frame):
            self.lbl_token_status.configure(
                text="✅ Test photo sent!", text_color="#3fb950"
            )
        else:
            self.lbl_token_status.configure(
                text="❌ Failed to send test photo.", text_color="#da3633"
            )

    def _refresh_status(self):
        if self.tm.is_running:
            self.status_dot.configure(text_color="#3fb950")
            self.lbl_status.configure(text="Bot running")
            self.btn_start_stop.configure(text="Stop Bot", fg_color="#a40e26")
        else:
            self.status_dot.configure(text_color="#8b949e")
            self.lbl_status.configure(text="Bot stopped")
            self.btn_start_stop.configure(text="Start Bot", fg_color="#238636")

    def on_close(self):
        if self._capture_armed:
            self.tm.cancel_capture()
        self.destroy()
