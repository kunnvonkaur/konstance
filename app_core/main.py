import os
import sys
import json
import customtkinter as ctk

ctk.set_appearance_mode("dark")

import tkinter as tk
import cv2
import numpy as np
import threading
import time
import psutil
import gc
import socket 
import paramiko
import webbrowser
import requests
from datetime import datetime, timedelta
from PIL import Image
from ultralytics import YOLO

APP_VERSION = 0.1

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"

import viewer_app  

from protocol import CentauriProtocol
from vision import CentauriVision
from hardware_control import HardwareController
from file_manager import CentauriFileManager
from telegram_manager import TelegramManager

def get_user_data_dir():
    app_data = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
    base_dir = os.path.join(app_data, "KonstanceWatchdog")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

class CTkAccordion(ctk.CTkFrame):
    def __init__(self, master, title, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.is_open = False
        self.btn = ctk.CTkButton(
            self, text=f"▶  {title}", anchor="w", fg_color="#21262d", 
            hover_color="#30363d", command=self.toggle
        )
        self.btn.pack(fill="x")
        self.content = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=5)
        
    def toggle(self):
        self.is_open = not self.is_open
        if self.is_open:
            self.btn.configure(text=self.btn.cget("text").replace("▶", "▼"))
            self.content.pack(fill="x", pady=2, ipady=5)
        else:
            self.btn.configure(text=self.btn.cget("text").replace("▼", "▶"))
            self.content.pack_forget()
            

class OCToolTip:
    _tw = None
    _lbl = None
    _active_widget = None

    def __init__(self, widget, app):
        self.widget = widget
        self.app = app
        self.widget.bind("<Enter>", self.enter, add="+")
        self.widget.bind("<Leave>", self.leave, add="+")
        self.widget.bind("<Motion>", self.motion, add="+")

    @classmethod
    def get_tw(cls, master):
        if cls._tw is None:
            cls._tw = tk.Toplevel(master)
            cls._tw.wm_overrideredirect(True)
            cls._tw.attributes('-topmost', True)
            cls._lbl = tk.Label(cls._tw, text=" 🔒 OC Mode Only ", bg="#da3633", fg="white", font=("Consolas", 9, "bold"))
            cls._lbl.pack()
            cls._tw.withdraw()
        return cls._tw

    def enter(self, event):
        if getattr(self.app, 'oc_mode_active', False) or str(self.widget.cget("state")) == "normal":
            return
        tw = self.get_tw(self.app)
        OCToolTip._active_widget = self.widget
        tw.wm_geometry(f"+{event.x_root + 15}+{event.y_root + 15}")
        tw.deiconify()
        self.check_mouse()

    def motion(self, event):
        if OCToolTip._active_widget == self.widget:
            tw = self.get_tw(self.app)
            tw.wm_geometry(f"+{event.x_root + 15}+{event.y_root + 15}")

    def leave(self, event):
        if OCToolTip._active_widget == self.widget:
            OCToolTip._active_widget = None
            self.get_tw(self.app).withdraw()

    def check_mouse(self):
        if OCToolTip._active_widget == self.widget:
            try:
                mx, my = self.widget.winfo_pointerxy()
                wx = self.widget.winfo_rootx()
                wy = self.widget.winfo_rooty()
                ww = self.widget.winfo_width()
                wh = self.widget.winfo_height()
                if not (wx <= mx <= wx + ww and wy <= my <= wy + wh):
                    self.leave(None)
                else:
                    self.widget.after(50, self.check_mouse)
            except:
                self.leave(None)

class CentauriWatchdog(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.oc_mode_active = False  
        self.title("Konstance the Watchdog v0.1")
        
        self.geometry("1240x1050")
        
        self.is_monitoring = False
        self.is_connecting = False
        self.dot_count = 0
        self.cooldown_active = False
        self.sidebar_visible = False
        self.move_step = 10.0
        
        self.vid_offset_x = 0
        self.vid_offset_y = 0
        self.vid_fw = 1920
        self.vid_fh = 1080
        
        self.last_ping_time = time.time()
        self.watchdog_running = False
        
        self.konstance_active = False
        self.model_loaded = False
        self.model = None
        self.model_map = {}
        self.get_available_models()
        self.strike_counter = 0
        self.last_detection_time = 0
        
        self.stat_print_start_time = 0
        self.stat_total_anomalies = 0
        self.stat_auto_pauses = 0
        self.stat_final_extrusion = 0.0
        
        self.speed_confirm_state = 0 
        self.current_process = psutil.Process(os.getpid())       
        self.is_custom_leveling = False
        self.current_chamber_temp = 20.0
        
        self.ignore_zones = []
        self.draw_mode = False
        self.is_dragging = False
        self.current_drawing = None
        self.frame_width = 1920
        self.frame_height = 1080
        self.led_state = False
        self.last_status = "OFFLINE"

        # Load persistent ignore zones (saved across app restarts).
        # Stored in user_dir/ignore_zones.json as a list of [x1,y1,x2,y2] in
        # frame pixel space, so they stay anchored to the same camera spot
        # regardless of window size.
        try:
            zones_path = os.path.join(get_user_data_dir(), "ignore_zones.json")
            if os.path.exists(zones_path):
                with open(zones_path, "r", encoding="utf-8") as zf:
                    loaded = json.load(zf)
                    if isinstance(loaded, list):
                        self.ignore_zones = [tuple(z) for z in loaded if len(z) == 4]
        except Exception:
            pass

        # --- Temperature cache (so Telegram /temps can read them) ---
        self.temp_current_nozzle = 0.0
        self.temp_target_nozzle = 0.0
        self.temp_current_bed = 0.0
        self.temp_target_bed = 0.0
        self.temp_current_chamber = 0.0

        # --- Preheat state ---
        self.preheat_active = False
        self.preheat_end_ts = 0
        self.preheat_timer_id = None         # Tk after() id for the cooldown timer
        self.preheat_target_nozzle = 0.0     # desired temps while preheat is active
        self.preheat_target_bed = 0.0
        self.preheat_watchdog_id = None      # Tk after() id for the re-apply watchdog
        self.preheat_ready_notified = False  # so we only say "ready" once per session
        # --- end preheat ---

        # --- Telegram integration: shared latest annotated frame ---
        self.latest_frame = None
        self.latest_frame_lock = threading.Lock()
        self.cached_file_list = []  # last-received list from cmd 258, used by Telegram /files
        # Print progress cache for Telegram /status
        self.print_progress_pct = 0.0
        self.print_current_layer = 0
        self.print_total_layer = 0
        self.print_current_ticks = 0
        self.print_total_ticks = 0
        self.print_filename = ""
        # --- end telegram ---
        
        self.is_custom_leveling = 0
        self._lvl_idle_streak = 0
        self.mesh_verified_for_print = False 
        
        self.file_manager = None
        self.fan_states = {"ModelFan": 0, "AuxiliaryFan": 0, "BoxFan": 0}
        self.filter_states = {
            "Enabled": False, "Grayscale": False, "CLAHE": False, "Edge": False,
            "Bright": 0, "Contrast": 1.0, "Gamma": 1.0, "Black": 0
        }
        self.ssh_ranges = {
            "saturation": (0, 255), "contrast": (0, 255), "hue": (-180, 180),
            "gain": (4, 8), "sharpness": (0, 7), "exposure_absolute": (10, 2500)
        }
        self.ssh_defaults = {
            "saturation": 64, "contrast": 32, "hue": 0, "gain": 4, "sharpness": 2
        }
        self.hw = None 
        self.status_map = {
            0: "Idle", 1: "Preparing", 5: "Pausing", 6: "Paused", 
            7: "Stopping", 8: "Stopped", 9: "Print Complete", 13: "Printing", 
            16: "Preparing", 20: "Preparing"
        }
        self.color_palette = {
            "Idle": "#10D0DE", "Preparing": "#107EDE", "Printing": "#001DBA", 
            "Pausing": "#F2F538", "Paused": "#EDAF00", "Stopping": "#F26F6F", 
            "Stopped": "#D11919", "Homing": "#F33A9E", "Print Complete": "#3fb950",
            "OFFLINE": "#8b949e", "Leveling": "#8957e5"
        }

        self.user_dir = get_user_data_dir()
        os.makedirs(os.path.join(self.user_dir, "temp_logs/Traffic"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "temp_logs/AI"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "temp_logs/App"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "temp_logs/History"), exist_ok=True)
        self.cleanup_old_logs()

        # --- Telegram integration ---
        self.telegram = TelegramManager(self.user_dir, self._deferred_log, self)
        self.telegram.load_config()
        self.telegram_window = None
        # --- end telegram ---

        self.setup_ui()
        threading.Thread(target=self.load_model, daemon=True).start()
        self.update_pc_stats() 
        self.set_ui_state("disabled")

        # --- Telegram: auto-start bot if configured ---
        if self.telegram.auto_start and self.telegram.has_token:
            threading.Thread(target=self._telegram_auto_start, daemon=True).start()
        # --- end telegram ---

        self.protocol("WM_DELETE_WINDOW", self.on_app_close)
        
        

    def setup_ui(self):
        self.top_frame = ctk.CTkFrame(self, height=60)
        self.top_frame.pack(fill="x", padx=10, pady=10)
        
        self.ip_entry = ctk.CTkEntry(self.top_frame, placeholder_text="Printer IP", width=140)
        self.ip_entry.pack(side="left", padx=10)
        
        self.connect_btn = ctk.CTkButton(self.top_frame, text="🔌 Connect", fg_color="#238636", width=110, command=self.toggle_monitoring)
        self.connect_btn.pack(side="left", padx=5)
        
        self.temp_frame = ctk.CTkFrame(self.top_frame, fg_color="#0d1117", corner_radius=8)
        self.temp_frame.pack(side="left", padx=20)
        self.temp_widgets = {}
        self.create_temp_widget("Nozzle", True)
        self.create_temp_widget("Bed", True)
        self.create_temp_widget("Chamber", False) 
        
        hint_lbl = ctk.CTkLabel(self.temp_frame, text="double click to change target temps.", font=ctk.CTkFont(size=9), text_color="#8b949e")
        hint_lbl.pack(side="left", padx=10)

        self.mid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.mid_frame.pack(fill="both", expand=True, padx=10)
        self.mid_frame.grid_columnconfigure(0, weight=1)
        self.mid_frame.grid_columnconfigure(1, weight=0, minsize=320)
        self.mid_frame.grid_rowconfigure(0, weight=1)
        
        self.video_container = ctk.CTkFrame(self.mid_frame, fg_color="#010409", corner_radius=10)
        self.video_container.grid(row=0, column=0, sticky="nsew")
        self.video_label = ctk.CTkLabel(self.video_container, text="Offline")
        self.video_label.pack(expand=True, fill="both")
        self.video_label.bind("<ButtonPress-1>", self.on_mouse_down)
        self.video_label.bind("<B1-Motion>", self.on_mouse_drag)
        self.video_label.bind("<ButtonRelease-1>", self.on_mouse_up)
        
        self.sidebar_frame = ctk.CTkScrollableFrame(self.mid_frame, width=320, corner_radius=8, fg_color="#161b22")
        self.sidebar_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0)) 
        self.build_sidebar()

        self.dashboard_wrapper = ctk.CTkFrame(self)
        self.active_prog_container = ctk.CTkFrame(self.dashboard_wrapper, fg_color="transparent")
        self.active_prog_container.pack(fill="x", expand=True)
        
        self.prog_info_frame = ctk.CTkFrame(self.active_prog_container, fg_color="transparent")
        self.prog_info_frame.pack(side="left", fill="x", expand=True, padx=10, pady=5)
        
        self.file_lbl = ctk.CTkLabel(self.prog_info_frame, text="File: --", font=ctk.CTkFont(weight="bold", size=13), text_color="#10D0DE")
        self.file_lbl.pack(anchor="w")
        
        self.prog_bar = ctk.CTkProgressBar(self.prog_info_frame, progress_color="#1f6feb")
        self.prog_bar.set(0)
        self.prog_bar.pack(fill="x", pady=4)
        
        self.telemetry_lbl = ctk.CTkLabel(self.prog_info_frame, text="Layer: 0 / 0  |  Progress: 0.0%    |    Model Fan: 0%  Aux Fan: 0%  Box Fan: 0%    |    Extruded: 0mm    |    XYZ: 0, 0, 0", font=ctk.CTkFont(size=11), text_color="#8b949e")
        self.telemetry_lbl.pack(anchor="w")
        
        self.speed_frame = ctk.CTkFrame(self.active_prog_container, fg_color="transparent")
        self.speed_frame.pack(side="right", padx=10)
        
        self.speed_var = ctk.StringVar(value="Normal (100%)")
        self.speed_opts = ["Slow as Bambu (25%)", "Silent (50%)", "Normal (100%)", "Sport (130%)", "Ludicrous (160%)", "Pimped Ender speed (200%)"]
        self.speed_menu = ctk.CTkOptionMenu(self.speed_frame, values=self.speed_opts, variable=self.speed_var, command=self.handle_speed_change, width=180)
        self.speed_menu.pack(side="left")
        
        self.speed_warn_lbl = ctk.CTkLabel(self.speed_frame, text="⚠️", text_color="#8b949e")
        self.speed_warn_lbl.pack(side="left", padx=5)
        
        self.speed_confirm_btn = ctk.CTkButton(self.speed_frame, text="CONFIRM 200%", fg_color="#a40e26", width=110, command=self.confirm_200_speed)
        self.speed_cancel_btn = ctk.CTkButton(self.speed_frame, text="Cancel", fg_color="#30363d", width=60, command=self.cancel_200_speed)

        self.summary_container = ctk.CTkFrame(self.dashboard_wrapper, fg_color="#161b22", corner_radius=8)
        ctk.CTkLabel(self.summary_container, text="Print completed", font=ctk.CTkFont(weight="bold", size=15), text_color="#3fb950").pack(pady=(10, 5))
        self.sum_time_lbl = ctk.CTkLabel(self.summary_container, text="Actual print time was: --", font=ctk.CTkFont(size=12))
        self.sum_time_lbl.pack()
        self.sum_anom_lbl = ctk.CTkLabel(self.summary_container, text="Number of anomalies during print: --", font=ctk.CTkFont(size=12))
        self.sum_anom_lbl.pack()
        self.sum_pause_lbl = ctk.CTkLabel(self.summary_container, text="Auto-stop trigger times: --", font=ctk.CTkFont(size=12))
        self.sum_pause_lbl.pack()
        self.sum_ext_lbl = ctk.CTkLabel(self.summary_container, text="Total Extrusion: --", font=ctk.CTkFont(size=12))
        self.sum_ext_lbl.pack(pady=(0, 10))

        self.control_frame = ctk.CTkFrame(self)
        self.control_frame.pack(fill="x", padx=10, pady=5)
        self.state_lbl = ctk.CTkLabel(self.control_frame, text="Printer: OFFLINE", font=ctk.CTkFont(size=14, weight="bold"))
        self.state_lbl.pack(side="left", padx=15)
        self.ready_print_lbl = ctk.CTkLabel(self.control_frame, text="", text_color="#3fb950", font=ctk.CTkFont(size=11, weight="bold"))
        self.ready_print_lbl.pack(side="left", padx=5)
        
        self.btn_sub = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        self.btn_sub.pack(side="left", padx=15)
        
        self.pause_btn = ctk.CTkButton(self.btn_sub, text="Pause", fg_color="#d29922", width=85, state="disabled", command=lambda: self.trigger_action("pause"))
        self.pause_btn.pack(side="left", padx=2)
        
        self.resume_btn = ctk.CTkButton(self.btn_sub, text="Resume", fg_color="#8957e5", width=85, state="disabled", command=lambda: self.trigger_action("resume"))
        self.resume_btn.pack(side="left", padx=2)
        
        self.stop_btn = ctk.CTkButton(self.btn_sub, text="Stop", fg_color="#a40e26", width=85, state="disabled", command=self.show_stop_confirm)
        self.stop_btn.pack(side="left", padx=2)
        self.confirm_frame = ctk.CTkFrame(self.btn_sub, fg_color="#a40e26", corner_radius=5)
        ctk.CTkButton(self.confirm_frame, text="CONFIRM STOP?", fg_color="transparent", command=lambda: self.trigger_action("stop")).pack(side="left")
        ctk.CTkButton(self.confirm_frame, text="X", width=20, fg_color="transparent", command=self.hide_stop_confirm).pack(side="left")
        
        self.active_mesh_status_lbl = ctk.CTkLabel(
            self.control_frame, 
            text="(No pre-probed mesh initialized)", 
            text_color="#8b949e", 
            font=ctk.CTkFont(size=11, weight="bold")
        )
        
        self.led_btn = ctk.CTkButton(self.control_frame, text="💡 LED OFF", fg_color="#21262d", width=100, command=self.toggle_led)
        self.led_btn.pack(side="right", padx=10)
        
        self.refresh_btn = ctk.CTkButton(self.control_frame, text="🔄 Refresh", fg_color="#1f6feb", width=100, command=self.manual_refresh)
        self.refresh_btn.pack(side="right", padx=5)

        self.ai_frame = ctk.CTkFrame(self)
        self.ai_frame.pack(fill="x", padx=10, pady=10)
        
        master_ai_col = ctk.CTkFrame(self.ai_frame, fg_color="transparent")
        master_ai_col.pack(side="left", padx=10)
        
        self.konstance_btn = ctk.CTkButton(master_ai_col, text="Konstance: LOADING", fg_color="#21262d", text_color="#d29922", width=140, state="disabled", command=self.toggle_konstance)
        self.konstance_btn.pack(pady=(0, 5))

        m_names = list(self.model_map.keys())
        self.model_menu = ctk.CTkOptionMenu(
            master_ai_col, 
            values=m_names if m_names else ["No Models Found"],
            command=self.reload_model_event,
            width=220, 
            fg_color="#30363d",
            button_color="#21262d",
            anchor="center"
        )
        if "Konstance_light_openvino_model" in m_names:
            self.model_menu.set("Konstance_light_openvino_model")
        elif m_names:
            self.model_menu.set(m_names[0])
            
        self.model_menu.pack(pady=5)
        
        ctk.CTkLabel(master_ai_col, text="Konstance = AI model", font=ctk.CTkFont(size=9), text_color="#8b949e").pack()
        
        self.auto_pause_switch = ctk.CTkSwitch(master_ai_col, text="Auto-Pause", progress_color="#da3633", state="disabled")
        self.auto_pause_switch.select()
        self.auto_pause_switch.pack(pady=5)

        # --- Telegram Warn switch (independent from Auto-Pause) ---
        self.telegram_warn_switch = ctk.CTkSwitch(master_ai_col, text="Telegram Warn", progress_color="#1f6feb", state="disabled")
        self.telegram_warn_switch.deselect()
        self.telegram_warn_switch.pack(pady=5)
        # --- end telegram ---
        
        s_cont = ctk.CTkFrame(self.ai_frame, fg_color="transparent")
        s_cont.pack(side="left", padx=20)
        
        self.conf_slider, self.conf_ent = self.create_ai_row(s_cont, "Confidence", 0, 10, 95, 50, "%")
        self.scan_slider, self.scan_ent = self.create_ai_row(s_cont, "Intensity", 1, 0.5, 10.0, 3.0, "s")
        self.strike_slider, self.strike_ent = self.create_ai_row(s_cont, "Confirmations", 2, 1, 20, 3, "x", steps=19)

        btn_ai_col = ctk.CTkFrame(self.ai_frame, fg_color="transparent")
        btn_ai_col.pack(side="right", padx=5)
        self.draw_btn = ctk.CTkButton(btn_ai_col, text="Draw ignore zone", fg_color="#1f6feb", width=120, command=self.toggle_draw_mode)
        self.draw_btn.pack(pady=2)
        ctk.CTkButton(btn_ai_col, text="Clear all zones", fg_color="#21262d", width=120, command=self._clear_all_zones).pack(pady=2)
        ctk.CTkButton(btn_ai_col, text="Clear last zone", fg_color="#21262d", width=120, command=self.undo_zone).pack(pady=2)

        self.bottom_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_bar.pack(side="bottom", fill="x", padx=15, pady=5)
        
        self.status_label = ctk.CTkLabel(self.bottom_bar, text="Console: Ready", text_color="#8b949e", font=ctk.CTkFont(family="Consolas", size=11))
        self.status_label.pack(side="left")

        self.stats_frame = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.stats_frame.pack(side="right")
        
        self.app_cpu_lbl = ctk.CTkLabel(self.stats_frame, text="App CPU: --%", font=ctk.CTkFont(size=11))
        self.app_cpu_lbl.pack(side="left", padx=(0, 5))
        self.app_ram_lbl = ctk.CTkLabel(self.stats_frame, text="App RAM: -- MB", font=ctk.CTkFont(size=11))
        self.app_ram_lbl.pack(side="left", padx=(0, 15))
        self.cpu_lbl = ctk.CTkLabel(self.stats_frame, text="💻 PC CPU: --%", font=ctk.CTkFont(size=11, weight="bold"))
        self.cpu_lbl.pack(side="left", padx=(0, 10))
        self.ram_lbl = ctk.CTkLabel(self.stats_frame, text="🧠 PC RAM: --%", font=ctk.CTkFont(size=11, weight="bold"))
        self.ram_lbl.pack(side="left")
        
        
    def check_for_updates(self):
        def _task():
            try:
                # Ask the official API instead of the raw CDN server
                url = "https://api.github.com/repos/kunnvonkaur/konstance/contents/version"
                
                # This magic header tells the API: "Don't send me JSON data, just give me the raw text inside the file instantly."
                headers = {
                    "Accept": "application/vnd.github.v3.raw",
                    "Cache-Control": "no-cache"
                }
                
                resp = requests.get(url, headers=headers, timeout=5)
                
                if resp.status_code == 200:
                    latest_version = float(resp.text.strip())
                    
                    if latest_version > APP_VERSION:
                        self.after(0, lambda: [
                            self.btn_update.configure(
                                state="normal", 
                                text="New version of Konstance is available!", 
                                fg_color="#1f6feb", 
                                text_color="#ffffff",
                                hover_color="#388bfd"
                            )
                        ])
                    else:
                        self.after(0, lambda: [
                            self.btn_update.configure(
                                state="disabled", 
                                text="Konstance is up to date", 
                                fg_color="#21262d", 
                                text_color="#8b949e"
                            )
                        ])
            except Exception:
                pass 
        
        threading.Thread(target=_task, daemon=True).start()

    def open_update_link(self):
        webbrowser.open("https://github.com/kunnvonkaur/konstance/releases")

    def show_licenses(self):
        lic_win = ctk.CTkToplevel(self)
        lic_win.title("Open Source Licenses")
        lic_win.geometry("550x450")
        lic_win.attributes("-topmost", True)
        
        tb = ctk.CTkTextbox(lic_win, wrap="word", font=ctk.CTkFont(family="Consolas", size=11), fg_color="#0d1117")
        tb.pack(fill="both", expand=True, padx=10, pady=10)
        
        licenses_text = (
            "KONSTANCE SOFTWARE LICENSES\n"
            "===========================\n\n"
            "This software is made possible by the following open-source projects:\n\n"
            "1. CustomTkinter\nMIT License. Copyright (c) 2023 Tom Schimansky\n\n"
            "2. OpenCV (cv2)\nApache License 2.0. Copyright (C) 2000-2023, Intel Corporation, all rights reserved.\n\n"
            "3. Ultralytics (YOLO)\nAGPL-3.0 License. Copyright (c) 2023 Ultralytics Inc.\n\n"
            "4. Paramiko\nLGPL License. Copyright (c) 2003-2007 Robey Pointer\n\n"
            "5. Psutil\nBSD 3-Clause License. Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola'\n\n"
            "6. Pillow (PIL)\nHPND License. Copyright (c) 1997-2011 by Secret Labs AB.\n\n"
            "7. Requests\nApache License 2.0. Copyright (c) 2012 Kenneth Reitz\n"
        )
        tb.insert("1.0", licenses_text)
        tb.configure(state="disabled")
        
    def connection_watchdog(self):
        while True:
            time.sleep(3)
            if not self.is_monitoring or self.is_connecting:
                continue
                
            if time.time() - self.last_ping_time > 12.0:
                self.after(0, lambda: self.log("Watchdog: Connection lost! Waiting for printer to reboot...", "#d29922"))
                self.after(0, lambda: self.update_ui_state("REBOOTING / OFFLINE", "#da3633"))
                
                ip = self.ip_entry.get().strip()
                
                is_online = False
                while self.is_monitoring:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(2)
                        if sock.connect_ex((ip, 3030)) == 0:
                            is_online = True
                            sock.close()
                            break
                        sock.close()
                    except: pass
                    time.sleep(3)
                    
                if not self.is_monitoring:
                    break 
                    
                self.after(0, lambda: self.log("Watchdog: Printer online! Restoring connection...", "#3fb950"))
                time.sleep(8) 
                
                try:
                    if hasattr(self.protocol, 'ws'): self.protocol.ws.close()
                    elif hasattr(self.protocol, 'close'): self.protocol.close()
                except: pass
                
                try:
                    self.protocol = CentauriProtocol(ip, self.process_status_update)
                    self.protocol.connect()
                    self.last_ping_time = time.time()
                    
                    time.sleep(2)
                    self.send_cmd(1, {})
                    self.send_cmd(0, {})
                    
                    self.send_cmd(386, {"Enable": 1})
                    self.send_cmd(403, {"LightStatus": {"SecondLight": 1}}) 
                    
                    threading.Thread(target=self.force_restart_camera, daemon=True).start()

                    self.after(0, lambda: self.log("Watchdog: Telemetry and Camera streams successfully restored.", "#3fb950"))
                    
                    if self.oc_mode_active:
                        threading.Thread(target=self.scan_active_mesh, daemon=True).start()
                except Exception as e:
                    print(f"Watchdog reconnect failed: {e}")

    def upload_file(self):
        self.log("Upload logic will be connected tomorrow!", "#d29922")

    def create_ai_row(self, master, name, row, min_v, max_v, start_v, suffix, steps=None):
        lbl = ctk.CTkLabel(master, text=f"{name}: {start_v}{suffix}", font=ctk.CTkFont(size=11), width=100, anchor="w")
        lbl.grid(row=row, column=0, padx=5)
        
        sl = ctk.CTkSlider(master, from_=min_v, to=max_v, width=120, number_of_steps=steps)
        sl.set(start_v)
        sl.grid(row=row, column=1, padx=5)
        
        ent = ctk.CTkEntry(master, width=45, height=22, font=ctk.CTkFont(size=10))
        ent.grid(row=row, column=2, padx=2)
        
        setattr(self, f"ai_lbl_{row}", lbl)
        
        def update_from_slider(val, r=row, n=name, suf=suffix):
            v = int(val) if steps else round(val, 1)
            getattr(self, f"ai_lbl_{r}").configure(text=f"{n}: {v}{suf}")
            
        sl.configure(command=update_from_slider)
        
        def set_from_ent(s=sl, e=ent, mi=min_v, ma=max_v, is_int=(steps is not None)):
            try:
                raw = float(e.get())
                val = max(mi, min(ma, int(raw) if is_int else raw))
                s.set(val)
                update_from_slider(val)
                e.delete(0, 'end')
            except ValueError:
                pass
                
        ctk.CTkButton(master, text="SET", width=35, height=22, fg_color="#30363d", command=set_from_ent).grid(row=row, column=3, padx=2)
        return sl, ent

    def cleanup_old_logs(self):
        cutoff = datetime.now() - timedelta(days=3)
        for cat in ["Traffic", "AI", "App", "History"]:
            path = os.path.join(self.user_dir, f"temp_logs/{cat}")
            if os.path.exists(path):
                for f in os.listdir(path):
                    f_path = os.path.join(path, f)
                    if os.path.isfile(f_path):
                        if datetime.fromtimestamp(os.path.getctime(f_path)) < cutoff:
                            try: os.remove(f_path)
                            except: pass

    def write_log(self, category, msg):
        if not self.log_cb.get(): return
        date_str = datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(self.user_dir, f"temp_logs/{category}/{category}_Log_{date_str}.txt")
        ts = datetime.now().strftime('%H:%M:%S')
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

    def open_app_data_folder(self):
        try:
            os.startfile(self.user_dir)
            self.log("Opened AppData folder.")
        except Exception as e:
            self.log(f"Failed to open folder: {e}", "#da3633") 

    def open_bed_mesh_manager(self):
        try:
            from bed_mesh_manager import BedMeshManager
            if not hasattr(self, 'mesh_manager_window') or not self.mesh_manager_window.winfo_exists():
                self.mesh_manager_window = BedMeshManager(self)
                self.after(100, self.apply_oc_locks)
            else:
                self.mesh_manager_window.deiconify() 
                self.mesh_manager_window.focus()        
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(err)
            self.log(f"❌ Mesh Manager Failed to Load: {e}", "#da3633")

    def open_telegram_manager(self):
        try:
            from telegram_window import TelegramWindow
            if self.telegram_window is None or not self.telegram_window.winfo_exists():
                self.telegram_window = TelegramWindow(self, self.telegram)
            else:
                self.telegram_window.deiconify()
                self.telegram_window.focus()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log(f"❌ Telegram Window Failed to Load: {e}", "#da3633")

    def _deferred_log(self, msg, color="#8b949e"):
        """Thread-safe log callback for the Telegram bot thread."""
        try:
            self.after(0, lambda: self.log(msg, color))
        except Exception:
            pass

    def _telegram_auto_start(self):
        """Start the bot from a background thread so UI boot isn't blocked."""
        try:
            ok = self.telegram.start()
            if ok:
                self.after(0, self.on_telegram_state_changed)
        except Exception as e:
            self.after(0, lambda err=e: self.log(
                f"Telegram auto-start failed: {err}", "#da3633"))

    def on_telegram_state_changed(self):
        """Called when the bot starts/stops. Enables/disables the Warn switch."""
        try:
            if self.telegram.is_running and self.konstance_active:
                self.telegram_warn_switch.configure(state="normal")
            else:
                self.telegram_warn_switch.configure(state="disabled")
                if not self.telegram.is_running:
                    self.telegram_warn_switch.deselect()
        except Exception:
            pass

    def on_app_close(self):
        """Clean shutdown: stop bot thread before destroying Tk."""
        try:
            if hasattr(self, 'telegram') and self.telegram is not None:
                self.telegram.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # -------- Preheat sequence (called from Telegram) --------
    def set_target_temp(self, which, value):
        """Set a single target temp. which in {'nozzle','bed'}. Value in °C."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        key = "TempTargetNozzle" if which == "nozzle" else "TempTargetHotbed"
        self.send_cmd(403, {key: v})
        self.log(f"🌡️ Target {which} set to {v}°C (via Telegram)")
        return True

    def run_preheat_sequence(self, nozzle_temp, bed_temp, duration_minutes):
        """Full preheat sequence:
          1) Home XYZ               → wait 120s
          2) Drop bed 200mm         → wait 15s
          3) Set nozzle + bed temps → wait 10s
          4) Turn on model fan at 40% to circulate chamber air
          Watchdog re-applies temps if the printer zeros them mid-preheat.
          Fires a 'preheat ready' notification when both temps reach target.
          Returns (success, reason) for the caller to report back."""
        if self.last_status in ("Printing", "Preparing", "Paused", "Pausing"):
            return False, f"Cannot preheat: printer is {self.last_status}."

        # Cancel any previous preheat (timers + watchdog)
        self.cancel_preheat(silent=True)

        try:
            nozzle_temp = float(nozzle_temp)
            bed_temp = float(bed_temp)
            duration_minutes = int(duration_minutes)
        except (TypeError, ValueError):
            return False, "Invalid numeric values."

        if not (0 <= nozzle_temp <= 300):
            return False, "Nozzle temp must be 0-300°C."
        if not (0 <= bed_temp <= 120):
            return False, "Bed temp must be 0-120°C."
        if not (1 <= duration_minutes <= 180):
            return False, "Duration must be 1-180 minutes."

        # Remember what we asked for (watchdog reference)
        self.preheat_target_nozzle = nozzle_temp
        self.preheat_target_bed = bed_temp
        self.preheat_ready_notified = False

        self.log(f"🔥 Preheat starting: N={nozzle_temp}° B={bed_temp}° for {duration_minutes}min", "#d29922")

        # Step 1: Home XYZ (same command as the 🏠 button)
        self.send_cmd(402, {"Axis": "XYZ"})
        self.log("🔥 Step 1/4: Home XYZ sent, waiting 120s...", "#d29922")

        def _drop_bed():
            # Step 2: Drop bed 200mm
            self.send_cmd(401, {"Axis": "Z", "Step": 200.0})
            self.log("🔥 Step 2/4: Bed dropped 200mm, waiting 15s...", "#d29922")
            self.after(15000, _set_temps)

        def _set_temps():
            # Step 3: Set temps
            self.send_cmd(403, {
                "TempTargetNozzle": nozzle_temp,
                "TempTargetHotbed": bed_temp,
            })
            self.log(f"🔥 Step 3/4: Temps set N={nozzle_temp}° B={bed_temp}°, waiting 10s...", "#d29922")
            self.after(10000, _start_fan)

        def _start_fan():
            # Step 4: Model fan at 40% for chamber circulation
            self.fan_states["ModelFan"] = 40
            self.send_cmd(403, {"TargetFanSpeed": self.fan_states})
            self.log("🔥 Step 4/4: Model fan set to 40% for chamber circulation.", "#d29922")

            # Preheat is now officially "active" — watchdog and cooldown start NOW.
            self.preheat_active = True
            self.preheat_end_ts = time.time() + (duration_minutes * 60)

            # Schedule cooldown
            self.preheat_timer_id = self.after(
                duration_minutes * 60 * 1000,
                lambda: self.cancel_preheat(silent=False, reason="timer expired")
            )

            # Start the temp-hold watchdog (re-applies targets if printer zeros them)
            self.preheat_watchdog_id = self.after(20000, self._preheat_watchdog_tick)

            # Notify Telegram + update in-app UI button
            try:
                if hasattr(self, 'telegram') and self.telegram and self.telegram.is_running:
                    self.telegram.notify_preheat_started(nozzle_temp, bed_temp, duration_minutes)
            except Exception:
                pass
            try:
                self._update_preheat_ui_state()
            except Exception:
                pass

        # Step 1 → wait 120s → _drop_bed
        self.after(120000, _drop_bed)
        return True, (
            f"Preheat started: home → 120s → bed drop → 15s → "
            f"temps → 10s → fan 40%. Auto cool-down in {duration_minutes} min."
        )

    def _preheat_watchdog_tick(self):
        """Runs every 20s while preheat is active. Re-applies targets if the
        printer has zeroed them out. Also detects 'ready' state and notifies."""
        if not self.preheat_active:
            self.preheat_watchdog_id = None
            return

        tol = 2.0  # °C tolerance for rounding

        # Re-apply nozzle target if it dropped
        try:
            if self.preheat_target_nozzle > 0 and self.temp_target_nozzle < (self.preheat_target_nozzle - tol):
                self.log(
                    f"🔥 Nozzle target dropped ({self.temp_target_nozzle}° < {self.preheat_target_nozzle}°), re-applying.",
                    "#d29922"
                )
                self.send_cmd(403, {"TempTargetNozzle": self.preheat_target_nozzle})
        except Exception:
            pass

        # Re-apply bed target if it dropped
        try:
            if self.preheat_target_bed > 0 and self.temp_target_bed < (self.preheat_target_bed - tol):
                self.log(
                    f"🔥 Bed target dropped ({self.temp_target_bed}° < {self.preheat_target_bed}°), re-applying.",
                    "#d29922"
                )
                self.send_cmd(403, {"TempTargetHotbed": self.preheat_target_bed})
        except Exception:
            pass

        # Also re-apply fan if the printer cleared it
        try:
            if self.fan_states.get("ModelFan", 0) < 35:
                self.fan_states["ModelFan"] = 40
                self.send_cmd(403, {"TargetFanSpeed": self.fan_states})
        except Exception:
            pass

        # "Preheat ready" detection — both temps within tolerance of target
        try:
            if not self.preheat_ready_notified:
                n_ok = (self.preheat_target_nozzle == 0) or (self.temp_current_nozzle >= self.preheat_target_nozzle - tol)
                b_ok = (self.preheat_target_bed == 0) or (self.temp_current_bed >= self.preheat_target_bed - tol)
                if n_ok and b_ok:
                    self.preheat_ready_notified = True
                    self.log("✅ Preheat reached target temperatures.", "#3fb950")
                    try:
                        if hasattr(self, 'telegram') and self.telegram and self.telegram.is_running:
                            self.telegram.notify_preheat_ready(
                                self.temp_current_nozzle, self.temp_current_bed
                            )
                    except Exception:
                        pass
        except Exception:
            pass

        # Reschedule
        self.preheat_watchdog_id = self.after(20000, self._preheat_watchdog_tick)

    def cancel_preheat(self, silent=False, reason="manual"):
        """Cancel any active preheat: kill timers, zero temps, stop fan."""
        if self.preheat_timer_id is not None:
            try:
                self.after_cancel(self.preheat_timer_id)
            except Exception:
                pass
            self.preheat_timer_id = None

        if self.preheat_watchdog_id is not None:
            try:
                self.after_cancel(self.preheat_watchdog_id)
            except Exception:
                pass
            self.preheat_watchdog_id = None

        was_active = self.preheat_active
        self.preheat_active = False
        self.preheat_end_ts = 0
        self.preheat_target_nozzle = 0.0
        self.preheat_target_bed = 0.0
        self.preheat_ready_notified = False

        # Set both temps to 0 and stop the model fan
        self.send_cmd(403, {"TempTargetNozzle": 0.0, "TempTargetHotbed": 0.0})
        try:
            self.fan_states["ModelFan"] = 0
            self.send_cmd(403, {"TargetFanSpeed": self.fan_states})
        except Exception:
            pass

        if not silent:
            self.log(f"❄️ Cool down: temps set to 0, fan off ({reason})", "#10D0DE")
            try:
                if was_active and hasattr(self, 'telegram') and self.telegram and self.telegram.is_running:
                    self.telegram.notify_preheat_ended(reason)
            except Exception:
                pass

        # Update in-app preheat UI back to idle
        try:
            self._update_preheat_ui_state()
        except Exception:
            pass

    def preheat_remaining_minutes(self):
        """Returns minutes remaining on active preheat, or 0 if not active."""
        if not self.preheat_active:
            return 0
        remaining = self.preheat_end_ts - time.time()
        return max(0, int(remaining / 60))

    def _update_preheat_ui_state(self):
        """Flip the Start/Cancel button state in the sidebar based on active flag."""
        btn = getattr(self, "preheat_start_btn", None)
        if btn is None:
            return
        if self.preheat_active:
            btn.configure(text="❄️ Cancel Preheat", fg_color="#a40e26", hover_color="#c9312a")
        else:
            btn.configure(text="🔥 Start Preheat", fg_color="#238636", hover_color="#2ea043")

    def on_preheat_button_click(self):
        """Sidebar button handler. Start if idle, cancel if active."""
        if self.preheat_active:
            self.cancel_preheat(silent=False, reason="manual (app)")
            return
        try:
            n = float(self.preheat_nozzle_ent.get() or 0)
            b = float(self.preheat_bed_ent.get() or 0)
            m = int(self.preheat_mins_ent.get() or 0)
        except ValueError:
            self.log("🔥 Preheat: invalid numeric input.", "#da3633")
            return
        ok, msg = self.run_preheat_sequence(n, b, m)
        color = "#3fb950" if ok else "#da3633"
        self.log(f"🔥 {msg}", color)

    # -------- Remote file ops (called by Telegram) --------
    def request_file_list_for_telegram(self, path="/local/"):
        """Trigger a cmd 258 fetch. The response will land in the 258 handler,
        which calls self.telegram.on_file_list_received()."""
        try:
            self.send_cmd(258, {"Url": path})
            return True
        except Exception as e:
            self.log(f"File list request failed: {e}", "#da3633")
            return False

    def start_print_file(self, filename, leveling=False, timelapse=False, plate_type=0):
        """Start a print of an existing file on the printer.
        filename: full printer path like '/local/cube.gcode'
        leveling: bed leveling on/off (Calibration_switch)
        timelapse: timelapse on/off (Tlp_Switch)
        plate_type: 0 = Textured PEI, 1 = Smooth PEI (PrintPlatformType)
        Returns (success, message)."""
        if self.last_status in ("Printing", "Preparing", "Paused", "Pausing"):
            return False, f"Cannot start print: printer is {self.last_status}."
        if not filename:
            return False, "No filename provided."

        # Make sure path is /local/<name> — match the original UI exactly
        if not filename.startswith("/local/") and not filename.startswith("/"):
            filename = f"/local/{filename}"

        # Full payload matching the sniffed wire format from the original UI
        payload = {
            "Filename": filename,
            "StartLayer": 0,
            "Calibration_switch": 1 if leveling else 0,
            "PrintPlatformType": int(plate_type),
            "Tlp_Switch": 1 if timelapse else 0,
        }

        try:
            self.send_cmd(128, payload)
            opts = []
            if leveling: opts.append("Leveling")
            if timelapse: opts.append("Timelapse")
            opts.append("Smooth PEI" if plate_type == 1 else "Textured PEI")
            opts_txt = f" ({', '.join(opts)})"
            display_name = filename.rsplit("/", 1)[-1]
            self.log(f"🖨️ Start print sent: {display_name}{opts_txt}", "#10D0DE")
            return True, f"Print start sent for {display_name}{opts_txt}"
        except Exception as e:
            self.log(f"Start print failed: {e}", "#da3633")
            return False, f"Start print failed: {e}"

    def delete_file_remote(self, filename):
        """Delete a file on the printer. filename is full path like '/local/cube.gcode'."""
        try:
            self.send_cmd(259, {"FileList": [filename], "FolderList": []})
            self.log(f"🗑️ Delete sent: {filename}")
            return True, f"Deleted: {os.path.basename(filename)}"
        except Exception as e:
            return False, f"Delete failed: {e}"

    def log(self, msg, color="#8b949e"): 
        self.status_label.configure(text=f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", text_color=color)
        self.write_log("History", msg)
        try:
            self.history_text.insert("1.0", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            if int(self.history_text.index('end-1c').split('.')[0]) > 100: self.history_text.delete("101.0", "end")
        except: pass

    def log_traffic(self, msg):
        self.write_log("Traffic", msg)
        try:
            ts = datetime.now().strftime('%H:%M:%S')
            self.traffic_text.insert("1.0", f"[{ts}] {msg}\n")
            if int(self.traffic_text.index('end-1c').split('.')[0]) > 100: self.traffic_text.delete("101.0", "end")
        except: pass

    def log_ai(self, msg):
        self.write_log("AI", msg)
        try:
            ts = datetime.now().strftime('%H:%M:%S')
            self.ai_text.insert("1.0", f"[{ts}] {msg}\n")
            if int(self.ai_text.index('end-1c').split('.')[0]) > 100: self.ai_text.delete("101.0", "end")
        except: pass
        
    def log_app(self, msg):
        self.write_log("App", msg)
        try:
            ts = datetime.now().strftime('%H:%M:%S')
            self.app_text.insert("1.0", f"[{ts}] {msg}\n")
            if int(self.app_text.index('end-1c').split('.')[0]) > 100: self.app_text.delete("101.0", "end")
        except: pass

    def toggle_konstance(self):
        if not self.model_loaded: return
        self.konstance_active = not self.konstance_active
        if self.konstance_active:
            self.konstance_btn.configure(text="Konstance Active", text_color="#3fb950", fg_color="#21262d")
            self.auto_pause_switch.configure(state="normal")
            if self.telegram.is_running:
                self.telegram_warn_switch.configure(state="normal")
            self.log_ai("Konstance Engine activated.")
        else:
            self.konstance_btn.configure(text="Konstance Inactive", text_color="#d29922", fg_color="#21262d")
            self.auto_pause_switch.configure(state="disabled")
            self.telegram_warn_switch.configure(state="disabled")
            self.log_ai("Konstance Engine deactivated.")

    def get_available_models(self):
        model_dir = os.path.join(os.getcwd(), "models")
        os.makedirs(model_dir, exist_ok=True)
        self.model_map = {}

        for folder in os.listdir(model_dir):
            path = os.path.join(model_dir, folder)
            if os.path.isdir(path):
                files = os.listdir(path)
                # OpenVINO model: needs both model.xml AND model.bin
                if "model.xml" in files and "model.bin" in files:
                    self.model_map[folder] = path  # folder path for ultralytics
                else:
                    pts = [f for f in files if f.endswith(".pt")]
                    if pts:
                        self.model_map[folder] = os.path.join(path, pts[0])

        return list(self.model_map.keys())

    def _release_current_model(self):
        """Aggressively release the current model. Both .pt and OpenVINO can
        hold onto memory in non-obvious places, so we hit it with everything."""
        try:
            if self.model is not None:
                # Try to clear any internal predictor state
                try:
                    if hasattr(self.model, 'predictor') and self.model.predictor is not None:
                        if hasattr(self.model.predictor, 'model'):
                            del self.model.predictor.model
                        del self.model.predictor
                except Exception:
                    pass
                try:
                    del self.model
                except Exception:
                    pass
        except Exception:
            pass
        self.model = None
        gc.collect()
        # OpenVINO IR resources sometimes need a second GC pass
        time.sleep(0.1)
        gc.collect()

    def _load_model_from_path(self, target_path, name):
        """Try to load a model. Returns (model_object, error_string).
        For OpenVINO models the path is a folder. For .pt it's a file."""
        # Pre-flight check: if it's an OpenVINO folder, verify both files exist
        if os.path.isdir(target_path):
            xml = os.path.join(target_path, "model.xml")
            binf = os.path.join(target_path, "model.bin")
            if not os.path.exists(xml):
                return None, f"Missing model.xml in {os.path.basename(target_path)}"
            if not os.path.exists(binf):
                return None, f"Missing model.bin in {os.path.basename(target_path)}"
        elif not os.path.exists(target_path):
            return None, f"File not found: {target_path}"

        # Attempt 1: load from the path as-is (folder for OpenVINO, file for .pt)
        try:
            m = YOLO(target_path, task='detect')
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            # Attempt 2 (OpenVINO fallback): try the explicit model.xml file path
            if os.path.isdir(target_path):
                try:
                    xml = os.path.join(target_path, "model.xml")
                    m = YOLO(xml, task='detect')
                except Exception as e2:
                    return None, f"{err}  |  fallback: {type(e2).__name__}: {e2}"
            else:
                return None, err

        # Warmup inference — catches "loaded but broken" cases
        try:
            _ = m.predict(np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)
        except Exception as e:
            return None, f"Warmup inference failed: {type(e).__name__}: {e}"

        return m, None

    def load_model(self):
        default_name = "Konstance_light_openvino_model"
        start_path = self.model_map.get(default_name)

        if not start_path and self.model_map:
            default_name = list(self.model_map.keys())[0]
            start_path = self.model_map[default_name]

        if not start_path:
            self.after(0, lambda: [
                self.konstance_btn.configure(text="Konstance: NO MODELS", text_color="#da3633"),
                self.log_app("No models found in models/ folder")
            ])
            return

        m, err = self._load_model_from_path(start_path, default_name)
        if m is not None:
            self.model = m
            self.model_loaded = True
            self.after(0, lambda: [
                self.konstance_btn.configure(text="Konstance Inactive", state="normal", text_color="#d29922"),
                self.log(f"✅ AI Ready: {default_name}", "#3fb950"),
                self.log_app(f"Model loaded: {default_name}")
            ])
        else:
            self.model = None
            self.model_loaded = False
            self.after(0, lambda e=err, n=default_name: [
                self.konstance_btn.configure(text="Konstance: ERROR", text_color="#da3633"),
                self.log_app(f"Model Load Failed [{n}]: {e}"),
                self.log(f"❌ Model Load Failed: {e[:80]}", "#da3633")
            ])

    def reload_model_event(self, selected_name):
        # Stop inference immediately so the camera loop doesn't touch a dying model
        self.model_loaded = False
        self.konstance_btn.configure(text="SWAPPING AI...", text_color="#d29922")

        target = self.model_map.get(selected_name)
        if not target:
            self.log_ai(f"❌ Model '{selected_name}' not in map.")
            return

        def swap_task():
            try:
                self.log_ai(f"🔄 Releasing current model...")
                self._release_current_model()

                self.log_ai(f"🔄 Loading: {selected_name}...")
                m, err = self._load_model_from_path(target, selected_name)

                if m is not None:
                    self.model = m
                    self.model_loaded = True
                    self.after(0, lambda: [
                        self.konstance_btn.configure(
                            text="Konstance Active" if self.konstance_active else "Konstance Inactive",
                            text_color="#3fb950" if self.konstance_active else "#d29922"
                        ),
                        self.log_ai(f"✅ AI Swapped: {selected_name}")
                    ])
                else:
                    # Failed swap — leave self.model = None so the camera loop is safe
                    self.model = None
                    self.model_loaded = False
                    self.after(0, lambda e=err: [
                        self.log_ai(f"❌ Swap Failed: {e}"),
                        self.log_app(f"Model Swap Failed [{selected_name}]: {e}"),
                        self.konstance_btn.configure(text="Konstance: ERROR", text_color="#da3633")
                    ])
            except Exception as e:
                # Catch-all so the swap thread never dies silently
                err_full = f"{type(e).__name__}: {e}"
                self.model = None
                self.model_loaded = False
                self.after(0, lambda err=err_full: [
                    self.log_ai(f"❌ Swap Crash: {err}"),
                    self.log_app(f"Model Swap Crash [{selected_name}]: {err}"),
                    self.konstance_btn.configure(text="Konstance: ERROR", text_color="#da3633")
                ])

        threading.Thread(target=swap_task, daemon=True).start()

    def create_temp_widget(self, name, edit):
        f = ctk.CTkFrame(self.temp_frame, fg_color="transparent")
        f.pack(side="left", padx=12)
        l = ctk.CTkLabel(f, text=f"{name}: -- / -- °C", font=ctk.CTkFont(size=11, weight="bold"), cursor="hand2")
        l.pack(side="left")
        ent_f = ctk.CTkFrame(f, fg_color="transparent")
        ent = ctk.CTkEntry(ent_f, width=45, height=20, font=ctk.CTkFont(size=10))
        ent.pack(side="left")
        
        def save_temp(n=name, e=ent, lb=l, ef=ent_f):
            v = e.get()
            if v:
                target_key = "TempTargetNozzle" if n == "Nozzle" else "TempTargetHotbed"
                self.send_cmd(403, {target_key: float(v)})
            ef.pack_forget()
            lb.pack(side="left")

        ctk.CTkButton(ent_f, text="✅", width=25, height=20, command=save_temp).pack(side="left", padx=2)
        
        def set_zero(n=name, ef=ent_f, lb=l):
            target_key = "TempTargetNozzle" if n == "Nozzle" else "TempTargetHotbed"
            self.send_cmd(403, {target_key: 0.0})
            ef.pack_forget()
            lb.pack(side="left")

        ctk.CTkButton(ent_f, text="0", width=25, height=20, fg_color="#a40e26", command=set_zero).pack(side="left")
        if edit: l.bind("<Double-Button-1>", lambda e: [l.pack_forget(), ent_f.pack(side="left"), ent.focus()])
        self.temp_widgets[name] = {"label": l}

    def build_sidebar(self):
        fm_acc = self.add_accordion("Print and file manager")
        self.file_manager = CentauriFileManager(self, self.log, fm_acc.content, self.send_cmd)

        f_acc = self.add_accordion("Software Filters")
        self.f_master = ctk.CTkSwitch(f_acc.content, text="Master Switch", command=lambda: self.update_filter_state("Enabled", self.f_master.get()))
        self.f_master.pack(pady=5)
        self.add_filter_row(f_acc.content, "Grayscale")
        self.add_filter_row(f_acc.content, "CLAHE (Contrast)")
        self.add_filter_row(f_acc.content, "Edge (Sharpen)")
        
        self.add_filter_slider(f_acc.content, "Brightness", "Bright", -100, 100, 0)
        self.add_filter_slider(f_acc.content, "Contrast", "Contrast", 0.1, 5.0, 1.0)
        self.add_filter_slider(f_acc.content, "Gamma", "Gamma", 0.1, 4.0, 1.0)
        self.add_filter_slider(f_acc.content, "Black", "Black", -150, 150, 0)

        m_acc = self.add_accordion("Movement/Extruder/Preheat control")
        self.move_step = 10.0
        step_f = ctk.CTkFrame(m_acc.content, fg_color="transparent")
        step_f.pack(pady=5)
        self.step_btns = []
        for s in [0.1, 1, 10, 100]:
            btn = ctk.CTkButton(step_f, text=f"{s}mm", width=55, height=25, fg_color="#1f6feb" if s == 10 else "#21262d", 
                               command=lambda v=s: self.set_move_step_preset(v))
            btn.pack(side="left", padx=2)
            self.step_btns.append(btn)
        
        custom_f = ctk.CTkFrame(m_acc.content, fg_color="transparent")
        custom_f.pack(pady=2)
        ctk.CTkLabel(custom_f, text="Custom:", font=ctk.CTkFont(size=10)).pack(side="left", padx=2)
        self.custom_step_ent = ctk.CTkEntry(custom_f, width=50, height=22, font=ctk.CTkFont(size=10), placeholder_text="...")
        self.custom_step_ent.pack(side="left", padx=2)
        self.custom_step_btn = ctk.CTkButton(custom_f, text="SET", width=35, height=22, fg_color="#21262d", 
                     command=lambda: self.set_move_step_custom(self.custom_step_ent.get()))
        self.custom_step_btn.pack(side="left")
        ctk.CTkLabel(m_acc.content, text="Custom step (0.1 - 200mm)", font=ctk.CTkFont(size=9), text_color="#8b949e").pack()

        g = ctk.CTkFrame(m_acc.content, fg_color="transparent")
        g.pack(pady=10, anchor="center")
        ctk.CTkButton(g, text="Y+", width=50, command=lambda: self.send_cmd(401, {"Axis":"Y","Step":self.move_step})).grid(row=0, column=1)
        ctk.CTkButton(g, text="X-", width=50, command=lambda: self.send_cmd(401, {"Axis":"X","Step":-self.move_step})).grid(row=1, column=0)
        ctk.CTkButton(g, text="🏠", width=50, fg_color="#21262d", command=lambda: self.send_cmd(402, {"Axis":"XYZ"})).grid(row=1, column=1)
        ctk.CTkButton(g, text="X+", width=50, command=lambda: self.send_cmd(401, {"Axis":"X","Step":self.move_step})).grid(row=1, column=2)
        ctk.CTkButton(g, text="Y-", width=50, command=lambda: self.send_cmd(401, {"Axis":"Y","Step":-self.move_step})).grid(row=2, column=1)
        ctk.CTkButton(g, text="Z- ⬆️", width=50, fg_color="#30363d", command=lambda: self.send_cmd(401, {"Axis":"Z","Step":-self.move_step})).grid(row=0, column=3, padx=10)
        ctk.CTkButton(g, text="Z+ ⬇️", width=50, fg_color="#30363d", command=lambda: self.send_cmd(401, {"Axis":"Z","Step":self.move_step})).grid(row=2, column=3, padx=10)

        # --- Preheat block ---
        ctk.CTkFrame(m_acc.content, height=2, fg_color="#30363d").pack(fill="x", padx=5, pady=(10, 5))
        ctk.CTkLabel(m_acc.content, text="🔥 Preheat", font=ctk.CTkFont(size=12, weight="bold"), text_color="#d29922").pack(pady=(2, 5))

        ph_grid = ctk.CTkFrame(m_acc.content, fg_color="transparent")
        ph_grid.pack(pady=2)

        ctk.CTkLabel(ph_grid, text="Nozzle", font=ctk.CTkFont(size=10), width=50).grid(row=0, column=0, padx=2)
        ctk.CTkLabel(ph_grid, text="Bed", font=ctk.CTkFont(size=10), width=50).grid(row=0, column=1, padx=2)
        ctk.CTkLabel(ph_grid, text="Minutes", font=ctk.CTkFont(size=10), width=50).grid(row=0, column=2, padx=2)

        self.preheat_nozzle_ent = ctk.CTkEntry(ph_grid, width=55, height=24, font=ctk.CTkFont(size=10), placeholder_text="210")
        self.preheat_nozzle_ent.grid(row=1, column=0, padx=2)
        self.preheat_bed_ent = ctk.CTkEntry(ph_grid, width=55, height=24, font=ctk.CTkFont(size=10), placeholder_text="60")
        self.preheat_bed_ent.grid(row=1, column=1, padx=2)
        self.preheat_mins_ent = ctk.CTkEntry(ph_grid, width=55, height=24, font=ctk.CTkFont(size=10), placeholder_text="15")
        self.preheat_mins_ent.grid(row=1, column=2, padx=2)

        self.preheat_start_btn = ctk.CTkButton(
            m_acc.content, text="🔥 Start Preheat",
            fg_color="#238636", hover_color="#2ea043",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.on_preheat_button_click
        )
        self.preheat_start_btn.pack(fill="x", padx=10, pady=(8, 5))

        ctk.CTkLabel(
            m_acc.content,
            text="Home → 120s → bed drop → 15s →\ntemps → 10s → chamber fan 40%",
            font=ctk.CTkFont(size=9), text_color="#8b949e", justify="center"
        ).pack(pady=(0, 8))
        # --- end preheat block ---

        fan_acc = self.add_accordion("Fan Controls")
        for idx, n in enumerate(["ModelFan", "AuxiliaryFan", "BoxFan"]):
            short_n = ["Mdl", "Aux", "Box"][idx]
            fr = ctk.CTkFrame(fan_acc.content, fg_color="transparent")
            fr.pack(fill="x", pady=4, anchor="center")
            
            ctk.CTkLabel(fr, text=short_n, width=30, font=ctk.CTkFont(size=10, weight="bold"), anchor="w").pack(side="left")
            
            sl = ctk.CTkSlider(fr, from_=0, to=100, width=70, number_of_steps=20)
            sl.set(0)
            
            ent = ctk.CTkEntry(fr, width=35, height=20, font=ctk.CTkFont(size=10))
            ent.insert(0, "0")
            
            def set_fan(val, fan_name, slider_widget, entry_widget):
                try: v = max(0, min(100, int(float(val))))
                except: v = 0
                slider_widget.set(v)
                entry_widget.delete(0, 'end')
                entry_widget.insert(0, str(v))
                self.fan_states[fan_name] = v
                self.send_fan(fan_name)

            ctk.CTkButton(fr, text="MIN", width=30, height=20, fg_color="#30363d", command=lambda fn=n, s=sl, e=ent: set_fan(0, fn, s, e)).pack(side="left", padx=2)
            
            sl.configure(command=lambda v, e=ent: [e.delete(0, 'end'), e.insert(0, str(int(float(v))))])
            
            sl.bind("<ButtonRelease-1>", lambda event, fn=n, s=sl, e=ent: set_fan(s.get(), fn, s, e))
            sl.pack(side="left", padx=2)
            
            ctk.CTkButton(fr, text="MAX", width=30, height=20, fg_color="#30363d", command=lambda fn=n, s=sl, e=ent: set_fan(100, fn, s, e)).pack(side="left", padx=2)
            
            ent.pack(side="left", padx=2)
            
            ctk.CTkButton(fr, text="SET", width=30, height=20, fg_color="#30363d", command=lambda fn=n, s=sl, e=ent: set_fan(e.get(), fn, s, e)).pack(side="left", padx=2)

        self.ssh_acc = self.add_accordion("Simple SSH tools(OC only)")
        
        ctk.CTkLabel(self.ssh_acc.content, text="Printer SSH Credentials", font=ctk.CTkFont(size=11, weight="bold"), text_color="#10D0DE").pack(pady=(5,0))
        cred_f = ctk.CTkFrame(self.ssh_acc.content, fg_color="transparent")
        cred_f.pack(fill="x", padx=10, pady=5)
        self.ssh_user = ctk.CTkEntry(cred_f, width=120, height=25, font=ctk.CTkFont(size=11))
        self.ssh_user.insert(0, "root")
        self.ssh_user.pack(side="left", padx=2)
        self.ssh_pwd = ctk.CTkEntry(cred_f, width=120, height=25, font=ctk.CTkFont(size=11), show="*")
        self.ssh_pwd.insert(0, "OpenCentauri")
        self.ssh_pwd.pack(side="left", padx=2)
        ctk.CTkButton(self.ssh_acc.content, text="Confirm Credentials", width=120, height=25, fg_color="#30363d", command=self.update_hw_creds).pack(pady=5)
        
        ctk.CTkFrame(self.ssh_acc.content, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(self.ssh_acc.content, text="cam driver lvl tuning", font=ctk.CTkFont(size=11, weight="bold"), text_color="#10D0DE").pack(pady=2)
        
        ctk.CTkButton(self.ssh_acc.content, text="Factory Default", fg_color="#1f6feb", command=self.apply_factory_hw).pack(pady=(5, 2), padx=10, fill="x")
        ctk.CTkButton(self.ssh_acc.content, text="Decent AI settings", fg_color="#1f6feb", command=lambda: self.hw.apply_preset_1(self.log)).pack(pady=2, padx=10, fill="x")
        
        ae_f = ctk.CTkFrame(self.ssh_acc.content, fg_color="transparent")
        ae_f.pack(pady=5)
        self.ae_sw = ctk.CTkSwitch(ae_f, text="Auto Exposure (ON)", command=self.update_ae_ui_state)
        self.ae_sw.select()
        self.ae_sw.pack()
        
        self.ssh_inputs = {}
        controls = [("Saturation", "saturation"), ("Contrast", "contrast"), ("Hue", "hue"), ("Gain", "gain"), ("Sharpness", "sharpness"), ("Manual Exp", "exposure_absolute")]
        for lab, key in controls:
            row = ctk.CTkFrame(self.ssh_acc.content, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=lab, width=80, anchor="w", font=ctk.CTkFont(size=10)).pack(side="left")
            ent = ctk.CTkEntry(row, width=50, height=22, font=ctk.CTkFont(size=10))
            ent.pack(side="left", padx=2)
            if key != "exposure_absolute":
                ctk.CTkLabel(row, text=f"(Def: {self.ssh_defaults[key]})", font=ctk.CTkFont(size=9), text_color="#8b949e").pack(side="left", padx=2)
            self.ssh_inputs[key] = ent
            
        self.update_ae_ui_state()
        ctk.CTkButton(self.ssh_acc.content, text="Push settings to camera", fg_color="#1f6feb", command=self.push_batch_ssh).pack(pady=(10, 5), padx=10, fill="x")
        
        ctk.CTkFrame(self.ssh_acc.content, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(self.ssh_acc.content, text="Power Management", font=ctk.CTkFont(size=11, weight="bold"), text_color="#da3633").pack(pady=2)
        ctk.CTkButton(self.ssh_acc.content, text="Soft Reboot Printer", fg_color="#a40e26", command=lambda: self.hw.run_ssh_cmd("reboot", self.log)).pack(pady=(2, 10), padx=10, fill="x")
        
        dbg_acc = self.add_accordion("Debug Zone")
        
        log_f = ctk.CTkFrame(dbg_acc.content, fg_color="transparent")
        log_f.pack(fill="x", padx=10, pady=2)
        self.log_cb = ctk.CTkCheckBox(log_f, text="Save logs (\\temp_logs\\)", font=ctk.CTkFont(size=10))
        self.log_cb.pack(side="left")

        self.debug_tabs = ctk.CTkTabview(dbg_acc.content, height=180)
        self.debug_tabs.pack(fill="both", expand=True, padx=5, pady=2)
        
        for t in ["Traffic", "AI", "App", "History"]: self.debug_tabs.add(t)
            
        self.traffic_text = ctk.CTkTextbox(self.debug_tabs.tab("Traffic"), font=ctk.CTkFont(family="Consolas", size=10))
        self.traffic_text.pack(fill="both", expand=True)
        
        self.ai_text = ctk.CTkTextbox(self.debug_tabs.tab("AI"), font=ctk.CTkFont(family="Consolas", size=10))
        self.ai_text.pack(fill="both", expand=True)
        
        self.app_text = ctk.CTkTextbox(self.debug_tabs.tab("App"), font=ctk.CTkFont(family="Consolas", size=10))
        self.app_text.pack(fill="both", expand=True)
        
        self.history_text = ctk.CTkTextbox(self.debug_tabs.tab("History"), font=ctk.CTkFont(family="Consolas", size=10))
        self.history_text.pack(fill="both", expand=True)

        ctk.CTkFrame(self.sidebar_frame, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=(20, 5))
        
        ctk.CTkButton(self.sidebar_frame, text="Bed Mesh Manager", fg_color="#1f6feb", hover_color="#388bfd", command=self.open_bed_mesh_manager).pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkButton(self.sidebar_frame, text="Telegram Remote", fg_color="#1f6feb", hover_color="#388bfd", command=self.open_telegram_manager).pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkButton(self.sidebar_frame, text="Open App Data Folder", fg_color="#21262d", hover_color="#30363d", command=self.open_app_data_folder).pack(fill="x", padx=10, pady=(0, 15))

        ctk.CTkFrame(self.sidebar_frame, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=(5, 10))
        
        self.oc_mode_switch = ctk.CTkSwitch(self.sidebar_frame, text="OC Mode", font=ctk.CTkFont(weight="bold", size=12), command=self.toggle_oc_mode)
        self.oc_mode_switch.pack(padx=10, pady=(0, 2), anchor="w")
        
        self.oc_mode_switch.deselect()
        self.oc_mode_active = False
        
        ctk.CTkLabel(self.sidebar_frame, text="Turns on some functions for\nOpenCentauri users. If you have\noriginal firmware, some options\nmay be inactive for you.", font=ctk.CTkFont(size=9), text_color="#8b949e", justify="left").pack(padx=10, pady=(0, 10), anchor="w")

        self.after(1500, self.apply_oc_locks)
        
        ctk.CTkFrame(self.sidebar_frame, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=(15, 10))

        ctk.CTkFrame(self.sidebar_frame, fg_color="transparent").pack(expand=True, fill="both")

        ctk.CTkFrame(self.sidebar_frame, height=2, fg_color="#30363d").pack(fill="x", padx=10, pady=(5, 10))

        self.btn_update = ctk.CTkButton(self.sidebar_frame, text="Konstance is up to date", fg_color="#21262d", text_color="#8b949e", state="disabled", command=self.open_update_link)
        self.btn_update.pack(fill="x", padx=10, pady=5)

        self.btn_licenses = ctk.CTkButton(self.sidebar_frame, text="📜 Open Source Licenses", fg_color="#21262d", hover_color="#30363d", command=self.show_licenses)
        self.btn_licenses.pack(fill="x", padx=10, pady=5)

        link_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        link_frame.pack(fill="x", pady=(5, 15))

        self.lbl_git = ctk.CTkLabel(link_frame, text="GitHub", font=ctk.CTkFont(underline=True, size=11), text_color="#1f6feb", cursor="hand2")
        self.lbl_git.pack(side="left", expand=True)
        self.lbl_git.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/kunnvonkaur/konstance"))
        
        self.lbl_agpl = ctk.CTkLabel(link_frame, text="Licence: AGPL v3.0", font=ctk.CTkFont(underline=True, size=11), text_color="#1f6feb", cursor="hand2")
        self.lbl_agpl.pack(side="left", expand=True)
        self.lbl_agpl.bind("<Button-1>", lambda e: webbrowser.open("https://www.gnu.org/licenses/agpl-3.0.en.html#license-text"))

        self.lbl_web = ctk.CTkLabel(link_frame, text="konstance.cc", font=ctk.CTkFont(underline=True, size=11), text_color="#1f6feb", cursor="hand2")
        self.lbl_web.pack(side="right", expand=True)
        self.lbl_web.bind("<Button-1>", lambda e: webbrowser.open("https://konstance.cc"))

        self.after(2000, self.check_for_updates)
        
    def scan_active_mesh(self):
        self.after(0, lambda: self.active_mesh_status_lbl.configure(text="(Scanning active mesh...)"))
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname=self.ip_entry.get().strip(), username=self.ssh_user.get(), password=self.ssh_pwd.get(), timeout=10)
            _, stdout, _ = ssh.exec_command("cat /board-resource/printer.cfg")
            printer_cfg = stdout.read().decode('utf-8')
            ssh.close()

            def extract_blocks(text):
                blocks = {}
                for b in ["[besh_profile_standard_default]", "[besh_profile_enhancement_default]", "[besh_profile_standard_1]", "[besh_profile_enhancement_1]"]:
                    lines = text.split('\n')
                    in_block = False
                    data = []
                    for line in lines:
                        if line.strip() == b:
                            in_block = True
                            data.append(line.strip())
                        elif in_block and line.strip().startswith('[') and line.strip() != b:
                            break
                        elif in_block:
                            data.append(line.strip())
                    blocks[b] = '\n'.join(data)
                return blocks

            p_blocks = extract_blocks(printer_cfg)
            configs_dir = os.path.join(self.user_dir, "mesh_configs")
            
            if not os.path.exists(configs_dir):
                self.after(0, lambda: self.active_mesh_status_lbl.configure(text="(Default Factory Mesh)", text_color="#8b949e"))
                return

            matched_profile = None
            for prof_name in os.listdir(configs_dir):
                prof_path = os.path.join(configs_dir, prof_name, "printer.cfg")
                if os.path.exists(prof_path):
                    with open(prof_path, "r") as f:
                        l_blocks = extract_blocks(f.read())
                    if all(p_blocks.get(k) == l_blocks.get(k) for k in p_blocks):
                        matched_profile = prof_name
                        break

            if matched_profile:
                self.after(0, lambda: self.active_mesh_status_lbl.configure(text=f"(Active Mesh: {matched_profile})", text_color="#3fb950"))
            else:
                self.after(0, lambda: self.active_mesh_status_lbl.configure(text="(Default Factory Mesh)", text_color="#8b949e"))

        except Exception as e:
            self.after(0, lambda: self.active_mesh_status_lbl.configure(text="(Failed to verify mesh)", text_color="#da3633"))

    def toggle_oc_mode(self):
        self.oc_mode_active = self.oc_mode_switch.get() == 1
        self.log(f"OC Mode {'ENABLED' if self.oc_mode_active else 'DISABLED'}.", "#10D0DE")
        self.apply_oc_locks()
        
        if self.oc_mode_active:
            self.active_mesh_status_lbl.pack(side="left", padx=20)
            if self.is_monitoring:
                threading.Thread(target=self.scan_active_mesh, daemon=True).start()
        else:
            self.active_mesh_status_lbl.pack_forget()

    def apply_oc_locks(self):
        state = "normal" if self.oc_mode_active else "disabled"
        
        def lock_children(widget):
            for child in widget.winfo_children():
                w_type = child.__class__.__name__
                if w_type in ["CTkButton", "CTkEntry", "CTkSwitch", "CTkOptionMenu", "CTkComboBox"]:
                    child.configure(state=state)
                    
                    if not getattr(child, '_oc_tooltip_bound', False):
                        OCToolTip(child, self)
                        child._oc_tooltip_bound = True
                        
                lock_children(child)
                
        if hasattr(self, 'ssh_acc'):
            lock_children(self.ssh_acc.content)
            
        if hasattr(self, 'mesh_manager_window') and self.mesh_manager_window.winfo_exists():
            lock_children(self.mesh_manager_window)

    def set_move_step_preset(self, val):
        self.move_step = val
        for b in self.step_btns:
            if b.cget("text") == f"{val}mm": b.configure(fg_color="#1f6feb") 
            else: b.configure(fg_color="#21262d")
        self.custom_step_btn.configure(fg_color="#21262d")
        self.log(f"Step set to {val}mm")

    def add_filter_slider(self, master, label, key, mi, ma, st):
        f = ctk.CTkFrame(master, fg_color="transparent")
        f.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(f, text=label, width=65, font=ctk.CTkFont(size=10), anchor="w").pack(side="left")
        
        sl = ctk.CTkSlider(f, from_=mi, to=ma, width=80)
        sl.set(st)
        sl.pack(side="left")
        
        ent = ctk.CTkEntry(f, width=35, height=20, font=ctk.CTkFont(size=10))
        ent.insert(0, str(st))
        ent.pack(side="left", padx=2)
        
        def update_val(val=None):
            try:
                v = float(ent.get()) if val is None else float(val)
                v = max(mi, min(ma, v))
                sl.set(v)
                ent.delete(0, 'end')
                ent.insert(0, str(round(v, 1)) if isinstance(v, float) else str(v))
                self.filter_states[key] = v
            except ValueError: pass
            
        sl.configure(command=update_val)
        ctk.CTkButton(f, text="SET", width=30, height=20, fg_color="#30363d", command=update_val).pack(side="left", padx=2)
        ctk.CTkButton(f, text="Default", width=45, height=20, fg_color="#21262d", command=lambda: update_val(st)).pack(side="left", padx=2)
        return sl

    def apply_factory_hw(self):
        self.hw.apply_factory_default(self.log)
        self.ae_sw.select()
        self.update_ae_ui_state()

    def update_fan_ui(self, name, val, lbl, send=False):
        v = int(val)
        lbl.configure(text=f"{v}%")
        self.fan_states[name] = v
        if send: self.send_fan(name)

    def send_fan(self, name):
        self.send_cmd(403, {"TargetFanSpeed": self.fan_states})
        self.log_traffic(f"Fan Update Sent: {self.fan_states}")

    def update_ae_ui_state(self):
        is_on = self.ae_sw.get()
        self.ae_sw.configure(text="Auto Exposure (ON)" if is_on else "Auto Exposure (OFF)")
        self.ssh_inputs["exposure_absolute"].configure(state="disabled" if is_on else "normal")

    def handle_speed_change(self, val):
        speed_map = {"Slow as Bambu (25%)": 25, "Silent (50%)": 50, "Normal (100%)": 100, "Sport (130%)": 130, "Ludicrous (160%)": 160}
        
        if val == "Pimped Ender speed (200%)":
            self.speed_menu.pack_forget()
            self.speed_warn_lbl.configure(text="⚠️ Risk of crash! All responsability is on the user", text_color="#da3633")
            self.speed_confirm_btn.pack(side="left", padx=2)
            self.speed_cancel_btn.pack(side="left")
            self.speed_confirm_state = 1
        else:
            pct = speed_map.get(val, 100)
            self.send_cmd(403, {"PrintSpeedPct": pct})
            self.speed_warn_lbl.configure(text="⚠️", text_color="#8b949e")
            self.log(f"Print speed changed to {pct}%")
            
    def cancel_200_speed(self):
        self.speed_confirm_btn.pack_forget()
        self.speed_cancel_btn.pack_forget()
        self.speed_menu.pack(side="left")
        self.speed_warn_lbl.configure(text="⚠️", text_color="#8b949e")
        self.speed_var.set("Normal (100%)")
            
    def confirm_200_speed(self):
        if self.speed_confirm_state == 1:
            self.speed_confirm_btn.configure(text="ARE YOU SURE?")
            self.speed_confirm_state = 2
        elif self.speed_confirm_state == 2:
            self.send_cmd(403, {"PrintSpeedPct": 200})
            self.speed_confirm_btn.pack_forget()
            self.speed_cancel_btn.pack_forget()
            self.speed_menu.pack(side="left")
            self.speed_warn_lbl.configure(text="⚠️ 200% ACTIVE", text_color="#da3633")
            self.log("Godspeed. 200% Print Speed Activated.", "#da3633")

    def reset_logging_stats(self):
        self.stat_print_start_time = time.time()
        self.stat_total_anomalies = 0
        self.stat_auto_pauses = 0
        self.stat_final_extrusion = 0.0
        self.strike_counter = 0

    def generate_summary(self):
        elapsed_s = int(time.time() - self.stat_print_start_time)
        hrs, rem = divmod(elapsed_s, 3600)
        mins, secs = divmod(rem, 60)
        time_str = f"{hrs}h {mins}m {secs}s" if hrs > 0 else f"{mins}m {secs}s"

        anom_color = "#3fb950" if self.stat_total_anomalies == 0 else "#d29922" if self.stat_total_anomalies <= 3 else "#da3633"
        pause_color = "#3fb950" if self.stat_auto_pauses == 0 else "#da3633"

        self.sum_time_lbl.configure(text=f"Actual print time was: {time_str}")
        self.sum_anom_lbl.configure(text=f"Number of anomalies during print: {self.stat_total_anomalies}", text_color=anom_color)
        self.sum_pause_lbl.configure(text=f"Auto-stop trigger times: {self.stat_auto_pauses}", text_color=pause_color)
        self.sum_ext_lbl.configure(text=f"Total Extrusion: {self.stat_final_extrusion:.2f} mm²", text_color="#3fb950" if self.stat_final_extrusion > 0 else "#8b949e")

        self.active_prog_container.pack_forget()
        self.summary_container.pack(fill="x", padx=10, pady=10)
        self.log_app("Print Summary Generated.")

    def set_ui_state(self, state, widget=None):
        is_root = (widget is None) 
        if widget is None: widget = self
        
        if widget in [self.connect_btn, self.ip_entry, self.traffic_text, self.ai_text, self.app_text, self.history_text, self.log_cb, getattr(self, 'oc_mode_switch', None)]: 
            return
            
        if isinstance(widget, ctk.CTkButton):
            txt = widget.cget("text")
            if any(x in txt for x in ["▶", "▼", "CONFIRM", "ARE YOU SURE", "Cancel"]): pass 
            else:
                try: widget.configure(state=state)
                except: pass
        else:
            try: widget.configure(state=state)
            except: pass
            
        for child in widget.winfo_children():
            self.set_ui_state(state, child)
            
        if is_root and hasattr(self, 'apply_oc_locks'):
            self.apply_oc_locks()

    def animate_connecting(self):
        if not self.is_connecting: return
        dots = "." * (self.dot_count % 4)
        self.connect_btn.configure(text=f"Connecting{dots}", fg_color="#d29922")
        self.dot_count += 1
        self.after(500, self.animate_connecting)

    def connection_timeout_thread(self):
        for _ in range(10):
            if not self.is_connecting: return 
            time.sleep(1)
        if self.is_connecting: self.after(0, self.handle_timeout)
            
    def handle_timeout(self):
        self.log("Connection Timeout! Printer unreachable.", "#da3633")
        self.disconnect_cleanly()

    def disconnect_cleanly(self):
        self.is_monitoring = False
        self.is_connecting = False
        self.connect_btn.configure(text="🔌 Connect", fg_color="#238636", state="normal")
        self.update_ui_state("OFFLINE", "#8b949e")
        self.set_ui_state("disabled")
        self.video_label.configure(image="", text="Offline")
        self.dashboard_wrapper.pack_forget()
        self.ready_print_lbl.configure(text="")
        
    def force_restart_camera(self):
        self.log("Rebooting video stream...", "#d29922")
        
        self.video_label.configure(image="", text="Camera Loading [■■■□□□□□□□] 30%")
        
        try:
            if hasattr(self, 'cap') and self.cap is not None:
                self.cap.release()
        except: pass
        self.cap = None

        time.sleep(1.5)
        
        self.video_label.configure(text="Camera Loading [■■■■■■■■■■] 100%")
        threading.Thread(target=self.video_loop, daemon=True).start()

    def toggle_monitoring(self):
        if not self.is_monitoring:
            ip = self.ip_entry.get().strip()
            if not ip:
                self.log("Enter IP First!", "#da3633")
                return
            
            self.is_monitoring = True
            self.is_connecting = True
            self.dot_count = 0
            self.connect_btn.configure(state="disabled")
            self.animate_connecting()
            self.log(f"Attempting handshake with {ip}...", "#d29922")
            
            self.last_ping_time = time.time()
            if not self.watchdog_running:
                self.watchdog_running = True
                threading.Thread(target=self.connection_watchdog, daemon=True).start()
            
            self.protocol = CentauriProtocol(ip, self.process_status_update)
            self.protocol.connect()
            self.hw = HardwareController(ip)
            self.file_manager.printer_ip = ip
            
            threading.Thread(target=self.connection_timeout_thread, daemon=True).start()
            
            def initial_poke():
                self.send_cmd(1, {})
                time.sleep(0.5)
                self.send_cmd(0, {})
                self.file_manager.request_file_list()
                
                self.after(0, lambda: self.video_label.configure(text="Camera Loading [■■□□□□□□□□] 20%"))
                time.sleep(1.0)
                self.after(0, lambda: self.video_label.configure(text="Camera Loading [■■■■■■□□□□] 60%"))
                time.sleep(1.0)
                self.after(0, lambda: self.video_label.configure(text="Camera Loading [■■■■■■■■■■] 100%"))
                
                self.send_cmd(386, {"Enable": 1})
                self.log("Camera wake-up command sent.")
                
                if self.oc_mode_active:
                    threading.Thread(target=self.scan_active_mesh, daemon=True).start()
            
            threading.Thread(target=initial_poke, daemon=True).start()
            threading.Thread(target=self.video_loop, daemon=True).start()
            threading.Thread(target=self.tele_loop, daemon=True).start()
        else:
            self.disconnect_cleanly()

    def update_pc_stats(self):
        try:
            cores = psutil.cpu_count(logical=True) or 1
            app_cpu = self.current_process.cpu_percent(interval=None) / cores
            app_ram = self.current_process.memory_info().rss / (1024 * 1024)
            self.app_cpu_lbl.configure(text=f"App CPU: {app_cpu:.1f}%", text_color="#10D0DE")
            self.app_ram_lbl.configure(text=f"App RAM: {app_ram:.1f} MB", text_color="#10D0DE")
        except: pass

        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        tot_gb = mem.total / (1024**3)
        
        cpu_color = "#da3633" if cpu > 90 else "#d29922" if cpu > 50 else "#3fb950"
        ram_color = "#da3633" if mem.percent > 90 else "#d29922" if mem.percent > 50 else "#3fb950"

        self.cpu_lbl.configure(text=f"💻 PC CPU: {cpu}%", text_color=cpu_color)
        self.ram_lbl.configure(text=f"🧠 PC RAM: {mem.percent}% of {tot_gb:.1f}GB", text_color=ram_color)
        self.after(2000, self.update_pc_stats)

    def send_cmd(self, cmd_id, payload={}):
        if self.protocol and self.is_monitoring:
            self.log_traffic(f"-> SENT (Cmd: {cmd_id})")
            self.protocol.send(cmd_id, payload)

    def process_status_update(self, data):
        self.last_ping_time = time.time() 
        if self.is_connecting: self.after(0, self.confirm_connection)
            
        attributes = data.get("Attributes")
        if attributes:
            self.after(0, lambda: self.log_traffic(f"<- RCVD (Attributes)"))
            if self.file_manager:
                self.after(0, lambda: self.file_manager.update_info(attributes.get("UsbDiskStatus", 0), attributes.get("RemainingMemory", 0)))
            
        raw_cmd = data.get("Data", {}).get("Cmd", "Unknown")
        if raw_cmd != "Unknown": self.after(0, lambda cmd=raw_cmd: self.log_traffic(f"<- RCVD (Cmd: {cmd})"))
        
        payload_data = data.get("Data", {}).get("Data", {})

        if raw_cmd == 258:
            files = payload_data.get("FileList", [])
            self.cached_file_list = files  # For Telegram /files
            if self.file_manager:
                self.after(0, lambda: self.file_manager.update_list(files))
            # Notify any pending Telegram file-list requests
            try:
                if hasattr(self, 'telegram') and self.telegram is not None:
                    self.telegram.on_file_list_received(files)
            except Exception:
                pass
            return
        if raw_cmd == 259:
            err = payload_data.get("ErrData", [])
            self.after(0, lambda: self.log(f"Failed to delete: {err}" if err else "Deleted successfully.", "#da3633" if err else "#3fb950"))
            return

        # Cmd 128 (Start Print) ack — see SDCP doc
        if raw_cmd == 128:
            ack = payload_data.get("Ack")
            ack_messages = {
                0: ("OK", True),
                1: ("Printer busy", False),
                2: ("File not found", False),
                3: ("MD5 check failed", False),
                4: ("File I/O failed", False),
                5: ("Invalid resolution", False),
                6: ("Unknown file format", False),
                7: ("Unknown printer model", False),
            }
            label, success = ack_messages.get(ack, (f"Unknown ack code {ack}", False))
            color = "#3fb950" if success else "#da3633"
            icon = "✅" if success else "❌"
            self.after(0, lambda l=label, c=color, i=icon:
                       self.log(f"{i} Print start ack: {l}", c))
            # Notify Telegram if waiting
            try:
                if hasattr(self, 'telegram') and self.telegram is not None:
                    self.telegram.on_print_start_ack(success, label)
            except Exception:
                pass
            return

        st = self.find_in_dict(data, "Status")
        if st:
            self.current_chamber_temp = st.get("TempOfBox", 20.0)

            # --- Cache values for Telegram /temps ---
            self.temp_current_nozzle = float(st.get("TempOfNozzle", 0.0))
            self.temp_target_nozzle = float(st.get("TempTargetNozzle", 0.0))
            self.temp_current_bed = float(st.get("TempOfHotbed", 0.0))
            self.temp_target_bed = float(st.get("TempTargetHotbed", 0.0))
            self.temp_current_chamber = float(st.get("TempOfBox", 0.0))
            # --- end temp cache ---

            for k in ["Nozzle", "Bed", "Chamber"]:
                n_key = "TempOfNozzle" if k=="Nozzle" else "TempOfHotbed" if k=="Bed" else "TempOfBox"
                t_key = "TempTargetNozzle" if k=="Nozzle" else "TempTargetHotbed"
                txt = f"{k}: {st.get(n_key, 0.0):.1f} / {st.get(t_key, 0.0):.1f} °C" if k != "Chamber" else f"Chamber: {st.get(n_key, 0.0):.1f} °C"
                self.after(0, lambda t=txt, key=k: self.temp_widgets[key]["label"].configure(text=t))

            print_info = st.get("PrintInfo") or {}

            def safe_int(val, default):
                try: return int(val)
                except (ValueError, TypeError): return default

            f_name = str(print_info.get("Filename") or "")
            c_ticks = safe_int(print_info.get("CurrentTicks"), 0)
            t_ticks = safe_int(print_info.get("TotalTicks"), 1)
            c_layer = safe_int(print_info.get("CurrentLayer"), 0)
            t_layer = safe_int(print_info.get("TotalLayer"), 0)

            fans = st.get("CurrentFanSpeed", {})
            f_m, f_a, f_b = fans.get("ModelFan", 0), fans.get("AuxiliaryFan", 0), fans.get("BoxFan", 0)

            tot_ext = print_info.get("54 6F 74 61 6C 45 78 74 72 75 73 69 6F 6E 00", 0.0)
            cur_ext = print_info.get("43 75 72 72 65 6E 74 45 78 74 72 75 73 69 6F 6E 00", 0.0)
            self.stat_final_extrusion = max(getattr(self, 'stat_final_extrusion', 0.0), tot_ext)
            coords = st.get("CurrenCoord", "0,0,0")

            is_homing = st.get("Homing", 0) == 1
            raw_status = print_info.get("Status")
            current_txt = "Homing" if is_homing else self.status_map.get(raw_status, "Idle") if raw_status is not None else "Idle"

            if current_txt != self.last_status:
                if current_txt in ["Preparing", "Printing", "Leveling"] and self.last_status in ["Idle", "Stopped", "Print Complete", "OFFLINE"]:
                    self.reset_logging_stats()
                    self.after(0, lambda: [self.dashboard_wrapper.pack(fill="x", padx=10, pady=(5,0)), self.summary_container.pack_forget(), self.active_prog_container.pack(fill="x", expand=True)])
                
                elif current_txt in ["Idle", "Stopped", "Homing"]:
                    self.after(0, lambda: self.dashboard_wrapper.pack_forget())
                    self.mesh_verified_for_print = False 
                
                elif current_txt == "Print Complete" and getattr(self, 'last_status', '') != "Print Complete":
                    self.after(0, self.generate_summary)
                    self.mesh_verified_for_print = False
                    
                    memory_name = getattr(self, 'active_print_filename', "")
                    if "_KMesh_" in memory_name:
                        clean_name = memory_name.split('/')[-1]
                        self.send_cmd(259, {"FileList": [f"/local/{clean_name}"], "FolderList": []})
                        self.log(f"🗑️ Auto-cleaned temporary mesh file: {clean_name}", "#8b949e")
                        self.active_print_filename = ""

            try:
                pct = min(100.0, max(0.0, (c_ticks / t_ticks) * 100)) if (t_ticks > 0 and current_txt == "Printing") else 0.0
            except Exception:
                pct = 0.0

            # --- Cache for Telegram /status ---
            self.print_progress_pct = pct
            self.print_current_layer = c_layer
            self.print_total_layer = t_layer
            self.print_current_ticks = c_ticks
            self.print_total_ticks = t_ticks
            self.print_filename = f_name
            # --- end cache ---

            tele_str = f"Layer: {c_layer} / {t_layer}  |  Progress: {pct:.1f}%  |  Extrusion: {tot_ext:.1f} ({cur_ext:.1f})  |  XYZ: {coords}"
            self.after(0, lambda p=pct/100.0, ts=tele_str: [self.prog_bar.set(p), self.telemetry_lbl.configure(text=ts)])

            clean_name = f_name.split('/')[-1] if f_name else "--"
            if hasattr(self, 'file_lbl'): 
                self.after(0, lambda cn=clean_name: self.file_lbl.configure(text=f"File: {cn}"))

            if f_name:
                self.active_print_filename = f_name

            if current_txt == "Printing":
                try:
                    if c_layer >= 1 and "_KMesh_" in f_name:
                        if not getattr(self, 'mesh_verified_for_print', False):
                            self.mesh_verified_for_print = True 
                            prof_name = f_name.split("_KMesh_")[-1].replace(".gcode", "")
                            self.hw.verify_mesh_configs(prof_name, self.user_dir, f_name.split('/')[-1], self.log, self.mesh_verification_result)
                except Exception as e:
                    print(f"MESH TRIGGER CRASH: {e}") 

            custom_lvl_state = getattr(self, 'is_custom_leveling', 0)

            # ONLY check the filename if it is officially "Printing". Ignore the filename while "Preparing" because Klipper might be lagging!
            if custom_lvl_state > 0 and current_txt == "Printing" and f_name and "konstance_bed_mesh_generator" not in f_name:
                self.is_custom_leveling = 0
                custom_lvl_state = 0

            if custom_lvl_state > 0:
                # Smooth PEI plate firmware path briefly drops to "Idle" between
                # probe points and at end of leveling, instead of staying in
                # "Preparing" the whole time like the textured plate does.
                # We treat Idle as part of the leveling sequence so the rename
                # holds, and we count consecutive Idle cycles to detect when
                # leveling has actually finished (since the smooth-plate path
                # never reaches "Print Complete" — it just settles back to Idle).
                if current_txt in ["Preparing", "Printing"]:
                    current_txt = "Leveling"
                    self.is_custom_leveling = 2
                    self._lvl_idle_streak = 0

                elif current_txt == "Idle" and custom_lvl_state == 2:
                    # We were in Leveling and now status is Idle. Could be a
                    # mid-leveling pause OR the end of leveling. Hold the
                    # "Leveling" label for a few cycles, then if Idle persists
                    # treat it as completion and harvest.
                    self._lvl_idle_streak = getattr(self, '_lvl_idle_streak', 0) + 1
                    if self._lvl_idle_streak < 4:  # ~20 seconds at 5s poll interval
                        current_txt = "Leveling"
                    else:
                        # Idle has held long enough — leveling is done. Trigger
                        # harvest the same way the textured-plate path does.
                        self.send_cmd(259, {"FileList": ["/local/konstance_bed_mesh_generator.gcode"], "FolderList": []})
                        self.log("Auto-cleaned temporary mesh G-code from printer storage.", "#8b949e")

                        prof_name = getattr(self, 'target_mesh_profile', "Default_Mesh")
                        try:
                            if hasattr(self.hw, 'harvest_configs'):
                                self.hw.harvest_configs(prof_name, getattr(self, 'user_dir', ''), self.log)

                                if self.oc_mode_active:
                                    threading.Thread(target=self.scan_active_mesh, daemon=True).start()
                            else:
                                self.log("Harvest failed: hardware_control.py missing function!", "#da3633")
                        except Exception as e:
                            self.log(f"Harvest trigger error: {e}", "#da3633")

                        if hasattr(self, 'mesh_manager_window') and self.mesh_manager_window.winfo_exists():
                            self.after(1500, self.mesh_manager_window.unlock_ui_after_leveling)

                        self.is_custom_leveling = 0
                        self._lvl_idle_streak = 0

                elif current_txt in ["Stopped", "Print Complete", "OFFLINE"]:
                    if custom_lvl_state == 2:
                        if current_txt == "Print Complete":
                            self.send_cmd(259, {"FileList": ["/local/konstance_bed_mesh_generator.gcode"], "FolderList": []})
                            self.log("Auto-cleaned temporary mesh G-code from printer storage.", "#8b949e")

                        prof_name = getattr(self, 'target_mesh_profile', "Default_Mesh")
                        try:
                            if hasattr(self.hw, 'harvest_configs'):
                                self.hw.harvest_configs(prof_name, getattr(self, 'user_dir', ''), self.log)
                                
                                if self.oc_mode_active:
                                    threading.Thread(target=self.scan_active_mesh, daemon=True).start()
                            else:
                                self.log("Harvest failed: hardware_control.py missing function!", "#da3633")
                        except Exception as e:
                            self.log(f"Harvest trigger error: {e}", "#da3633")

                        if hasattr(self, 'mesh_manager_window') and self.mesh_manager_window.winfo_exists():
                            self.after(1500, self.mesh_manager_window.unlock_ui_after_leveling)
                            
                    self.is_custom_leveling = 0
                    self._lvl_idle_streak = 0

            if current_txt == "Leveling":
                self.after(0, lambda: self.prog_bar.pack_forget())
            else:
                self.after(0, lambda: self.prog_bar.pack(fill="x", pady=4, after=self.file_lbl))

            self.after(0, lambda t=current_txt, c=self.color_palette.get(current_txt, "#8b949e"): self.update_ui_state(t, c))
            self.last_status = current_txt

    def confirm_connection(self):
        self.is_connecting = False
        self.connect_btn.configure(state="normal", text="🛑 Disconnect", fg_color="#da3633")
        self.set_ui_state("normal")
        if not self.konstance_active: self.auto_pause_switch.configure(state="disabled")
        self.log("Connected Successfully!", "#3fb950")

    def update_ui_state(self, txt, clr):
        self.state_lbl.configure(text=f"Printer: {txt}", text_color=clr)
        
        if txt in ["Idle", "Print Complete"]: self.ready_print_lbl.configure(text="Ready to Print")
        else: self.ready_print_lbl.configure(text="")
            
        if not self.cooldown_active:
            can_stop = txt in ["Printing", "Preparing", "Pausing", "Paused", "Stopping", "Homing"]
            try:
                self.pause_btn.configure(state="normal" if txt in ["Printing", "Preparing"] else "disabled")
                self.stop_btn.configure(state="normal" if can_stop else "disabled")
                self.resume_btn.configure(state="normal" if txt == "Paused" else "disabled")
            except: pass

    def manual_refresh(self):
        if self.is_monitoring:
            self.send_cmd(1, {})
            time.sleep(0.2)
            self.send_cmd(0, {})

    def find_in_dict(self, data, key):
        if key in data: return data[key]
        for k, v in data.items():
            if isinstance(v, dict):
                res = self.find_in_dict(v, key)
                if res is not None: return res
        return None

    def tele_loop(self):
        while self.is_monitoring:
            self.send_cmd(0, {})
            time.sleep(5.0) 

    def video_loop(self):
        try:
            url = f"http://{self.protocol.ip}:3031/video"
            cap = cv2.VideoCapture(url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            
            retry_count = 0
            l_scan, l_boxes = 0, []
            
            time.sleep(3.0)
            
            while self.is_monitoring:
                if not cap.isOpened():
                    retry_count += 1
                    if retry_count > 5:
                        self.after(0, lambda: self.video_label.configure(image="", text="Cannot connect camera. Retrying..."))
                    time.sleep(2)
                    cap.open(url)
                    continue
                    
                ret, frame = cap.read()
                if not ret:
                    retry_count += 1
                    if retry_count > 2: 
                        self.after(0, lambda: self.video_label.configure(image="", text="Video feed lost. Watchdog monitoring..."))
                        cap.release()
                        time.sleep(3)
                        cap.open(url)
                        retry_count = 0
                    time.sleep(0.5)
                    continue
                    
                retry_count = 0 
                self.frame_height, self.frame_width = frame.shape[:2]
                
                try:
                    if self.filter_states["Enabled"]: 
                        frame = CentauriVision.apply_filters(frame, self.filter_states)
                        
                    if self.konstance_active and self.model_loaded and self.model is not None and time.time() - l_scan >= self.scan_slider.get():
                        res = self.model(frame, conf=self.conf_slider.get()/100.0, verbose=False)
                        l_boxes = [b for b in res[0].boxes if not any(zx1 < (int(b.xyxy[0][0]) + int(b.xyxy[0][2]))/2 < zx2 and zy1 < (int(b.xyxy[0][1]) + int(b.xyxy[0][3]))/2 < zy2 for (zx1, zy1, zx2, zy2) in self.ignore_zones)]
                        l_scan = time.time()
                        
                        if l_boxes: self.log_ai(f"Detection: {len(l_boxes)} anomalies found.")
                        
                        if self.last_status == "Printing":
                            if l_boxes:
                                self.last_detection_time = time.time()
                                self.strike_counter += 1
                                self.stat_total_anomalies += 1
                                self.after(0, lambda: self.log(f"🚨 Fault! Strikes: {self.strike_counter}/{int(self.strike_slider.get())}", "#da3633"))
                                
                                if self.strike_counter >= int(self.strike_slider.get()):
                                    # Decide auto-pause FIRST so we can tell Telegram
                                    # whether to show Resume (paused) or Pause (still printing) buttons.
                                    will_auto_pause = bool(self.auto_pause_switch.get())

                                    # --- Telegram warn branch ---
                                    try:
                                        if self.telegram_warn_switch.get() and self.telegram.is_running:
                                            top_conf = 0.0
                                            for b in l_boxes:
                                                try:
                                                    c = float(b.conf[0])
                                                    if c > top_conf: top_conf = c
                                                except Exception:
                                                    pass
                                            annotated = CentauriVision.draw_overlays(
                                                frame.copy(), l_boxes, self.ignore_zones,
                                                self.current_drawing, self.draw_mode
                                            )
                                            if will_auto_pause:
                                                caption = (
                                                    f"🛑 Anomaly detected — print AUTO-PAUSED!\n"
                                                    f"Confidence: {int(top_conf*100)}%\n"
                                                    f"Tap Resume if it was a false alarm."
                                                )
                                            else:
                                                caption = (
                                                    f"⚠️ Anomaly detected!\n"
                                                    f"Confidence: {int(top_conf*100)}%\n"
                                                    f"Strikes: {self.strike_counter}/{int(self.strike_slider.get())}"
                                                )
                                            if self.telegram.send_alert(annotated, caption, auto_paused=will_auto_pause):
                                                self.after(0, lambda: self.log_ai("📱 Telegram alert sent."))
                                    except Exception as tg_e:
                                        self.after(0, lambda err=tg_e: self.log(
                                            f"Telegram alert error: {err}", "#da3633"))
                                    # --- end telegram ---

                                    if will_auto_pause:
                                        self.stat_auto_pauses += 1
                                        self.trigger_action("pause")
                                        self.log_ai("AUTO-PAUSE TRIGGERED.")
                                    self.strike_counter = 0
                            else:
                                if self.strike_counter > 0 and (time.time() - self.last_detection_time) > 10.0:
                                    self.strike_counter = 0
                                    self.after(0, lambda: self.log("Anomalies cleared. Strikes reset to 0.", "#3fb950"))
                                
                    vw = self.video_label.winfo_width()
                    vh = self.video_label.winfo_height()
                    
                    if vw <= 10 or vh <= 10 or self.frame_height == 0:
                        time.sleep(0.03)
                        continue
                        
                    rat = self.frame_width / self.frame_height
                    
                    if vw/vh <= rat:
                        fw, fh = vw, int(vw / rat)
                    else:
                        fw, fh = int(vh * rat), vh
                        
                    self.vid_fw, self.vid_fh = fw, fh
                    
                    frame = CentauriVision.draw_overlays(frame, l_boxes, self.ignore_zones, self.current_drawing, self.draw_mode)

                    # --- Telegram: cache the latest annotated frame for /snapshot ---
                    try:
                        with self.latest_frame_lock:
                            self.latest_frame = frame.copy()
                    except Exception:
                        pass
                    # --- end telegram ---

                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    self.after(0, lambda i=ctk.CTkImage(light_image=img, dark_image=img, size=(fw, fh)): self.video_label.configure(image=i, text=""))
                    time.sleep(0.03)
                except Exception as loop_e:
                    self.after(0, lambda err=loop_e: self.log(f"Camera Loop Error: {err}", "#da3633"))
                    time.sleep(1)
        except Exception as fatal_e:
            self.after(0, lambda err=fatal_e: self.log(f"FATAL CAMERA CRASH: {err}", "#da3633"))

    def set_move_step_preset(self, val):
        self.move_step = val
        for b in self.step_btns:
            if b.cget("text") == f"{val}mm": b.configure(fg_color="#30363d")
            else: b.configure(fg_color="#21262d")
        self.custom_step_btn.configure(fg_color="#21262d")
        self.log(f"Step set to {val}mm")
        
    def mesh_verification_result(self, success, log_path):
        if success:
            self.after(0, lambda: [
                self.log("✅ Mesh check OK! Printer retained the custom config.", "#3fb950"),
                self.ready_print_lbl.configure(text="Custom Mesh Verified", text_color="#3fb950")
            ])
        else:
            self.after(0, lambda: [
                self.trigger_action("pause"), 
                self.log(f"❌ MESH CORRUPTION DETECTED! Print Auto-Paused.", "#da3633"),
                self.log(f"Check log: {log_path}", "#da3633"),
                self.ready_print_lbl.configure(text="MESH ERROR - PAUSED", text_color="#da3633")
            ])
            try: os.startfile(log_path)
            except: pass

    def set_move_step_custom(self, val):
        try:
            self.move_step = max(0.1, min(200.0, float(val)))
            self.log(f"Movement step set to {self.move_step}mm")
            for b in self.step_btns: b.configure(fg_color="#21262d")
            self.custom_step_btn.configure(fg_color="#1f6feb")
        except ValueError: self.log("Invalid move step", "#da3633")

    def trigger_action(self, a):
        self.hide_stop_confirm()
        if not self.cooldown_active:
            self.send_cmd({"pause": 129, "resume": 131, "stop": 130}[a])
            self.start_cooldown()

    def push_batch_ssh(self):
        config = {"exposure_auto": 3 if self.ae_sw.get() else 1}
        for key, ent in self.ssh_inputs.items():
            val_raw = ent.get().strip()
            if val_raw: 
                try: 
                    val = max(self.ssh_ranges[key][0], min(self.ssh_ranges[key][1], int(val_raw)))
                    ent.delete(0, 'end')
                    ent.insert(0, str(val))
                    config[key] = val
                except ValueError: self.log(f"Invalid input for {key}", "#da3633")
        self.hw.apply_batch_config(config, self.log)

    def add_filter_row(self, master, name):
        f = ctk.CTkFrame(master, fg_color="transparent")
        f.pack(fill="x", padx=5)
        sw = ctk.CTkSwitch(f, text=name, font=ctk.CTkFont(size=11), command=lambda k=name.split(" ")[0]: self.update_filter_state(k, sw.get()))
        sw.pack(side="left", pady=2)

    def add_accordion(self, title):
        a = CTkAccordion(self.sidebar_frame, title)
        a.pack(fill="x", padx=5, pady=5)
        return a

    def start_cooldown(self):
        self.cooldown_active = True
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        threading.Thread(target=self.cd_timer, daemon=True).start()
        
    def cd_timer(self):
        for i in range(10, 0, -1): 
            self.after(0, lambda x=i: [self.pause_btn.configure(text=f"P ({x})"), self.resume_btn.configure(text=f"R ({x})"), self.stop_btn.configure(text=f"S ({x})")])
            time.sleep(1)
        self.after(0, lambda: [
            self.pause_btn.configure(text="Pause", state="normal" if self.last_status in ["Printing", "Preparing"] else "disabled"), 
            self.resume_btn.configure(text="Resume", state="normal" if self.last_status == "Paused" else "disabled"), 
            self.stop_btn.configure(text="Stop", state="normal" if self.last_status in ["Printing", "Preparing", "Pausing", "Paused", "Stopping", "Homing"] else "disabled"), 
            setattr(self, 'cooldown_active', False), 
            self.update_ui_state(self.last_status, self.color_palette.get(self.last_status, "#8b949e"))
        ])
        
    def toggle_led(self):
        self.led_state = not self.led_state
        self.send_cmd(403, {"LightStatus": {"SecondLight": int(self.led_state)}})
        self.led_btn.configure(text="💡 LED ON" if self.led_state else "💡 LED OFF", fg_color="#238636" if self.led_state else "#21262d")
        
    def show_stop_confirm(self): 
        self.stop_btn.pack_forget()
        self.confirm_frame.pack(side="left", padx=2)
        
    def hide_stop_confirm(self): 
        self.confirm_frame.pack_forget()
        self.stop_btn.pack(side="left", padx=2)
        
    def toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible: 
            self.mid_frame.grid_columnconfigure(1, weight=0, minsize=320)
            self.sidebar_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        else: 
            self.sidebar_frame.grid_forget()
            self.mid_frame.grid_columnconfigure(1, weight=0, minsize=0)
            
    def toggle_draw_mode(self): 
        self.draw_mode = not self.draw_mode
        self.draw_btn.configure(fg_color="#1f6feb" if self.draw_mode else "#30363d")
        
    def map_coords(self, e):
        vw = self.video_label.winfo_width()
        vh = self.video_label.winfo_height()

        if vw <= 10 or vh <= 10: 
            return 0, 0

        fw = getattr(self, 'vid_fw', vw)
        fh = getattr(self, 'vid_fh', vh)

        offset_x = (vw - fw) / 2
        offset_y = (vh - fh) / 2

        img_x = e.x - offset_x
        img_y = e.y - offset_y

        img_x = max(0, min(fw, img_x))
        img_y = max(0, min(fh, img_y))

        vid_w = getattr(self, 'frame_width', 1920)
        vid_h = getattr(self, 'frame_height', 1080)

        native_x = int((img_x / fw) * vid_w) if fw > 0 else 0
        native_y = int((img_y / fh) * vid_h) if fh > 0 else 0

        return native_x, native_y

    def on_mouse_down(self, e): 
        if self.draw_mode: 
            self.start_rx, self.start_ry = self.map_coords(e)
            self.is_dragging = True
            self.current_drawing = None
            
    def on_mouse_drag(self, e): 
        if self.draw_mode and self.is_dragging: 
            cur_rx, cur_ry = self.map_coords(e)
            self.current_drawing = (self.start_rx, self.start_ry, cur_rx, cur_ry)
            
    def on_mouse_up(self, e):
        if self.draw_mode and self.is_dragging:
            self.is_dragging = False
            end_rx, end_ry = self.map_coords(e)

            fx1, fx2 = min(self.start_rx, end_rx), max(self.start_rx, end_rx)
            fy1, fy2 = min(self.start_ry, end_ry), max(self.start_ry, end_ry)

            if fx2 - fx1 > 10 and fy2 - fy1 > 10: 
                self.ignore_zones.append((fx1, fy1, fx2, fy2))
                self._save_ignore_zones()
                
            self.current_drawing = None
            self.toggle_draw_mode()
            
    def _save_ignore_zones(self):
        """Persist ignore zones to disk so they survive app restarts."""
        try:
            zones_path = os.path.join(get_user_data_dir(), "ignore_zones.json")
            with open(zones_path, "w", encoding="utf-8") as zf:
                json.dump([list(z) for z in self.ignore_zones], zf)
        except Exception as e:
            self.log(f"Failed to save ignore zones: {e}", "#da3633")

    def _clear_all_zones(self):
        """Clear all ignore zones and persist the empty state."""
        self.ignore_zones.clear()
        self._save_ignore_zones()
        self.log("All ignore zones cleared.", "#8b949e")

    def update_hw_creds(self): 
        self.hw.update_credentials(self.ssh_user.get(), self.ssh_pwd.get())
        self.log("Credentials Updated.")
        
    def update_filter_state(self, k, v): self.filter_states[k] = v
        
    def undo_zone(self): 
        if self.ignore_zones:
            self.ignore_zones.pop()
            self._save_ignore_zones()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--viewer":
        import viewer_app
        viewer_app.main()
    else:
        app = CentauriWatchdog()
        app.mainloop()