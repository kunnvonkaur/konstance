import websocket, json, uuid, time, threading

class CentauriProtocol:
    def __init__(self, ip, callback):
        self.ip, self.callback, self.ws = ip, callback, None
        self.mainboard_id, self.connected = "", False

    def connect(self):
        self.ws = websocket.WebSocketApp(f"ws://{self.ip}:3030/websocket",
            on_message=self.on_message, on_open=lambda w: setattr(self, 'connected', True),
            on_close=lambda w,s,m: setattr(self, 'connected', False))
        threading.Thread(target=self.ws.run_forever, daemon=True).start()

    def on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            if "MainboardID" in data: self.mainboard_id = data["MainboardID"]
            elif "Data" in data and "MainboardID" in data["Data"]: self.mainboard_id = data["Data"]["MainboardID"]
            self.callback(data)
        except: pass

    def send(self, cmd_id, payload={}):
        if not self.ws or not self.connected: return
        req = {"Id": str(uuid.uuid4()), "Data": {"Cmd": cmd_id, "Data": payload, "RequestID": str(uuid.uuid4()),
               "MainboardID": self.mainboard_id, "TimeStamp": int(time.time() * 1000), "From": 1},
               "Topic": f"sdcp/request/{self.mainboard_id}" if self.mainboard_id else "sdcp/request/identify"}
        try: self.ws.send(json.dumps(req))
        except: pass