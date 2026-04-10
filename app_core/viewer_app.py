import webview
import sys
import urllib.parse
import os
import requests
import hashlib
import uuid
import threading
import time
import paramiko
import datetime
import socket

try:
    from protocol import CentauriProtocol
except ImportError:
    CentauriProtocol = None

def get_app_dir():
    app_data = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
    target_dir = os.path.join(app_data, "KonstanceWatchdog")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.jsdelivr.net/npm/three@0.138.0/build/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.138.0/examples/js/controls/OrbitControls.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/gcode-preview@2.8.2/dist/gcode-preview.min.js"></script>
    <style>
        :root { --bg-color: #0d1117; --panel-bg: rgba(22, 27, 34, 0.85); --text-main: #c9d1d9; --text-dim: #8b949e; --border: #30363d; --accent: #10D0DE; }
        body { background: var(--bg-color); margin: 0; overflow: hidden; font-family: Consolas, monospace; color: var(--text-dim); }
        #render-canvas { width: 100vw; height: 100vh; display: block; background: var(--bg-color); }
        #ui-layer { position: absolute; top: 15px; left: 15px; z-index: 10; background: var(--panel-bg); padding: 15px; border-radius: 8px; border: 1px solid var(--border); pointer-events: none; }
        .title { color: var(--accent); font-weight: bold; font-size: 14px; margin-bottom: 8px; }
        .status { font-size: 12px; margin-bottom: 5px; }
        #print-controls { position: absolute; bottom: 15px; left: 50%; transform: translateX(-50%); z-index: 10; background: var(--panel-bg); padding: 10px 20px; border-radius: 8px; border: 1px solid var(--border); display: flex; align-items: center; gap: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        .stats-col { display: none; flex-direction: column; gap: 4px; border-right: 1px solid var(--border); padding-right: 15px; margin-right: 5px; }
        .info-tag { font-size: 11px; color: var(--text-main); }
        .info-label { color: var(--text-dim); }
        .control-group { display: flex; flex-direction: column; gap: 4px; }
        label { font-size: 10px; font-weight: bold; color: var(--text-main); text-transform: uppercase; }
        select, input[type="number"] { background: var(--bg-color); color: var(--text-main); border: 1px solid var(--border); padding: 5px; border-radius: 4px; font-family: Consolas; font-size: 12px; outline: none;}
        select:focus, input:focus { border-color: var(--accent); }
        select:disabled { opacity: 0.5; cursor: not-allowed; }
        .checkbox-group { display: flex; flex-direction: column; gap: 4px; border-right: 1px solid var(--border); padding-right: 15px; }
        .chk-row { display: flex; align-items: center; gap: 5px; }
        .chk-row label { font-size: 11px; text-transform: none; color: var(--text-main); cursor: pointer;}
        .chk-row input:disabled + label { opacity: 0.5; cursor: not-allowed; }
        #start-btn { background: #238636; color: white; border: none; padding: 10px 20px; border-radius: 5px; font-weight: bold; cursor: pointer; font-family: Consolas; transition: 0.2s; box-shadow: 0 2px 5px rgba(0,0,0,0.2);}
        #start-btn:hover { background: #2ea043; transform: translateY(-2px); }
        #start-btn:active { transform: translateY(0px); }
        .loader { border: 3px solid var(--border); border-top: 3px solid var(--accent); border-radius: 50%; width: 20px; height: 20px; animation: spin 1s linear infinite; display: inline-block; vertical-align: middle; margin-right: 10px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        
        #reboot-overlay { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(13,17,23,0.95); z-index: 9999; flex-direction: column; justify-content: center; align-items: center; text-align: center; }
        .big-spinner { border: 4px solid var(--border); border-top: 4px solid var(--accent); border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite; margin-bottom: 20px; }
        
        /* NEW BEAUTIFUL BANNER UI */
        #mesh-hint { position: absolute; bottom: 120px; left: 50%; transform: translateX(-50%); background: var(--panel-bg); padding: 10px 20px; border-radius: 8px; border: 1px solid var(--border); font-size: 12px; color: var(--text-main); width: max-content; max-width: 80%; text-align: center; display: none; box-shadow: 0 4px 15px rgba(0,0,0,0.5); z-index: 9;}
    </style>
</head>
<body>
    <div id="ui-layer">
        <div class="title" id="file-name">Initializing WebGL...</div>
        <div class="status" id="status-text"><div class="loader" id="spinner"></div><span id="stat-msg">Waiting for Python Bridge...</span></div>
    </div>
    <canvas id="render-canvas"></canvas>
    
    <div id="reboot-overlay">
        <div class="big-spinner"></div>
        <h2 id="reboot-title" style="color: #10D0DE;">Applying Mesh</h2>
        <p id="reboot-text" style="color: #8b949e; max-width: 400px; font-size: 14px;">Please wait while the new configuration is applied...</p>
    </div>

    <div id="mesh-hint">
        <b style="color: #10D0DE;">Tip:</b> If your preferred mesh is already active (check main dashboard), you can skip loading it here. Print normally.<br>
        <span style="color: #da3633; font-weight: bold;">⚠️</span> Normal "Bed Leveling" uses factory defaults and will overwrite your active mesh!
    </div>

    <div id="print-controls">
        <div class="stats-col" id="file-stats">
            <div class="info-tag"><span class="info-label">Time:</span> <span id="val-time">--</span></div>
            <div class="info-tag"><span class="info-label">Weight:</span> <span id="val-weight">--</span></div>
        </div>
        
        <div class="checkbox-group" style="max-width: 220px;">
            <div class="chk-row"><input type="checkbox" id="mesh-switch" onchange="toggleMesh()"><label for="mesh-switch" style="color: #10D0DE; font-weight: bold;">Pre-probed Mesh</label></div>
            <select id="mesh-profile" disabled style="width: 130px;"><option>Loading...</option></select>
        </div>

        <div class="control-group"><label>Bed Type</label><select id="bed-type"><option value="0">Textured PEI</option><option value="1">Smooth PEI</option></select></div>
        <div class="control-group"><label>Start Layer</label><input type="number" id="start-layer" value="0" min="0" style="width: 50px;"></div>
        
        <div class="checkbox-group" style="border: none; padding: 0;">
            <div class="chk-row"><input type="checkbox" id="calib-switch"><label for="calib-switch" id="lbl-calib">Bed Leveling</label></div>
            <div class="chk-row"><input type="checkbox" id="tlp-switch"><label for="tlp-switch">Timelapse</label></div>
        </div>
        <button id="start-btn" disabled style="background-color: #30363d; color: #8b949e; cursor: not-allowed;" onclick="triggerPrint()">FETCHING STATUS</button>
    </div>
    <script>
        let preview = null;
        let pollInterval = null;
        let hasStartedPrint = false; 

        function showReboot(title, text) {
            document.getElementById('reboot-overlay').style.display = 'flex';
            document.getElementById('reboot-title').innerText = title;
            document.getElementById('reboot-text').innerText = text;
        }
        function updateReboot(text) {
            document.getElementById('reboot-text').innerText = text;
        }
        function hideReboot() {
            document.getElementById('reboot-overlay').style.display = 'none';
        }

        function updateStatus(msg, color, hideSpinner) {
            document.getElementById('stat-msg').innerText = msg;
            document.getElementById('stat-msg').style.color = color;
            if(hideSpinner) document.getElementById('spinner').style.display = "none";
        }
        
        function pollPrinterData() {
            window.pywebview.api.poll_file_info().then(info => {
                document.getElementById('file-stats').style.display = "flex";
                document.getElementById('val-time').innerText = info.time;
                document.getElementById('val-weight').innerText = info.weight;
                
                const btn = document.getElementById('start-btn');
                
                if (hasStartedPrint) {
                    btn.disabled = true;
                    btn.innerText = "PRINT IN PROGRESS";
                    btn.style.background = "#30363d";
                    btn.style.color = "#8b949e";
                    btn.style.cursor = "not-allowed";
                    return; 
                }

                if (info.can_print === false) {
                    btn.disabled = true;
                    btn.innerText = "PRINTER BUSY";
                    btn.style.background = "#30363d";
                    btn.style.color = "#8b949e";
                    btn.style.cursor = "not-allowed";
                } else {
                    btn.disabled = false;
                    btn.innerText = "START PRINT";
                    btn.style.background = "#238636";
                    btn.style.color = "white";
                    btn.style.cursor = "pointer";
                }
            });
        }

        function toggleMesh() {
            const useMesh = document.getElementById('mesh-switch').checked;
            document.getElementById('mesh-profile').disabled = !useMesh;
            
            const calib = document.getElementById('calib-switch');
            calib.disabled = useMesh;
            if (useMesh) { calib.checked = false; }
        }

        function triggerPrint() {
            const btn = document.getElementById('start-btn');
            
            const config = { 
                bed: document.getElementById('bed-type').value, 
                layer: document.getElementById('start-layer').value, 
                calibration: document.getElementById('calib-switch').checked, 
                timelapse: document.getElementById('tlp-switch').checked,
                use_mesh: document.getElementById('mesh-switch').checked,
                mesh_name: document.getElementById('mesh-profile').value
            };
            
            btn.innerText = "Processing...";
            btn.style.background = "#d29922";
            btn.disabled = true;
            
            window.pywebview.api.start_print(config).then(res => {
                if(res.status === "success") { 
                    hasStartedPrint = true; 
                    btn.innerText = "Started!"; 
                    btn.style.background = "#3fb950"; 
                    setTimeout(() => {
                        btn.innerText = "PRINT IN PROGRESS";
                        btn.style.background = "#30363d";
                        btn.style.color = "#8b949e";
                    }, 2000);
                } else { 
                    btn.disabled = false;
                    btn.innerText = "Failed"; 
                    btn.style.background = "#da3633"; 
                    alert("Error: " + res.msg); 
                }
            });
        }

        function ensureLibraryReady(callback) {
            if (typeof THREE === 'undefined' || typeof GCodePreview === 'undefined') setTimeout(() => ensureLibraryReady(callback), 200);
            else callback();
        }

        window.addEventListener('pywebviewready', function() {
            window.pywebview.api.get_details().then(function(details) {
                document.getElementById('file-name').innerText = details.name;
                
                // SHOW BANNER ONLY IN OC MODE
                if (details.oc_mode === "1" || details.oc_mode === "True") {
                    document.getElementById('mesh-hint').style.display = 'block';
                } else {
                    const meshSwitch = document.getElementById('mesh-switch');
                    meshSwitch.disabled = true;
                    meshSwitch.parentElement.title = "🔒 OC Mode Only";
                    meshSwitch.nextElementSibling.style.color = "#8b949e";
                }

                pollInterval = setInterval(pollPrinterData, 2000);
                
                window.pywebview.api.get_mesh_profiles().then(profiles => {
                    const sel = document.getElementById('mesh-profile');
                    sel.innerHTML = '';
                    if (profiles.length === 0) {
                        sel.innerHTML = '<option value="">No profiles found</option>';
                        document.getElementById('mesh-switch').disabled = true; 
                    } else {
                        profiles.forEach(p => sel.innerHTML += `<option value="${p}">${p}</option>`);
                    }
                });

                ensureLibraryReady(function() {
                    preview = GCodePreview.init({ 
                        canvas: document.querySelector('#render-canvas'), 
                        buildVolume: {x: 256, y: 256, z: 250}, 
                        initialCameraPosition: [0, 400, 450], 
                        backgroundColor: '#0d1117', 
                        extrusionColor: '#388bfd', 
                        topLayerColor: '#10D0DE', 
                        renderTubes: true, 
                        extrusionWidth: 0.82, 
                        layerHeight: 0.4,
                        renderTravel: false 
                    });
                    
                    window.pywebview.api.prepare_file().then(function(res) {
                        if (res.status === "error") {
                            updateStatus("Download Failed: " + res.msg, "#da3633", true);
                            return;
                        }
                        updateStatus("Loading geometry...", "#10D0DE");
                        setTimeout(() => {
                            fetch(details.fetch_path)
                                .then(r => {
                                    if (!r.ok) throw new Error("HTTP " + r.status + " - File missing in local folder");
                                    return r.text();
                                })
                                .then(gcode => {
                                    preview.processGCode(gcode);
                                    updateStatus("Ready. (Left Click: Rotate | Right Click: Pan)", "#3fb950", true);
                                })
                                .catch(err => {
                                    updateStatus("Render Error: " + err.message, "#da3633", true);
                                });
                        }, 100);
                    }).catch(err => {
                        updateStatus("Python Bridge crashed: " + err.message, "#da3633", true);
                    });
                });
            });
        });
    </script>
</body>
</html>"""

class ViewerApi:
    def __init__(self, ip, file_path, display_name, local_save_path, oc_mode):
        self.ip = ip
        self.file_path = file_path
        self.display_name = display_name
        self.local_save_path = local_save_path
        self.oc_mode = str(oc_mode)
        self.fetch_path = f"gcodes/{urllib.parse.quote(self.display_name)}"
        
        self.file_info = {"time": "--", "weight": "--", "can_print": False}
        self.current_printer_status = 0
        
        self.protocol = None
        if CentauriProtocol and self.ip != "0.0.0.0":
            try:
                self.protocol = CentauriProtocol(self.ip, self.protocol_callback)
                self.protocol.connect()
                
                def fetch_info():
                    time.sleep(2) 
                    remote_url = f"/local/{self.display_name}"
                    self.protocol.send(260, {"Url": remote_url})
                    
                    while True:
                        try:
                            self.protocol.send(0, {})
                            time.sleep(3)
                        except:
                            break

                threading.Thread(target=fetch_info, daemon=True).start()
            except Exception as e:
                print(f"Viewer protocol connection failed: {e}")

    def find_in_dict(self, data, key):
        if key in data: return data[key]
        for k, v in data.items():
            if isinstance(v, dict):
                res = self.find_in_dict(v, key)
                if res is not None: return res
        return None

    def _run_js(self, script):
        if webview.windows:
            try:
                webview.windows[0].evaluate_js(script)
            except Exception:
                pass

    def protocol_callback(self, data):
        raw_cmd = data.get("Data", {}).get("Cmd", "Unknown")
        payload_data = data.get("Data", {}).get("Data", {})

        if raw_cmd == 260:
            file_info_block = payload_data.get("FileInfo", {})
            time_s = file_info_block.get("EstTime", 0)
            weight = file_info_block.get("EstWeight", 0.0)

            hours = time_s // 3600
            mins = (time_s % 3600) // 60
            secs = time_s % 60
            
            if hours > 0:
                time_str = f"{int(hours)}h {int(mins)}m {int(secs)}s"
            else:
                time_str = f"{int(mins)}m {int(secs)}s"

            self.file_info["time"] = time_str
            self.file_info["weight"] = f"{weight:.2f}g"

        st = self.find_in_dict(data, "Status")
        if isinstance(st, dict):
            print_info = st.get("PrintInfo", {})
            s_val = print_info.get("Status")
            if s_val is not None:
                self.current_printer_status = s_val

    def get_details(self):
        return {
            "name": self.display_name,
            "fetch_path": self.fetch_path,
            "oc_mode": self.oc_mode
        }
        
    def poll_file_info(self):
        can_print = False
        if self.current_printer_status in [0, 8, 9]:
            can_print = True
                
        self.file_info["can_print"] = can_print
        return self.file_info

    def get_mesh_profiles(self):
        app_dir = get_app_dir()
        configs_dir = os.path.join(app_dir, "mesh_configs")
        if not os.path.exists(configs_dir):
            return []
        
        profiles = [d for d in os.listdir(configs_dir) if os.path.isdir(os.path.join(configs_dir, d))]
        return profiles

    def check_app_port(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((self.ip, 3030))
            sock.close()
            return result == 0
        except:
            return False

    def prepare_file(self):
        try:
            if os.path.exists(self.local_save_path):
                return {"status": "success"}

            safe_path = urllib.parse.quote(self.file_path, safe='/')
            urls_to_try = [
                f"http://{self.ip}{safe_path}",                                     
                f"http://{self.ip}:3030/uploadFile/download?Url={safe_path}",       
                f"http://{self.ip}:8080/server/files/gcodes/{self.display_name}",   
                f"http://{self.ip}/local/{self.display_name}"                       
            ]
            
            success = False
            for url in urls_to_try:
                try:
                    r = requests.get(url, stream=True, timeout=5)
                    if r.status_code == 200:
                        with open(self.local_save_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        success = True
                        break
                except Exception:
                    continue
                    
            if not success:
                return {"status": "error", "msg": "Printer refused all HTTP download requests. Is the file still there?"}
                
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def start_print(self, config):
        if not self.protocol:
            return {"status": "error", "msg": "Cannot reach printer socket!"}

        app_dir = get_app_dir()
        remote_url = f"/local/{self.display_name}"
        calibration_flag = 1 if config["calibration"] else 0
        
        using_mesh = config.get("use_mesh") and config.get("mesh_name")
        
        try:
            if using_mesh:
                profile_name = config["mesh_name"]
                profile_dir = os.path.join(app_dir, "mesh_configs", profile_name)
                
                self._run_js("showReboot('Checking Mesh', 'Verifying printer configuration...')")
                
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname=self.ip, username="root", password="OpenCentauri", timeout=10)
                
                _, stdout, _ = ssh.exec_command("cat /board-resource/printer.cfg")
                printer_cfg = stdout.read().decode('utf-8')
                with open(os.path.join(profile_dir, "printer.cfg"), "r") as f:
                    local_cfg = f.read()

                def extract_blocks(text):
                    blocks = {}
                    for b in ["[besh_profile_standard_default]", "[besh_profile_enhancement_default]", 
                              "[besh_profile_standard_1]", "[besh_profile_enhancement_1]"]:
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
                l_blocks = extract_blocks(local_cfg)
                
                all_match = True
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                report = f"📊 PRE-PRINT MESH VERIFICATION [{timestamp}]\nProfile Target: {profile_name}\n" + "-"*50 + "\n"
                
                for k in p_blocks:
                    if p_blocks[k] == l_blocks[k]:
                        report += f"✅ {k}: MATCH\n"
                    else:
                        report += f"❌ {k}: MISMATCH\n"
                        all_match = False
                
                report += "-"*50 + "\n"
                report += "CONCLUSION: " + ("PERFECT MATCH (Skipping Reboot)" if all_match else "MISMATCH (Rebooting Printer...)") + "\n\n"
                
                log_dir = os.path.join(app_dir, "temp_logs")
                os.makedirs(log_dir, exist_ok=True)
                with open(os.path.join(log_dir, "mesh_comparison_log.txt"), "a", encoding="utf-8") as lf:
                    lf.write(report)

                if not all_match:
                    self._run_js("updateReboot('Pushing configs via SSH...')")
                    sftp = ssh.open_sftp()
                    
                    remote_cfg = "/board-resource/printer.cfg"
                    remote_user_cfg = "/board-resource/user_printer.cfg"
                    backup_cfg = "/board-resource/printer_konstance_backup.cfg"
                    backup_user_cfg = "/board-resource/user_printer_konstance_backup.cfg"
                    
                    try: sftp.stat(backup_cfg)
                    except IOError: ssh.exec_command(f"cp {remote_cfg} {backup_cfg}")
                        
                    try: sftp.stat(backup_user_cfg)
                    except IOError: ssh.exec_command(f"cp {remote_user_cfg} {backup_user_cfg}")
                    
                    local_cfg_path = os.path.join(profile_dir, "printer.cfg")
                    local_user_cfg_path = os.path.join(profile_dir, "user_printer.cfg")
                    
                    if os.path.exists(local_cfg_path): sftp.put(local_cfg_path, remote_cfg)
                    if os.path.exists(local_user_cfg_path): sftp.put(local_user_cfg_path, remote_user_cfg)
                    
                    sftp.close()
                    
                    self._run_js("updateReboot('Configs applied. Hard rebooting printer (~60s)...')")
                    ssh.exec_command("reboot")
                    ssh.close()
                    
                    time.sleep(15)
                    is_up = False
                    for _ in range(45): 
                        self._run_js("updateReboot('Waiting for printer /app/app to restart...')")
                        if self.check_app_port():
                            is_up = True
                            break
                        time.sleep(2)

                    if not is_up:
                        return {"status": "error", "msg": "Printer did not come back online after reboot."}

                    self._run_js("updateReboot('Printer online! Waking up Camera & LED...')")
                    
                    # 1. Give the printer and the main app's watchdog time to fully boot Klipper
                    time.sleep(10)
                    
                    try:
                        if hasattr(self.protocol, 'ws') and self.protocol.ws: self.protocol.ws.close()
                        elif hasattr(self.protocol, 'close'): self.protocol.close()
                    except: pass
                    
                    try:
                        self.protocol = CentauriProtocol(self.ip, self.protocol_callback)
                        self.protocol.connect()
                        time.sleep(2) 
                        
                        # 2. Explicitly turn on the Camera (386) AND LED (403) before starting the print
                        self.protocol.send(386, {"Enable": 1})
                        self.protocol.send(403, {"LightStatus": {"SecondLight": 1}})
                        
                        self._run_js("updateReboot('Stabilizing video stream...')")
                        
                        # 3. Wait 4 more seconds to ensure the video stream is fully active 
                        # so main.py's background watchdog can successfully grab the picture
                        time.sleep(10) 
                    except Exception as e:
                        print(f"Post-reboot telemetry reconnect failed: {e}")

                self._run_js("updateReboot('Filtering G-Code for Pre-Probed Mesh...')")
                
                base_name = self.display_name.replace('.gcode', '')
                safe_profile_name = profile_name.replace(" ", "_")
                mod_filename = f"{base_name}_KMesh_{safe_profile_name}.gcode"
                mod_filepath = os.path.join(app_dir, "gcodes", mod_filename)
                mod_log = []
                
                try:
                    with open(self.local_save_path, 'r', encoding='utf-8') as fin, open(mod_filepath, 'w', encoding='utf-8') as fout:
                        line_num = 1
                        for line in fin:
                            upper_line = line.upper()
                            if "BED_MESH_CALIBRATE" in upper_line or "G29" in upper_line:
                                fout.write(f"; {line.strip()} ; REMOVED BY KONSTANCE PRE-PROBE\n")
                                mod_log.append(f"Deleted '{line.strip()}' on line {line_num}")
                            else:
                                fout.write(line)
                            line_num += 1
                            
                    if mod_log:
                        gcode_log_dir = os.path.join(app_dir, "temp_logs", "gcode_changes")
                        os.makedirs(gcode_log_dir, exist_ok=True)
                        date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                        log_path = os.path.join(gcode_log_dir, f"{date_str}_{self.display_name}.txt")
                        with open(log_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(mod_log))
                except Exception as e:
                    return {"status": "error", "msg": f"Failed to modify G-code locally:\n{e}"}

                self._run_js("updateReboot('Uploading modified G-Code to printer...')")

                try:
                    with open(mod_filepath, "rb") as f:
                        file_bytes = f.read()
                    
                    file_md5 = hashlib.md5(file_bytes).hexdigest()
                    upload_uuid = str(uuid.uuid4().hex)
                    url = f"http://{self.ip}:3030/uploadFile/upload"
                    
                    data = {
                        "TotalSize": str(len(file_bytes)), 
                        "Uuid": upload_uuid, 
                        "Offset": "0", 
                        "Check": "1", 
                        "S-File-MD5": file_md5
                    }
                    
                    files = {"File": (mod_filename, file_bytes, "application/octet-stream")}
                    
                    res = requests.post(url, data=data, files=files, timeout=60)
                    
                    if res.status_code != 200:
                        return {"status": "error", "msg": f"Upload failed (HTTP {res.status_code}):\n{res.text}"}
                    
                    try:
                        resp_json = res.json()
                        if not resp_json.get("success"):
                            return {"status": "error", "msg": f"Printer rejected upload:\n{res.text}"}
                    except:
                        return {"status": "error", "msg": f"Invalid API response from printer:\n{res.text}"}
                    
                    time.sleep(1.5) 
                    remote_url = f"/local/{mod_filename}"
                    calibration_flag = 0 
                except Exception as e:
                    return {"status": "error", "msg": f"Failed to upload modified G-code:\n{str(e)}"}

                self._run_js("updateReboot('Initializing print sequence...')")

            payload = {
                "Filename": remote_url,
                "StartLayer": int(config["layer"]),
                "Calibration_switch": calibration_flag,
                "PrintPlatformType": int(config["bed"]),
                "Tlp_Switch": 1 if config["timelapse"] else 0
            }

            self.protocol.send(128, payload)
            time.sleep(0.5) 
            
            return {"status": "success", "msg": "Print command successfully sent!"}

        except Exception as e:
            return {"status": "error", "msg": str(e)}
        finally:
            if using_mesh:
                self._run_js("hideReboot()")

def launch_viewer(ip, file_path, display_name, oc_mode, pos_x=None, pos_y=None):
    app_dir = get_app_dir()
    os.makedirs(os.path.join(app_dir, "gcodes"), exist_ok=True)
    local_save_path = os.path.join(app_dir, "gcodes", display_name)
    
    html_path = os.path.join(app_dir, "viewer_engine.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE)

    api = ViewerApi(ip, file_path, display_name, local_save_path, oc_mode)

    import tkinter as tk
    root = tk.Tk()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    root.destroy()
    
    if pos_x is None or pos_y is None:
        window_width = 1000
        window_height = 750
        pos_x = (screen_width // 2) - (window_width // 2)
        pos_y = (screen_height // 2) - (window_height // 2)

    webview.create_window(
        f'Konstance Gcode Engine', 
        url=html_path,
        width=1000, height=750, 
        x=pos_x, y=pos_y, 
        background_color='#0d1117',
        js_api=api 
    )
    
    webview.start(http_server=True, gui='edgechromium')

def open_viewer(ip, file_path, display_name, oc_mode="False"):
    launch_viewer(ip, file_path, display_name, oc_mode)

def main():
    args = sys.argv[1:]
    if args and args[0] == "--viewer":
        args = args[1:] 

    file_path = args[0] if len(args) > 0 else "Unknown File"
    display_name = urllib.parse.unquote(file_path.split('/')[-1])

    try:
        pos_x, pos_y = int(args[1]), int(args[2])
    except (IndexError, ValueError):
        pos_x, pos_y = None, None 

    printer_ip = args[3] if len(args) > 3 else "0.0.0.0"
    oc_mode = args[4] if len(args) > 4 else "0"

    launch_viewer(printer_ip, file_path, display_name, oc_mode, pos_x, pos_y)

if __name__ == '__main__':
    main()