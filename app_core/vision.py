import cv2
import numpy as np

class CentauriVision:
    @staticmethod
    def apply_filters(frame, filters_config):
        if filters_config.get("Grayscale"):
            frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        
        if filters_config.get("CLAHE"):
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            cl = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8)).apply(l)
            frame = cv2.cvtColor(cv2.merge((cl,a,b)), cv2.COLOR_LAB2BGR)

        if filters_config.get("Edge"):
            frame = cv2.filter2D(frame, -1, np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]]))

        black = filters_config.get("Black", 0)
        if black > 0:
            frame = cv2.subtract(frame, np.full(frame.shape, int(black), dtype=np.uint8))
        
        frame = cv2.convertScaleAbs(frame, alpha=filters_config.get("Contrast", 1.0), beta=filters_config.get("Bright", 0))
        
        gamma = filters_config.get("Gamma", 1.0)
        if gamma != 1.0:
            table = np.array([((i / 255.0) ** (1.0/gamma)) * 255 for i in np.arange(0, 256)]).astype("uint8")
            frame = cv2.LUT(frame, table)
        return frame

    @staticmethod
    def draw_overlays(frame, boxes, zones, drawing_raw, draw_mode):
        # 1. Draw Saved Ignore Zones (Blue)
        for zx1, zy1, zx2, zy2 in zones:
            cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (255, 0, 0), 2)
            cv2.putText(frame, "IGNORED", (zx1, max(20, zy1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

        # 2. Draw Current Active Drag (Yellow)
        if draw_mode and drawing_raw:
            mx1, my1, mx2, my2 = drawing_raw
            cv2.rectangle(frame, (int(mx1), int(my1)), (int(mx2), int(my2)), (0, 255, 255), 2)

        # 3. Draw AI Detections (Red)
        for b in boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            conf = float(b.conf[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(frame, f"{int(conf*100)}%", (x1, max(20, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        return frame