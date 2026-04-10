import torch
import tkinter as tk
from tkinter import ttk
import threading
import time
import os
from PIL import Image, ImageTk

import sys


if "--viewer" in sys.argv:
    import viewer_app
    viewer_app.main()
    sys.exit(0)
    
    

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)



def background_loading_task(progress_var, status_label, root):
    try:
        status_label.config(text="Waking up Konstance The Watchdog...")
        progress_var.set(15)
        

        time.sleep(1.5) 
        
        status_label.config(text="Loading computer vision stuff...")
        progress_var.set(40)
        

        import main 
        
        status_label.config(text="Ui and camera stuff loading...")
        progress_var.set(60)
        time.sleep(1.0) 
        
        status_label.config(text="prepping WS and other connection stuff...")
        progress_var.set(80)
        time.sleep(1.0) 

        status_label.config(text="Konstance the Watchdog Ready.")
        progress_var.set(100)
        time.sleep(0.5) 

    except Exception as e:
        print(f"Error during startup: {e}")
    finally:
        root.after(0, root.destroy)


def launch_splash_screen():
    root = tk.Tk()
    root.overrideredirect(True) 
    

    image_path = resource_path("splash_bg.png")
    try:
        pil_image = Image.open(image_path)
        target_width = 800 
        w_percent = (target_width / float(pil_image.size[0]))
        target_height = int((float(pil_image.size[1]) * float(w_percent)))
        
        pil_image = pil_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        bg_image = ImageTk.PhotoImage(pil_image)
        width, height = target_width, target_height
    except Exception as e:
        print(f"Failed to load splash_bg.png: {e}")
        return 


    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width // 2) - (width // 2)
    y = (screen_height // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")


    canvas = tk.Canvas(root, width=width, height=height, highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=bg_image)
    root.bg_image = bg_image 


    try:
        agpl_path = resource_path("agpl_logo.png")
        agpl_pil = Image.open(agpl_path)
        

        agpl_pil.thumbnail((120, 120), Image.Resampling.LANCZOS)
        agpl_image = ImageTk.PhotoImage(agpl_pil)
        
        canvas.create_image(width - 20, height - 20, anchor="se", image=agpl_image)
    except Exception as e:
        print(f"Notice: agpl_logo.png not found or failed to load. Skipping logo. ({e})")


    tk.Label(root, text="v0.1", font=("Segoe UI", 12, "bold"), fg="#C08A63", bg="#1A1A1A").place(x=475, y=height - 90)
    
    status_label = tk.Label(root, text="Initializing...", font=("Segoe UI", 10), fg="#A9A9A9", bg="#1A1A1A")
    status_label.place(relx=0.5, rely=0.90, anchor="center")

    style = ttk.Style()
    style.theme_use('default')
    style.configure("TProgressbar", thickness=6, background='#C08A63', troughcolor='#333333')
    
    progress_var = tk.DoubleVar()
    ttk.Progressbar(root, variable=progress_var, maximum=100, length=300, style="TProgressbar").place(relx=0.5, rely=0.96, anchor="center")


    threading.Thread(target=background_loading_task, args=(progress_var, status_label, root), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    print("Initiating Konstance Boot Sequence...")
    launch_splash_screen()
    print("Splash complete. Handing over to main.py...")
    
    import main
    app = main.CentauriWatchdog()
    app.mainloop()