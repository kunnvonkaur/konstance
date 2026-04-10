# Make sure to run: pip install Pillow
from PIL import Image

def create_pro_icon():
    # Open your snipped square image
    img = Image.open("icon_raw.png")
    
    # Define the sizes Windows expects in a proper .ico
    icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    
    # Save it directly as an .ico
    img.save("icon.ico", format="ICO", sizes=icon_sizes)
    print("✅ icon.ico generated successfully! Ready for PyInstaller.")

if __name__ == "__main__":
    create_pro_icon()