import paramiko
import threading
import os
import time

class HardwareController:
    def __init__(self, ip, user="root", pwd="OpenCentauri"):
        self.ip = ip
        self.user = user
        self.pwd = pwd

    def update_credentials(self, user, pwd):
        self.user = user
        self.pwd = pwd

    def run_ssh_cmd(self, cmd, log_callback):
        def _task():
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname=self.ip, username=self.user, password=self.pwd, timeout=10)
                
                # Check for v4l2-ctl
                stdin, stdout, stderr = ssh.exec_command("which v4l2-ctl")
                if not stdout.read():
                    log_callback("🔧 v4l2 missing. Installing...", "#d29922")
                    ssh.exec_command("opkg install v4l2-utils")
                    log_callback("✅ v4l2 installed.", "#3fb950")

                ssh.exec_command(cmd)
                ssh.close()
                
                if "reboot" in cmd:
                    log_callback("♻️ Reboot command sent!", "#da3633")
                else:
                    log_callback("✅ Camera hardware updated.", "#3fb950")
            except Exception as e:
                log_callback(f"❌ SSH Error: {str(e)}", "#da3633")

        threading.Thread(target=self._task_wrapper(_task), daemon=True).start()

    def _task_wrapper(self, task):
        return task

    def harvest_configs(self, profile_name, base_dir, log_callback):
        def _task():
            try:
                log_callback(f"📥 Initiating Harvest for '{profile_name}'...", "#1f6feb")
                
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname=self.ip, username=self.user, password=self.pwd, timeout=10)
                
                sftp = ssh.open_sftp()
                
                # Centauri specific paths
                remote_cfg = "/board-resource/printer.cfg"
                remote_user_cfg = "/board-resource/user_printer.cfg"
                
                target_dir = os.path.join(base_dir, "mesh_configs", profile_name)
                os.makedirs(target_dir, exist_ok=True)
                
                cfg_stat = sftp.stat(remote_cfg)
                if abs(time.time() - cfg_stat.st_mtime) > 300: 
                    log_callback("⚠️ Config file is older than 5 mins, pulling anyway...", "#d29922")

                sftp.get(remote_cfg, os.path.join(target_dir, "printer.cfg"))
                
                try:
                    sftp.get(remote_user_cfg, os.path.join(target_dir, "user_printer.cfg"))
                except FileNotFoundError:
                    log_callback("⚠️ user_printer.cfg not found on printer, skipped.", "#d29922")

                sftp.close()
                ssh.close()
                
                log_callback(f"✅ Harvest Complete! Saved to mesh_configs/{profile_name}", "#3fb950")
            except Exception as e:
                log_callback(f"❌ Harvest Error: {str(e)}", "#da3633")

        threading.Thread(target=self._task_wrapper(_task), daemon=True).start()

    def verify_mesh_configs(self, profile_name, base_dir, gcode_name, log_callback, result_callback):
        def _task():
            import datetime
            try:
                log_callback(f"🔍 Validating Mesh '{profile_name}' on Layer 1...", "#1f6feb")
                
                local_prof_dir = os.path.join(base_dir, "mesh_configs", profile_name)
                comp_dir = os.path.join(base_dir, "temp_logs", "mesh_comparison")
                os.makedirs(comp_dir, exist_ok=True)
                
                date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                clean_gcode = gcode_name.replace("/", "").replace(".gcode", "")
                report_path = os.path.join(comp_dir, f"{date_str}_{clean_gcode}.html")

                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname=self.ip, username=self.user, password=self.pwd, timeout=10)
                sftp = ssh.open_sftp()
                
                remote_cfg_path = "/board-resource/printer.cfg"
                remote_user_cfg_path = "/board-resource/user_printer.cfg"
                
                temp_printer_cfg = os.path.join(comp_dir, "temp_printer.cfg")
                temp_user_cfg = os.path.join(comp_dir, "temp_user_printer.cfg")
                
                sftp.get(remote_cfg_path, temp_printer_cfg)
                try:
                    sftp.get(remote_user_cfg_path, temp_user_cfg)
                except:
                    open(temp_user_cfg, 'w').close() 
                    
                sftp.close()
                ssh.close()

                def extract_besh_blocks(filepath):
                    blocks = {}
                    targets = ["[besh_profile_standard_default]", "[besh_profile_enhancement_default]", 
                               "[besh_profile_standard_1]", "[besh_profile_enhancement_1]"]
                    if not os.path.exists(filepath): return blocks
                    
                    with open(filepath, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        
                    current = None
                    for line in lines:
                        stripped = line.strip()
                        if stripped in targets:
                            current = stripped
                            blocks[current] = []
                        elif current:
                            if stripped.startswith("[") or stripped == "":
                                current = None
                            else:
                                blocks[current].append(stripped)
                    return blocks

                local_data = extract_besh_blocks(os.path.join(local_prof_dir, "printer.cfg"))
                local_data.update(extract_besh_blocks(os.path.join(local_prof_dir, "user_printer.cfg")))
                
                remote_data = extract_besh_blocks(temp_printer_cfg)
                remote_data.update(extract_besh_blocks(temp_user_cfg))

                os.remove(temp_printer_cfg)
                os.remove(temp_user_cfg)

                all_match = True
                html = [f"<html><body style='background-color:#0d1117; color:#c9d1d9; font-family:Consolas, monospace; padding: 20px;'>"]
                html.append(f"<h2 style='color:#10D0DE;'>Mesh Verification Report</h2>")
                html.append(f"<p><b>Profile:</b> {profile_name}<br><b>G-Code:</b> {gcode_name}<br><b>Time:</b> {date_str}</p><hr style='border-color:#30363d;'>")

                for target in ["[besh_profile_standard_default]", "[besh_profile_enhancement_default]", "[besh_profile_standard_1]", "[besh_profile_enhancement_1]"]:
                    local_vals = local_data.get(target, [])
                    remote_vals = remote_data.get(target, [])
                    
                    if not local_vals:
                        continue 
                        
                    html.append(f"<h3 style='color:#8b949e;'>{target}</h3>")
                    
                    if local_vals == remote_vals:
                        html.append(f"<p style='color:#3fb950;'>✅ MATCH: All {len(local_vals)} rows identical.</p>")
                    else:
                        all_match = False
                        html.append("<table style='width:100%; border-collapse: collapse;'><tr><th style='text-align:left; color:#1f6feb;'>Pushed Configuration (Local)</th><th style='text-align:left; color:#da3633;'>Live Printer Configuration (Remote)</th></tr>")
                        max_len = max(len(local_vals), len(remote_vals))
                        for i in range(max_len):
                            l_val = local_vals[i] if i < len(local_vals) else "[MISSING]"
                            r_val = remote_vals[i] if i < len(remote_vals) else "[MISSING]"
                            color = "#3fb950" if l_val == r_val else "#da3633"
                            html.append(f"<tr><td style='border:1px solid #30363d; padding:5px;'>{l_val}</td><td style='border:1px solid #30363d; padding:5px; color:{color};'>{r_val}</td></tr>")
                        html.append("</table><br>")

                html.append("</body></html>")
                
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(html))

                result_callback(all_match, report_path)

            except Exception as e:
                log_callback(f"❌ Mesh Verification Crash: {str(e)}", "#da3633")

        threading.Thread(target=self._task_wrapper(_task), daemon=True).start()

    def apply_factory_default(self, log_callback):
        cmd = (
            "v4l2-ctl -d /dev/video0 --set-ctrl=exposure_auto=3; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=contrast=32; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=saturation=64; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=hue=0; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=gain=4; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=sharpness=2"
        )
        self.run_ssh_cmd(cmd, log_callback)

    def apply_preset_1(self, log_callback):
        cmd = (
            "v4l2-ctl -d /dev/video0 --set-ctrl=exposure_auto=1; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=contrast=50; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=saturation=1; "
            "v4l2-ctl -d /dev/video0 --set-ctrl=exposure_absolute=2250"
        )
        self.run_ssh_cmd(cmd, log_callback)

    def apply_batch_config(self, config_dict, log_callback):
        cmd_parts = []
        for ctrl, val in config_dict.items():
            cmd_parts.append(f"v4l2-ctl -d /dev/video0 --set-ctrl={ctrl}={val}")
        
        full_cmd = "; ".join(cmd_parts)
        self.run_ssh_cmd(full_cmd, log_callback)