import customtkinter as ctk
import os
import requests
import hashlib
import threading
import uuid
import time
import math
import shutil
import paramiko  # Added to handle our temporary SSH connections safely

class BedMeshManager(ctk.CTkToplevel):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.title("Bed Mesh Manager")
        self.protocol("WM_DELETE_WINDOW", self.on_close_attempt)
           
        self.geometry("620x780") 
        self.configure(fg_color="#161b22") 
        
        self.update_idletasks()
        app_x = self.master.winfo_rootx()
        app_y = self.master.winfo_rooty()
        app_w = self.master.winfo_width()
        app_h = self.master.winfo_height()
        
        popup_w = 620
        popup_h = 780
        
        center_x = app_x + (app_w // 2) - (popup_w // 2)
        center_y = app_y + (app_h // 2) - (popup_h // 2)
        self.geometry(f"{popup_w}x{popup_h}+{center_x}+{center_y}")
        
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

        self.current_gcode = ""
        self.countdown_val = 5
        self.countdown_active = False
        self.is_uploading = False

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        lbl_title = ctk.CTkLabel(self, text="Bed Mesh Generator", font=ctk.CTkFont(size=18, weight="bold"), text_color="#10D0DE")
        lbl_title.pack(pady=(15, 5))

        # --- Profile Name Input ---
        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.pack(pady=5)
        ctk.CTkLabel(name_frame, text="Profile Name:", width=130, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=5)
        self.profile_var = ctk.StringVar(value="My_Custom_Mesh")
        self.profile_entry = ctk.CTkEntry(name_frame, textvariable=self.profile_var, width=160)
        self.profile_entry.pack(side="left", padx=5)

        # --- Text Inputs ---
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.pack(pady=5)

        ctk.CTkLabel(input_frame, text="Nozzle Temp (°C):", width=130, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=5, pady=5)
        self.nozzle_var = ctk.StringVar(value="150")
        self.nozzle_var.trace_add("write", lambda *args: self.enforce_max_limit(self.nozzle_var, 280))
        self.nozzle_entry = ctk.CTkEntry(input_frame, textvariable=self.nozzle_var, width=80)
        self.nozzle_entry.grid(row=0, column=1, padx=5, pady=5)
        ctk.CTkLabel(input_frame, text="(Min: Chamber+5, Max 280)", font=ctk.CTkFont(size=11), text_color="#8b949e").grid(row=0, column=2, padx=5, sticky="w")

        ctk.CTkLabel(input_frame, text="Bed Temp (°C):", width=130, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).grid(row=1, column=0, padx=5, pady=5)
        self.bed_var = ctk.StringVar(value="60")
        self.bed_var.trace_add("write", lambda *args: self.enforce_max_limit(self.bed_var, 105))
        self.bed_entry = ctk.CTkEntry(input_frame, textvariable=self.bed_var, width=80)
        self.bed_entry.grid(row=1, column=1, padx=5, pady=5)
        ctk.CTkLabel(input_frame, text="(Min: Chamber+5, Max 105)", font=ctk.CTkFont(size=11), text_color="#8b949e").grid(row=1, column=2, padx=5, sticky="w")

        ctk.CTkLabel(input_frame, text="Nozzle Wipe (Times):", width=130, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).grid(row=2, column=0, padx=5, pady=5)
        self.wipe_var = ctk.StringVar(value="0")
        self.wipe_var.trace_add("write", lambda *args: self.enforce_max_limit(self.wipe_var, 5))
        self.wipe_entry = ctk.CTkEntry(input_frame, textvariable=self.wipe_var, width=80)
        self.wipe_entry.grid(row=2, column=1, padx=5, pady=5)
        ctk.CTkLabel(input_frame, text="(Max 5)", font=ctk.CTkFont(size=11), text_color="#8b949e").grid(row=2, column=2, padx=5, sticky="w")

        # --- Plate Selection ---
        plate_frame = ctk.CTkFrame(self, fg_color="transparent")
        plate_frame.pack(pady=10)
        
        self.plate_var = ctk.IntVar(value=0) 
        ctk.CTkRadioButton(plate_frame, text="Textured PEI Plate", variable=self.plate_var, value=0, font=ctk.CTkFont(size=12)).pack(side="left", padx=15)
        ctk.CTkRadioButton(plate_frame, text="Smooth PEI Plate", variable=self.plate_var, value=1, font=ctk.CTkFont(size=12)).pack(side="left", padx=15)

        # --- Sequential Action Buttons ---
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)

        self.btn_save = ctk.CTkButton(btn_frame, text="Save Values", fg_color="#238636", hover_color="#2ea043", width=100, state="normal", command=self.save_values)
        self.btn_save.pack(side="left", padx=5)
        ctk.CTkLabel(btn_frame, text="➔", font=ctk.CTkFont(size=16, weight="bold"), text_color="#8b949e").pack(side="left", padx=5)
        
        self.btn_gcode = ctk.CTkButton(btn_frame, text="Show G-code", fg_color="#1f6feb", hover_color="#388bfd", width=100, state="disabled", command=self.show_gcode)
        self.btn_gcode.pack(side="left", padx=5)
        ctk.CTkLabel(btn_frame, text="➔", font=ctk.CTkFont(size=16, weight="bold"), text_color="#8b949e").pack(side="left", padx=5)
        
        self.btn_level = ctk.CTkButton(btn_frame, text="Level Bed", fg_color="#d29922", hover_color="#b07d1b", width=100, state="disabled", command=self.start_leveling_process)
        self.btn_level.pack(side="left", padx=5)

        # G-code / Info Display Box
        self.gcode_box = ctk.CTkTextbox(self, width=480, height=120, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#0d1117")
        self.gcode_box.pack(pady=10)
        
        # --- Manage Meshes Section ---
        ctk.CTkFrame(self, height=2, fg_color="#30363d").pack(fill="x", padx=20, pady=15)
        
        ctk.CTkLabel(self, text="Manage Saved Meshes", font=ctk.CTkFont(size=14, weight="bold"), text_color="#10D0DE").pack(pady=(0, 5))
        
        self.active_mesh_lbl = ctk.CTkLabel(self, text="Status: No pre-probed mesh initialized", font=ctk.CTkFont(size=12, weight="bold"), text_color="#8b949e")
        self.active_mesh_lbl.pack(pady=(0, 10))

        dropdown_frame = ctk.CTkFrame(self, fg_color="transparent")
        dropdown_frame.pack(pady=5)

        self.mesh_dropdown_var = ctk.StringVar(value="Select Profile...")
        self.mesh_dropdown = ctk.CTkOptionMenu(dropdown_frame, variable=self.mesh_dropdown_var, values=["No profiles found"], width=200)
        self.mesh_dropdown.pack(side="left", padx=10)

        self.btn_delete = ctk.CTkButton(dropdown_frame, text="Delete Profile", fg_color="#da3633", hover_color="#b32d2a", width=100, command=self.delete_profile)
        self.btn_delete.pack(side="left", padx=10)

        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.pack(pady=10)

        self.btn_apply = ctk.CTkButton(action_frame, text="Apply Mesh", fg_color="#8957e5", hover_color="#6e46b8", width=120, command=self.apply_profile)
        self.btn_apply.pack(side="left", padx=5)

        self.btn_compare = ctk.CTkButton(action_frame, text="Compare Meshes", fg_color="#1f6feb", hover_color="#388bfd", width=120, command=self.compare_meshes)
        self.btn_compare.pack(side="left", padx=5)

        self.btn_restore = ctk.CTkButton(action_frame, text="Restore Default", fg_color="#d29922", hover_color="#b07d1b", width=120, command=self.restore_default)
        self.btn_restore.pack(side="left", padx=5)

        self.refresh_mesh_list()
        
        if hasattr(self, 'btn_generate_gcode'): self.btn_generate_gcode.configure(state="disabled")
        if hasattr(self, 'btn_level_bed'): self.btn_level_bed.configure(state="disabled")

        if getattr(self.master, 'is_custom_leveling', 0) > 0:
            self.lock_ui_for_leveling()
            self.display_text("🚧 LEVELING ALREADY IN PROGRESS...\n\nPrinter is currently executing a bed leveling sequence.\n\nThis window is locked until the process completes to prevent errors.")
        else:
            initial_text = (
                "Awaiting generation...\n\n"
                "IMPORTANT NOTE: Minimum possible input temperatures are related to your chamber temperature. "
                "Please be noted that lower temperatures may need opening the printer top and doors for faster cooldown. "
                "Not recommended to use immediately after a print, best used as the first thing after startup."
            )
            self.display_text(initial_text)

    def enable_level_btn():
        self.btn_level.configure(state="normal")
        self.display_text("✅ G-code ready. You may now Start Bed Leveling.")
        
        self.btn_level.configure(state="disabled")
        self.display_text("Cooling down (5s)...")
        self.master.after(5000, enable_level_btn)

    def _get_ssh_client(self):
        """Creates a temporary SSH connection using the active credentials from HardwareController."""
        hw = self.master.hw
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=hw.ip, username=hw.user, password=hw.pwd, timeout=10)
        return ssh
             
    def on_close_attempt(self):
        # Check if ANY critical sequence is running (Leveling, Applying Mesh, or Defaults)
        if getattr(self, 'is_uploading', False) or getattr(self, 'is_busy', False) or getattr(self.master, 'is_custom_leveling', 0) > 0:
            self.iconify()  # Minimize the window instead of closing
            self.master.log("Mesh manager minimized to protect active sequence.", "#d29922")
        else:
            self.destroy()  # Safe to close  

    def on_closing(self):
        if getattr(self.master, 'is_custom_leveling', 0) > 0 or getattr(self, 'is_uploading', False):
            self.iconify() 
            self.display_text("⚠️ ACTION BLOCKED ⚠️\n\nYou cannot close this window while leveling or uploading is active. The window has been minimized to protect the process.")
        else:
            self.destroy()

    def refresh_mesh_list(self):
        base_dir = self.get_user_data_dir()
        configs_dir = os.path.join(base_dir, "mesh_configs")
        os.makedirs(configs_dir, exist_ok=True)
        
        profiles = [d for d in os.listdir(configs_dir) if os.path.isdir(os.path.join(configs_dir, d))]
        
        if profiles:
            self.mesh_dropdown.configure(values=profiles)
            self.mesh_dropdown_var.set("Select Profile...")
        else:
            self.mesh_dropdown.configure(values=["No profiles found"])
            self.mesh_dropdown_var.set("No profiles found")

    def delete_profile(self):
        prof_to_delete = self.mesh_dropdown_var.get()
        if prof_to_delete in ["Select Profile...", "No profiles found"]:
            return
            
        target_dir = os.path.join(self.get_user_data_dir(), "mesh_configs", prof_to_delete)
        if os.path.exists(target_dir):
            try:
                shutil.rmtree(target_dir)
                self.display_text(f"🗑️ SUCCESS: Deleted profile '{prof_to_delete}'.")
                self.refresh_mesh_list()
            except Exception as e:
                self.display_text(f"❌ Error deleting profile: {e}")

    # --- SAFETY FUNCTION: CREATES BACKUPS IF THEY DON'T EXIST ---
    def _ensure_backups_exist(self, ssh_client):
        """Checks for backup files on the printer, creates them if missing."""
        _, stdout, _ = ssh_client.exec_command("ls /board-resource/*konstance_backup.cfg")
        existing_files = stdout.read().decode('utf-8')
        
        backups_missing = False
        if "printer_konstance_backup.cfg" not in existing_files:
            backups_missing = True
        if "user_printer_konstance_backup.cfg" not in existing_files:
            backups_missing = True
            
        if backups_missing:
            self.master.after(0, lambda: self.display_text("⚠️ First time use detected! Creating permanent backups of original configurations..."))
            ssh_client.exec_command("cp /board-resource/printer.cfg /board-resource/printer_konstance_backup.cfg")
            ssh_client.exec_command("cp /board-resource/user_printer.cfg /board-resource/user_printer_konstance_backup.cfg")
            time.sleep(2) # Give it a moment to write to the flash

    def _update_main_ui_mesh_lbl(self, text):
        if hasattr(self.master, 'active_mesh_status_lbl'):
            if text:
                self.master.active_mesh_status_lbl.configure(text=text, text_color="#8957e5")
            else:
                self.master.active_mesh_status_lbl.configure(text="(No pre-probed mesh initialized)", text_color="#8b949e")
                
    def apply_profile(self):

        prof_to_apply = self.mesh_dropdown_var.get()
        if prof_to_apply in ["Select Profile...", "No profiles found"]:
            return
            
        if not hasattr(self.master, 'hw') or not self.master.hw:
            self.display_text("❌ ERROR: Not connected to printer!\nPlease connect via the main dashboard first.")
            return

        self.btn_apply.configure(state="disabled")
        self.btn_restore.configure(state="disabled")
        self.btn_compare.configure(state="disabled")
        
        def push_thread():
            self.is_busy = True
            ssh = None
            try:
                self.master.after(0, lambda: self.display_text(f"🚀 Pushing '{prof_to_apply}' to printer...\n\n1. Creating safety backups..."))
                
                base_dir = self.get_user_data_dir()
                prof_dir = os.path.join(base_dir, "mesh_configs", prof_to_apply)
                p_cfg = os.path.join(prof_dir, "printer.cfg")
                u_cfg = os.path.join(prof_dir, "user_printer.cfg")
                
                if not os.path.exists(p_cfg) or not os.path.exists(u_cfg):
                    self.master.after(0, lambda: self.display_text("❌ ERROR: Local profile is corrupted. Missing cfg files."))
                    return

                # 1. SSH & Upload
                ssh = self._get_ssh_client()
                self._ensure_backups_exist(ssh)
                
                self.master.after(0, lambda: self.display_text("2. Uploading new mesh configurations..."))
                sftp = ssh.open_sftp()
                sftp.put(p_cfg, "/board-resource/printer.cfg")
                sftp.put(u_cfg, "/board-resource/user_printer.cfg")
                sftp.close()
                
                # 2. Hard Reboot
                self.master.after(0, lambda: self.display_text("3. Configs applied. Executing HARD REBOOT...\n\nPrinter will disconnect. Please wait (~60s)..."))
                ssh.exec_command("reboot")
                ssh.close()
                ssh = None 
                
                # 3. Wait for Printer to come back online
                import socket
                time.sleep(15) # Give it time to power down
                is_up = False
                
                for i in range(45): # 90 seconds max wait
                    self.master.after(0, lambda val=i: self.display_text(f"Waiting for printer to power on and load OS... ({val+1}/45)"))
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        if sock.connect_ex((self.master.hw.ip, 3030)) == 0:
                            is_up = True
                            sock.close()
                            break
                        sock.close()
                    except: pass
                    time.sleep(2)
                    
                if not is_up:
                    self.master.after(0, lambda: self.display_text("❌ TIMEOUT: Printer did not come back online. Please check it manually."))
                    return
                    
                self.master.after(0, lambda: self.display_text("4. Printer online! Reconnecting telemetry & camera..."))
                
                # --- RECONNECT TELEMETRY & CAMERA ---
                try:
                    if hasattr(self.master.protocol, 'ws') and self.master.protocol.ws: 
                        self.master.protocol.ws.close()
                except: pass
                
                try:
                    from protocol import CentauriProtocol
                    self.master.protocol = CentauriProtocol(self.master.hw.ip, self.master.process_status_update)
                    self.master.protocol.connect()
                    self.master.after(2000, self.master.start_camera)
                except Exception as e:
                    print(f"Reconnect error: {e}")

                time.sleep(5) # Let Klipper fully initialize
                
                # --- AUTO-TURN ON LED ---
                try:
                    self.master.led_state = True
                    if hasattr(self.master, 'btn_led'):
                        self.master.btn_led.configure(text="💡 LED: ON", fg_color="#2ea043")
                    self.master.send_cmd(403, {"LightStatus": {"SecondLight": 1}}) 
                except: pass

                # 4. Verify it actually saved
                ssh_verify = self._get_ssh_client()
                _, stdout, _ = ssh_verify.exec_command("grep -A 10 '\[besh_profile_standard_default\]' /board-resource/printer.cfg")
                remote_check = stdout.read().decode('utf-8')
                ssh_verify.close()
                
                with open(p_cfg, 'r', encoding='utf-8') as f:
                    local_check = f.read()
                    
                if "[besh_profile_standard_default]" in remote_check:
                    self.master.after(0, lambda: [
                        self.display_text(f"🎉 VERIFICATION SUCCESS!\n\nThe printer has fully rebooted and is now securely using the '{prof_to_apply}' bed mesh."),
                        self.active_mesh_lbl.configure(text=f"Status: Custom mesh '{prof_to_apply}' initialized", text_color="#3fb950"),
                        self._update_main_ui_mesh_lbl(f"(Mesh active: {prof_to_apply})")
                    ])
                else:
                    self.master.after(0, lambda: self.display_text("⚠️ WARNING: Printer rebooted, but mesh verification failed! The OS may have rejected the file."))
                
            except Exception as e:
                self.master.after(0, lambda err=str(e): self.display_text(f"❌ SSH ERROR:\n{err}"))
            finally:
                if ssh:
                    ssh.close()
                self.master.after(0, lambda: [
                    self.btn_apply.configure(state="normal"),
                    self.btn_restore.configure(state="normal"),
                    self.btn_compare.configure(state="normal")
                ])

        threading.Thread(target=push_thread, daemon=True).start()
        self.is_busy = False

    def restore_default(self):
        self.is_busy = True
        if not hasattr(self.master, 'hw') or not self.master.hw:
            self.display_text("❌ ERROR: Not connected to printer!\nPlease connect via the main dashboard first.")
            return

        self.btn_apply.configure(state="disabled")
        self.btn_restore.configure(state="disabled")
        self.btn_compare.configure(state="disabled")
        
        def restore_thread():
            ssh = None
            try:
                self.master.after(0, lambda: self.display_text("🔄 Restoring original factory configurations..."))
                ssh = self._get_ssh_client()
                
                _, stdout, _ = ssh.exec_command("ls /board-resource/*konstance_backup.cfg")
                existing_files = stdout.read().decode('utf-8')
                
                if "printer_konstance_backup.cfg" not in existing_files:
                    self.master.after(0, lambda: self.display_text("❌ RESTORE FAILED: Backup files not found on printer. Have you applied a custom mesh yet?"))
                    return

                # Perform the copy
                self.master.after(0, lambda: self.display_text("1. Replacing active configs with factory backups..."))
                ssh.exec_command("cp /board-resource/printer_konstance_backup.cfg /board-resource/printer.cfg")
                ssh.exec_command("cp /board-resource/user_printer_konstance_backup.cfg /board-resource/user_printer.cfg")
                
                # Hard Reboot
                self.master.after(0, lambda: self.display_text("2. Defaults applied. Executing HARD REBOOT (~60s)...\n\nPlease wait."))
                ssh.exec_command("reboot")
                ssh.close()
                ssh = None
                
                # Wait for Printer
                import socket
                time.sleep(15) 
                is_up = False
                for i in range(45): 
                    self.master.after(0, lambda val=i: self.display_text(f"Waiting for printer to power on and load OS... ({val+1}/45)"))
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        if sock.connect_ex((self.master.hw.ip, 3030)) == 0:
                            is_up = True
                            sock.close()
                            break
                        sock.close()
                    except: pass
                    time.sleep(2)
                    
                if not is_up:
                    self.master.after(0, lambda: self.display_text("❌ TIMEOUT: Printer did not come back online."))
                    return
                    
                self.master.after(0, lambda: self.display_text("3. Printer online! Reconnecting telemetry & camera..."))
                
                try:
                    if hasattr(self.master.protocol, 'ws') and self.master.protocol.ws: 
                        self.master.protocol.ws.close()
                except: pass
                
                try:
                    from protocol import CentauriProtocol
                    self.master.protocol = CentauriProtocol(self.master.hw.ip, self.master.process_status_update)
                    self.master.protocol.connect()
                    self.master.after(2000, self.master.start_camera)
                except Exception as e:
                    print(f"Reconnect error: {e}")

                time.sleep(5) 
                
                try:
                    self.master.led_state = True
                    if hasattr(self.master, 'btn_led'):
                        self.master.btn_led.configure(text="💡 LED: ON", fg_color="#2ea043")
                    self.master.send_cmd(403, {"LightStatus": {"SecondLight": 1}}) 
                except: pass
                    
                self.master.after(0, lambda: [
                    self.display_text("🎉 SUCCESS! Original default configurations restored. Printer is fully online."),
                    self.active_mesh_lbl.configure(text="Status: No pre-probed mesh initialized", text_color="#8b949e"),
                    self._update_main_ui_mesh_lbl("")
                ])
            except Exception as e:
                self.master.after(0, lambda err=str(e): self.display_text(f"❌ SSH ERROR:\n{err}"))
            finally:
                if ssh:
                    ssh.close()
                self.master.after(0, lambda: [
                    self.btn_apply.configure(state="normal"),
                    self.btn_restore.configure(state="normal"),
                    self.btn_compare.configure(state="normal")
                ])
                
        threading.Thread(target=restore_thread, daemon=True).start()
        self.is_busy = False
    def compare_meshes(self):
        prof_to_compare = self.mesh_dropdown_var.get()
        if prof_to_compare in ["Select Profile...", "No profiles found"]:
            self.display_text("Please select a profile to compare!")
            return

        if not hasattr(self.master, 'hw') or not self.master.hw:
            self.display_text("❌ ERROR: Not connected to printer!\nPlease connect via the main dashboard first.")
            return

        self.display_text("🔍 Comparing meshes...\nDownloading current printer config...")

        def compare_thread():
            ssh = None
            try:
                ssh = self._get_ssh_client()
                
                _, stdout, _ = ssh.exec_command("cat /board-resource/printer.cfg")
                printer_cfg_content = stdout.read().decode('utf-8')

                local_cfg_path = os.path.join(self.get_user_data_dir(), "mesh_configs", prof_to_compare, "printer.cfg")
                if not os.path.exists(local_cfg_path):
                    self.master.after(0, lambda: self.display_text("❌ ERROR: Local profile missing printer.cfg"))
                    return
                with open(local_cfg_path, 'r', encoding='utf-8') as f:
                    local_cfg_content = f.read()

                blocks_to_check = [
                    "[besh_profile_standard_default]", 
                    "[besh_profile_enhancement_default]",
                    "[besh_profile_standard_1]",
                    "[besh_profile_enhancement_1]"
                ]

                def extract_block(text, block_name):
                    lines = text.split('\n')
                    in_block = False
                    block_data = []
                    for line in lines:
                        if line.strip() == block_name:
                            in_block = True
                            block_data.append(line.strip())
                        elif in_block and line.strip().startswith('[') and line.strip() != block_name:
                            break
                        elif in_block:
                            block_data.append(line.strip())
                    return '\n'.join(block_data)

                report = f"📊 MESH COMPARISON REPORT (Profile: {prof_to_compare})\n" + "-"*50 + "\n"
                
                all_match = True
                for block in blocks_to_check:
                    printer_block = extract_block(printer_cfg_content, block)
                    local_block = extract_block(local_cfg_content, block)
                    
                    if not printer_block and not local_block:
                        continue 
                    
                    if printer_block == local_block:
                        report += f"✅ {block}: MATCHES PERFECTLY\n"
                    else:
                        all_match = False
                        report += f"❌ {block}: MISMATCH DETECTED\n"
                
                report += "-"*50 + "\n"
                if all_match:
                    report += "CONCLUSION: The printer is currently running this exact mesh profile."
                else:
                    report += "CONCLUSION: The printer's active mesh differs from this profile."

                self.master.after(0, lambda: self.display_text(report))

            except Exception as e:
                self.master.after(0, lambda err=str(e): self.display_text(f"❌ SSH ERROR:\n{err}"))
            finally:
                if ssh:
                    ssh.close()

        threading.Thread(target=compare_thread, daemon=True).start()

    def lock_ui_for_leveling(self):
        self.btn_save.configure(state="disabled")
        self.btn_gcode.configure(state="disabled")
        self.btn_level.configure(state="disabled", text="Leveling...")
        self.btn_delete.configure(state="disabled")
        self.profile_entry.configure(state="disabled")
        self.nozzle_entry.configure(state="disabled")
        self.bed_entry.configure(state="disabled")

    def unlock_ui_after_leveling(self):
        self.btn_save.configure(state="normal")
        self.btn_gcode.configure(state="disabled")
        self.btn_level.configure(state="disabled", text="Level Bed") 
        self.btn_delete.configure(state="normal")
        self.profile_entry.configure(state="normal")
        self.nozzle_entry.configure(state="normal")
        self.bed_entry.configure(state="normal")
        self.refresh_mesh_list()
        self.display_text("✅ Leveling & Harvest Complete!\n\nYour new mesh profile has been successfully downloaded.\nYou can find it in the mesh_configs folder or delete it below.")

    def unlock_ui_on_error(self):
        self.btn_save.configure(state="normal")
        self.btn_gcode.configure(state="normal")
        self.btn_level.configure(state="normal", text="Level Bed")
        self.btn_delete.configure(state="normal")
        self.profile_entry.configure(state="normal")
        self.nozzle_entry.configure(state="normal")
        self.bed_entry.configure(state="normal")

    def get_user_data_dir(self):
        app_data = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
        base_dir = os.path.join(app_data, "KonstanceWatchdog")
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    def enforce_max_limit(self, var, max_val):
        val = var.get()
        if not val: return
        if not val.isdigit():
            clean_val = ''.join(filter(str.isdigit, val))
            var.set(clean_val)
            val = clean_val
        if val and int(val) > max_val:
            var.set(str(max_val))

    def get_dynamic_minimum(self):
        chamber_temp = getattr(self.master, 'current_chamber_temp', 20.0)
        return math.ceil(chamber_temp) + 5

    def display_text(self, text):
        self.gcode_box.configure(state="normal")
        self.gcode_box.delete("1.0", "end")
        self.gcode_box.insert("1.0", text)
        self.gcode_box.configure(state="disabled")

    def generate_gcode_string(self):
        min_temp = self.get_dynamic_minimum()
        nozzle_val = int(self.nozzle_var.get() or "0")
        bed_val = int(self.bed_var.get() or "0")
        wipes = int(self.wipe_var.get() or "0")
        
        if nozzle_val < min_temp:
            nozzle_val = min_temp
            self.nozzle_var.set(str(nozzle_val)) 
            
        if bed_val < min_temp:
            bed_val = min_temp
            self.bed_var.set(str(bed_val))

        wipe_cmds = "M729\n" * wipes if wipes > 0 else ""
        
        self.current_gcode = (
            f"M190 S{bed_val}\n"
            f"G28\n"
            f"M104 S140\n"
            f"{wipe_cmds}"
            f"M104 S{nozzle_val}\n"
            f"BED_MESH_CALIBRATE\n"
            f"SAVE_CONFIG\n"
            f"G28\n"
            f"M104 S0\n"
            f"M190 S0\n"
        )
        return nozzle_val, bed_val

    def save_values(self):
        nozzle_temp, bed_temp = self.generate_gcode_string()
        base_dir = self.get_user_data_dir()
        utility_dir = os.path.join(base_dir, "utility_gcodes")
        os.makedirs(utility_dir, exist_ok=True)
        file_path = os.path.join(utility_dir, "konstance_bed_mesh_generator.gcode")
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.current_gcode)
            
            success_msg = (
                "Saving successful!\n\n"
                "Ready to level bed with:\n"
                f"Nozzle Temp: {nozzle_temp}°C\n"
                f"Bed Temp: {bed_temp}°C\n\n"
                f"Please confirm from gcode that M104 S{nozzle_temp} and M190 S{bed_temp} are correct."
            )
            self.display_text(success_msg)
            
            self.btn_gcode.configure(state="normal")
            self.countdown_active = False
            self.btn_level.configure(state="disabled", text="Level Bed")
            
        except Exception as e:
            self.display_text(f"ERROR: Failed to save file!\n\n{str(e)}")

    def show_gcode(self):
        self.generate_gcode_string()
        self.display_text(self.current_gcode)
        
        if not self.countdown_active:
            self.countdown_active = True
            self.countdown_val = 5
            self.update_countdown()

    def update_countdown(self):
        if not self.countdown_active:
            return 
            
        if self.countdown_val > 0:
            self.btn_level.configure(state="disabled", text=f"Level Bed ({self.countdown_val})")
            self.countdown_val -= 1
            self.after(1000, self.update_countdown)
        else:
            self.btn_level.configure(state="normal", text="Level Bed")
            self.countdown_active = False

    def start_leveling_process(self):
        if not hasattr(self.master, 'protocol') or not self.master.is_monitoring:
            self.display_text("ERROR: Not connected to printer!\nPlease connect via the main dashboard first.")
            return

        safe_name = self.profile_var.get().strip() or "Default_Mesh"
        safe_name = "".join([c for c in safe_name if c.isalnum() or c in " _-"])
        
        base_dir = self.get_user_data_dir()
        target_dir = os.path.join(base_dir, "mesh_configs", safe_name)
        
        if os.path.exists(target_dir):
            self.display_text(f"❌ ERROR: A profile named '{safe_name}' already exists!\n\nPlease choose a different name or delete the existing one below before continuing.")
            return 

        self.is_uploading = True
        self.master.target_mesh_profile = safe_name
        self.lock_ui_for_leveling()
        self.display_text("Initializing upload sequence...")

        threading.Thread(target=self._upload_and_print_task, daemon=True).start()

    def _upload_and_print_task(self):
        printer_ip = self.master.protocol.ip
        base_dir = self.get_user_data_dir()
        file_path = os.path.join(base_dir, "utility_gcodes", "konstance_bed_mesh_generator.gcode")

        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            m = hashlib.md5()
            m.update(file_bytes)
            file_md5 = m.hexdigest()

            url = f"http://{printer_ip}:3030/uploadFile/upload"
            data = {
                "TotalSize": str(len(file_bytes)),
                "Uuid": str(uuid.uuid4().hex),
                "Offset": "0",
                "Check": "1",
                "S-File-MD5": file_md5,
            }
            files = {
                "File": ("konstance_bed_mesh_generator.gcode", file_bytes, "application/octet-stream")
            }
            
            response = requests.post(url, data=data, files=files, timeout=15)
            resp_json = response.json()

            if response.status_code == 200 and resp_json.get("success") == True:
                time.sleep(1.5)
                self.master.is_custom_leveling = 1 
                self.is_uploading = False 
                
                selected_plate = self.plate_var.get()
                payload = {
                    "Filename": "/local/konstance_bed_mesh_generator.gcode",
                    "StartLayer": 0,
                    "Calibration_switch": 0,
                    "PrintPlatformType": selected_plate,
                    "Tlp_Switch": 0
                }
                self.master.send_cmd(128, payload)
                
                self.master.after(0, lambda: [
                    self.display_text(f"🚧 LEVELING IN PROGRESS...\n\nPrinter is executing the bed leveling sequence.\n\nThis window is locked. You can minimize it and check the main dashboard telemetry.")
                ])
            else:
                self.is_uploading = False
                self.master.after(0, lambda: [
                    self.unlock_ui_on_error(),
                    self.display_text(f"UPLOAD ERROR: Printer rejected the file.\nCode: {resp_json.get('code')}\nMsg: {resp_json.get('messages')}")
                ])

        except Exception as e:
            self.is_uploading = False
            self.master.after(0, lambda err=str(e): [
                self.unlock_ui_on_error(),
                self.display_text(f"UPLOAD FAILED (Network/File error):\n{err}")
            ])