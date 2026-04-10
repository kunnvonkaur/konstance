import customtkinter as ctk
from datetime import datetime
import subprocess
import sys
import os
import threading
import hashlib
import uuid
import requests

class CentauriFileManager:
    def __init__(self, master, log_callback, parent_frame, send_callback):
        self.master = master
        self.send_cmd = send_callback 
        self.log = log_callback
        self.parent_frame = parent_frame
        self.current_path = "/local/"
        self.printer_ip = None  
        
        self.all_files = []
        self.current_page = 0
        self.files_per_page = 10

        info_frame = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        info_frame.pack(fill="x", padx=5, pady=5)

        self.lbl_usb_status = ctk.CTkLabel(info_frame, text="USB Status: WAITING...", font=ctk.CTkFont(size=11, weight="bold"), anchor="w", text_color="#8b949e")
        self.lbl_usb_status.pack(fill="x")
        self.lbl_local_mem = ctk.CTkLabel(info_frame, text="Onboard available memory: WAITING...", font=ctk.CTkFont(size=11, weight="bold"), anchor="w", text_color="#8b949e")
        self.lbl_local_mem.pack(fill="x")

        ctrl_frame = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        ctrl_frame.pack(fill="x", padx=5, pady=5)

        self.path_seg = ctk.CTkSegmentedButton(ctrl_frame, values=["/local/", "/video/"], command=self.change_path, font=ctk.CTkFont(size=10))
        self.path_seg.set("/local/")
        self.path_seg.pack(side="left", expand=True, fill="x", padx=(0, 2))

        ctk.CTkButton(ctrl_frame, text="🔄", width=30, height=22, font=ctk.CTkFont(size=10), command=self.request_file_list).pack(side="right")

        # Increased height slightly to fit all 10 items comfortably
        self.file_list_frame = ctk.CTkFrame(self.parent_frame, fg_color="#0d1117", height=340)
        self.file_list_frame.pack(fill="x", padx=5, pady=5)
        self.file_list_frame.pack_propagate(False)

        self.page_frame = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.page_frame.pack(fill="x", padx=5, pady=2)
        
        self.btn_prev = ctk.CTkButton(self.page_frame, text="◀ Prev", width=60, height=20, font=ctk.CTkFont(size=10), state="disabled", command=lambda: self.change_page(-1))
        self.btn_prev.pack(side="left")
        
        self.lbl_page = ctk.CTkLabel(self.page_frame, text="Page 1/1", font=ctk.CTkFont(size=10))
        self.lbl_page.pack(side="left", expand=True)
        
        self.btn_next = ctk.CTkButton(self.page_frame, text="Next ▶", width=60, height=20, font=ctk.CTkFont(size=10), state="disabled", command=lambda: self.change_page(1))
        self.btn_next.pack(side="right")
        
        self.btn_upload = ctk.CTkButton(self.parent_frame, text="Upload to printer", fg_color="#1f6feb", hover_color="#388bfd", font=ctk.CTkFont(weight="bold"), command=self.upload_file)
        self.btn_upload.pack(fill="x", padx=5, pady=(5, 10))
        
        
    def upload_file(self):
        if not self.printer_ip:
            self.log("Cannot upload: Not connected to printer.", "#da3633")
            return

        # 1. Open a file dialog to pick the G-code
        import tkinter.filedialog as filedialog
        file_path = filedialog.askopenfilename(
            title="Select G-code to Upload",
            filetypes=[("G-code Files", "*.gcode"), ("All Files", "*.*")]
        )
        
        if not file_path:
            return 

        self.upload_path_to_printer(file_path, on_done=None)

    def upload_path_to_printer(self, file_path, on_done=None):
        """Reusable chunked upload. Can be called by the dialog flow OR by Telegram.
        on_done: optional callable(success: bool, error_msg: str) run when upload finishes."""
        if not self.printer_ip:
            self.log("Cannot upload: Not connected to printer.", "#da3633")
            if on_done: on_done(False, "Not connected to printer")
            return

        file_name = os.path.basename(file_path)
        try:
            total_size = os.path.getsize(file_path)
        except OSError as e:
            self.log(f"Upload failed: {e}", "#da3633")
            if on_done: on_done(False, str(e))
            return

        self.log(f"Uploading {file_name} ({total_size / (1024*1024):.1f} MB)...", "#d29922")

        def process_upload():
            try:
                # RAM-safe MD5 Calculation
                m = hashlib.md5()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        m.update(chunk)
                file_md5 = m.hexdigest()

                upload_uuid = str(uuid.uuid4().hex)
                url = f"http://{self.printer_ip}:3030/uploadFile/upload"
                chunk_size = 1024 * 1024
                offset = 0
                last_logged_pct = 0

                with open(file_path, "rb") as f:
                    while offset < total_size:
                        chunk = f.read(chunk_size)
                        if not chunk: break

                        data = {
                            "TotalSize": str(total_size),
                            "Uuid": upload_uuid,
                            "Offset": str(offset),
                            "Check": "1",
                            "S-File-MD5": file_md5
                        }
                        files = {"File": (file_name, chunk, "application/octet-stream")}

                        res = requests.post(url, data=data, files=files, timeout=15)

                        if res.status_code != 200 or not res.json().get("success"):
                            err = f"Upload rejected at {offset/(1024*1024):.1f}MB: {res.text}"
                            self.log(err, "#da3633")
                            if on_done: on_done(False, err)
                            return

                        offset += len(chunk)

                        pct = int((offset / total_size) * 100)
                        if pct - last_logged_pct >= 10 or offset >= total_size:
                            self.log(f"Upload progress: {pct}%", "#10D0DE")
                            last_logged_pct = pct

                self.log(f"✅ Successfully uploaded {file_name}!", "#3fb950")
                self.master.after(1000, self.request_file_list)
                if on_done: on_done(True, "")

            except Exception as e:
                self.log(f"Upload failed: {e}", "#da3633")
                if on_done: on_done(False, str(e))

        threading.Thread(target=process_upload, daemon=True).start()

    def change_path(self, new_path):
        self.current_path = new_path
        self.current_page = 0
        self.request_file_list()

    def request_file_list(self):
        payload = {"Url": self.current_path}
        self.send_cmd(258, payload)
        self.log(f"Requesting {self.current_path} files...")

    def update_info(self, usb_status, remaining_mem):
        if usb_status == 1:
            self.lbl_usb_status.configure(text="USB Status: CONNECTED", text_color="#3fb950")
        else:
            self.lbl_usb_status.configure(text="USB Status: DISCONNECTED", text_color="#da3633")

        gb_calc = remaining_mem / (1024**3)
        self.lbl_local_mem.configure(text=f"Onboard available memory: {gb_calc:.2f} GB", text_color="#10D0DE")

    def delete_file(self, file_path, is_folder):
        payload = {"FileList": [], "FolderList": []}
        if is_folder:
            payload["FolderList"].append(file_path)
        else:
            payload["FileList"].append(file_path)
            
        self.send_cmd(259, payload)
        self.log(f"Delete sent: {file_path}")
        self.master.after(1500, self.request_file_list)

    def update_list(self, files_array):
        self.all_files = files_array
        self.current_page = 0
        self.render_page()

    def change_page(self, delta):
        self.current_page += delta
        self.render_page()

    def render_page(self):
        for widget in self.file_list_frame.winfo_children():
            widget.destroy()

        total_files = len(self.all_files)
        if total_files == 0:
            ctk.CTkLabel(self.file_list_frame, text=f"No files found in {self.current_path}", text_color="#8b949e", font=ctk.CTkFont(size=11)).pack(pady=20)
            self.lbl_page.configure(text="Page 1/1")
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            return

        total_pages = max(1, (total_files + self.files_per_page - 1) // self.files_per_page)
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        
        start_idx = self.current_page * self.files_per_page
        end_idx = min(start_idx + self.files_per_page, total_files)
        page_files = self.all_files[start_idx:end_idx]

        for item in page_files:
            name = item.get("name", "Unknown")
            file_type = item.get("type", 1) 
            
            raw_time = item.get("CreateTime", 0)
            time_str = ""
            if isinstance(raw_time, (int, float)) and raw_time > 0:
                time_str = datetime.fromtimestamp(raw_time).strftime('%Y-%m-%d %H:%M')

            display_name = name.split('/')[-1] if '/' in name else name
            short_name = display_name if len(display_name) <= 12 else display_name[:9] + "..."
            icon = "📁" if file_type == 0 else "📄"

            row = ctk.CTkFrame(self.file_list_frame, fg_color="#161b22", corner_radius=3)
            row.pack(fill="x", pady=1, padx=2)

            # Added cursor="hand2" to make it clear it's clickable
            lbl_name = ctk.CTkLabel(row, text=f"{icon} {short_name}", width=120, anchor="w", font=ctk.CTkFont(size=11), cursor="hand2")
            lbl_name.pack(side="left", padx=2, pady=2)
            
            lbl_name.bind("<Enter>", lambda e, full=f"{icon} {display_name}", l=lbl_name: l.configure(text=full))
            lbl_name.bind("<Leave>", lambda e, orig=f"{icon} {short_name}", l=lbl_name: l.configure(text=orig))

            def launch_viewer(e=None, f_path=name):
                if not self.printer_ip:
                    self.log("Cannot open viewer: Not connected.", "#da3633")
                    return
                
                self.log(f"Preparing 3D Engine for {f_path}...", "#1f6feb")
                
                app_x = self.master.winfo_rootx()
                app_y = self.master.winfo_rooty()
                app_w = self.master.winfo_width()
                app_h = self.master.winfo_height()
                
                popup_w = 900
                popup_h = 700
                
                center_x = app_x + (app_w // 2) - (popup_w // 2)
                center_y = app_y + (app_h // 2) - (popup_h // 2)

                # 1. Grab the OC Mode state from main.py
                oc_flag = "1" if getattr(self.master, 'oc_mode_active', False) else "0"
                
                # 2. Prevent subprocess from crashing if the printer IP is None
                safe_ip = str(self.printer_ip) if self.printer_ip else "0.0.0.0"
                
                if getattr(sys, 'frozen', False):
                    cmd = [sys.executable, "--viewer", f_path, str(center_x), str(center_y), safe_ip, oc_flag]
                else:
                    cmd = [sys.executable, "viewer_app.py", f_path, str(center_x), str(center_y), safe_ip, oc_flag]

                # 3. ACTUALLY LAUNCH THE APP (This was missing!)
                try:
                    import subprocess
                    subprocess.Popen(cmd)
                except Exception as e:
                    self.log(f"Failed to launch Viewer: {e}", "#da3633")

            lbl_name.bind("<Double-Button-1>", launch_viewer)

            # --- THE JUMP FIX: A single fixed-position frame for all buttons ---
            # --- THE JUMP FIX: A single fixed-position frame for all buttons ---
            action_frame = ctk.CTkFrame(row, fg_color="transparent")
            action_frame.pack(side="left", expand=True, anchor="e", padx=10)

            btn_open = ctk.CTkButton(action_frame, text="Open", fg_color="#238636", width=40, height=22, font=ctk.CTkFont(size=10, weight="bold"), command=lambda f=name: launch_viewer(None, f))
            btn_del = ctk.CTkButton(action_frame, text="X", fg_color="#a40e26", width=24, height=22, font=ctk.CTkFont(size=10, weight="bold"))
            
            btn_confirm = ctk.CTkButton(action_frame, text="Confirm", fg_color="#a40e26", width=45, height=22, font=ctk.CTkFont(size=10), command=lambda f=name, t=file_type: self.delete_file(f, t==0))
            btn_cancel = ctk.CTkButton(action_frame, text="Cancel", fg_color="#30363d", width=45, height=22, font=ctk.CTkFont(size=10))

            # FIX: By defining arguments here, Python "locks" the exact buttons for this specific row!
            def show_confirm(bo=btn_open, bd=btn_del, bc=btn_confirm, bca=btn_cancel):
                bo.pack_forget()
                bd.pack_forget()
                bc.pack(side="left", padx=2)
                bca.pack(side="left", padx=2)
                
            def hide_confirm(bo=btn_open, bd=btn_del, bc=btn_confirm, bca=btn_cancel, ft=file_type):
                bc.pack_forget()
                bca.pack_forget()
                if ft == 1:
                    bo.pack(side="left", padx=2)
                bd.pack(side="left", padx=2)

            btn_del.configure(command=show_confirm)
            btn_cancel.configure(command=hide_confirm)

            # Pack initial state
            if file_type == 1:
                btn_open.pack(side="left", padx=2)
            btn_del.pack(side="left", padx=2)

            # Timestamp stays safely on the absolute right
            if time_str:
                lbl_time = ctk.CTkLabel(row, text=time_str, text_color="#8b949e", font=ctk.CTkFont(size=10))
                lbl_time.pack(side="right", padx=10)

        self.lbl_page.configure(text=f"Page {self.current_page + 1}/{total_pages}")
        self.btn_prev.configure(state="normal" if self.current_page > 0 else "disabled")
        self.btn_next.configure(state="normal" if self.current_page < total_pages - 1 else "disabled")