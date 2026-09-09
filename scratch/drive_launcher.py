import os
import sys
import time
import ctypes
import ctypes.wintypes
from PIL import Image
import pygetwindow as gw
import pyautogui
import pyperclip

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
if hdesk:
    user32.SetThreadDesktop(hdesk)

try:
    user32.SetProcessDPIAware()
except Exception:
    pass

pyautogui.FAILSAFE = False

def capture_screen(region=None):
    """Captures the screen or a specific region using GDI BitBlt."""
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    
    hdesktop = user32.GetDesktopWindow()
    desktop_dc = user32.GetDC(hdesktop)
    img_dc = gdi32.CreateCompatibleDC(desktop_dc)
    
    mem_bitmap = gdi32.CreateCompatibleBitmap(desktop_dc, w, h)
    gdi32.SelectObject(img_dc, mem_bitmap)
    
    SRCCOPY = 0x00CC0020
    gdi32.BitBlt(img_dc, 0, 0, w, h, desktop_dc, 0, 0, SRCCOPY)
    
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', ctypes.wintypes.DWORD),
            ('biWidth', ctypes.wintypes.LONG),
            ('biHeight', ctypes.wintypes.LONG),
            ('biPlanes', ctypes.wintypes.WORD),
            ('biBitCount', ctypes.wintypes.WORD),
            ('biCompression', ctypes.wintypes.DWORD),
            ('biSizeImage', ctypes.wintypes.DWORD),
            ('biXPelsPerMeter', ctypes.wintypes.LONG),
            ('biYPelsPerMeter', ctypes.wintypes.LONG),
            ('biClrUsed', ctypes.wintypes.DWORD),
            ('biClrImportant', ctypes.wintypes.DWORD),
        ]
    
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    
    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(desktop_dc, mem_bitmap, 0, h, buffer, ctypes.byref(bmi), 0)
    
    gdi32.DeleteObject(mem_bitmap)
    gdi32.DeleteDC(img_dc)
    user32.ReleaseDC(hdesktop, desktop_dc)
    
    img = Image.frombuffer('RGBA', (w, h), buffer, 'raw', 'BGRA', 0, 1)
    if region:
        rx, ry, rw, rh = region
        img = img.crop((rx, ry, rx + rw, ry + rh))
    return img

def wait_for_launcher_window(timeout=15):
    """Waits for the NodeCore launcher window to appear."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        wins = [w for w in gw.getAllWindows() if "NodeCore Autonomous Orchestrator" in w.title]
        if wins:
            return wins[0]
        time.sleep(0.5)
    return None

def smooth_click(x, y, duration=0.4):
    """Moves the cursor smoothly to (x, y) and clicks."""
    pyautogui.moveTo(x, y, duration=duration)
    time.sleep(0.1)
    pyautogui.click()
    time.sleep(0.2)

def set_field_value(x, y, text):
    """Clicks on a field, clears its content, and types/pastes new text."""
    smooth_click(x, y, duration=0.5)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.15)
    pyautogui.press('backspace')
    time.sleep(0.15)
    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.2)

def run_automation(artifact_dir):
    print("[*] Waiting for NodeCore launcher window...")
    win = wait_for_launcher_window(timeout=20)
    if not win:
        print("[!] NodeCore launcher window not found!")
        return False
    
    print(f"[+] Found window: '{win.title}' at ({win.left}, {win.top}, {win.width}, {win.height})")
    try:
        win.restore()
        win.activate()
    except Exception as e:
        print(f"[*] Note during activate: {e}")
    time.sleep(1.5)

    client_x0 = win.left + 11
    client_y0 = win.top + 45

    url_x = client_x0 + 272
    url_y = client_y0 + 278

    probe_x = client_x0 + 545
    probe_y = client_y0 + 278

    ws_x = client_x0 + 329
    ws_y = client_y0 + 389

    model_x = client_x0 + 269
    model_y = client_y0 + 454

    start_x = client_x0 + 199
    start_y = client_y0 + 1181

    # 1. Enter URL
    target_url = "https://jane-placement-flooring-undefined.trycloudflare.com"
    print(f"[*] Typing Target URL: {target_url} at ({url_x}, {url_y})...")
    set_field_value(url_x, url_y, target_url)
    time.sleep(0.5)

    # 2. Enter Workspace Directory
    target_ws = r"C:\Users\Acer\Desktop\projects\tests\TESTING-0.1"
    print(f"[*] Typing Target Workspace: {target_ws} at ({ws_x}, {ws_y})...")
    set_field_value(ws_x, ws_y, target_ws)
    time.sleep(0.5)

    # 3. Enter Model
    target_model = "gemma4:e4b"
    print(f"[*] Typing Model: {target_model} at ({model_x}, {model_y})...")
    set_field_value(model_x, model_y, target_model)
    time.sleep(0.5)

    # 4. Click Probe
    print(f"[*] Clicking Probe button at ({probe_x}, {probe_y})...")
    smooth_click(probe_x, probe_y, duration=0.4)
    print("[*] Waiting 3 seconds for endpoint health check...")
    time.sleep(3.0)

    # 5. Capture configured state screenshot
    print("[*] Capturing configured launcher screenshot...")
    shot1 = capture_screen((win.left, win.top, win.width, win.height))
    shot1_path = os.path.join(artifact_dir, "01_launcher_configured.png")
    shot1.save(shot1_path)
    print(f"[+] Saved screenshot: {shot1_path}")

    # 6. Click Start Autonomous Task
    print(f"[*] Clicking Start Autonomous Task button at ({start_x}, {start_y})...")
    smooth_click(start_x, start_y, duration=0.5)
    print("[*] Task triggered! Waiting 4 seconds for runner thread to initialize...")
    time.sleep(4.0)

    # 7. Capture running state screenshot
    print("[*] Capturing running launcher screenshot...")
    shot2 = capture_screen((win.left, win.top, win.width, win.height))
    shot2_path = os.path.join(artifact_dir, "02_launcher_running.png")
    shot2.save(shot2_path)
    print(f"[+] Saved screenshot: {shot2_path}")

    return True

if __name__ == "__main__":
    artifact_dir = r"C:\Users\Acer\.gemini\antigravity-ide\brain\934d62a5-c675-4e90-9fae-8aca5ff122b9"
    os.makedirs(artifact_dir, exist_ok=True)
    success = run_automation(artifact_dir)
    sys.exit(0 if success else 1)

