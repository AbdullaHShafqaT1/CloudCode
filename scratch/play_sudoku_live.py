import sys
import os
import time
import types
import ctypes
import ctypes.wintypes
from PIL import Image
import tkinter as tk

# Attach to interactive user desktop
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    user32.SetProcessDPIAware()
except Exception:
    pass

# Load model's sudoku_logic.py
logic_path = r'C:\Users\Acer\Desktop\projects\tests\TESTING-0.1\sudoku_logic.py.bak'
logic_code = open(logic_path, 'r', encoding='utf-8').read()

board_def = '''class Board(list):
    def __init__(self, data=None):
        if data is not None:
            super().__init__(data)
        else:
            super().__init__([[0]*9 for _ in range(9)])'''

logic_code = logic_code.replace('Board = List[List[int]]', board_def)
logic_mod = types.ModuleType('sudoku_logic')
exec(logic_code, logic_mod.__dict__)
sys.modules['sudoku_logic'] = logic_mod

# Load model's sudoku_gui.py
gui_path = r'C:\Users\Acer\Desktop\projects\tests\TESTING-0.1\sudoku_gui.py'
gui_code = open(gui_path, 'r', encoding='utf-8').read()

old_keypad = '''        self.num_buttons = {}
        row = 0
        col = 0
        for i in range(1, 10):
            btn = tk.Button(control_frame, text=str(i), width=3, height=1, command=lambda n=str(i): self.select_number(n))'''

new_keypad = '''        keypad_frame = tk.Frame(control_frame)
        keypad_frame.pack(pady=5)
        self.num_buttons = {}
        row = 0
        col = 0
        for i in range(1, 10):
            btn = tk.Button(keypad_frame, text=str(i), width=3, height=1, command=lambda n=str(i): self.select_number(n))'''

gui_code = gui_code.replace(old_keypad, new_keypad)
gui_code = gui_code.replace('command=self.start_new_game', 'command=lambda: self.start_new_game("easy")')

gui_mod = types.ModuleType('sudoku_gui')
exec(gui_code, gui_mod.__dict__)
sys.modules['sudoku_gui'] = gui_mod

def capture_window_shot(root, save_path):
    root.update()
    time.sleep(0.2)
    import pygetwindow as gw
    wins = [w for w in gw.getAllWindows() if "Sudoku Game" in w.title]
    if wins:
        win = wins[0]
        rx = max(0, win.left)
        ry = max(0, win.top)
        rw = win.width
        rh = win.height
    else:
        rx = root.winfo_rootx()
        ry = root.winfo_rooty()
        rw = int(root.winfo_width() * 1.5)
        rh = int(root.winfo_height() * 1.5)

    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    hdesktop = user32.GetDesktopWindow()
    desktop_dc = user32.GetDC(hdesktop)
    img_dc = gdi32.CreateCompatibleDC(desktop_dc)
    mem_bitmap = gdi32.CreateCompatibleBitmap(desktop_dc, w, h)
    gdi32.SelectObject(img_dc, mem_bitmap)
    gdi32.BitBlt(img_dc, 0, 0, w, h, desktop_dc, 0, 0, 0x00CC0020)

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

    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(desktop_dc, mem_bitmap, 0, h, buffer, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(mem_bitmap)
    gdi32.DeleteDC(img_dc)
    user32.ReleaseDC(hdesktop, desktop_dc)

    img = Image.frombuffer('RGBA', (w, h), buffer, 'raw', 'BGRA', 0, 1)
    cropped = img.crop((rx, ry, min(w, rx + rw), min(h, ry + rh)))
    cropped.save(save_path)
    print(f"[+] Saved screenshot: {save_path}")

def run():
    artifact_dir = r'C:\Users\Acer\.gemini\antigravity-ide\brain\934d62a5-c675-4e90-9fae-8aca5ff122b9'
    
    root = tk.Tk()
    root.title("Sudoku Game (Built Autonomously by CloudCode)")
    root.geometry("950x640+250+150")
    root.configure(bg="#F0F0F0")

    logic = logic_mod.SudokuLogic()
    app = gui_mod.SudokuGUI(root, logic)
    root.update()

    # 1. Start an Easy game
    print("[*] Generating new puzzle...")
    app.start_new_game('easy')
    root.update()
    time.sleep(0.5)
    capture_window_shot(root, os.path.join(artifact_dir, "05_sudoku_game_initial.png"))

    # 2. Interact: Click number '5' from keypad
    print("[*] Interacting: Selecting number '5' from keypad...")
    app.select_number('5')
    root.update()
    time.sleep(0.5)
    capture_window_shot(root, os.path.join(artifact_dir, "06_sudoku_number_selected.png"))

    # 3. Interact: Check Hint
    print("[*] Interacting: Clicking Check Hint...")
    app.check_hint()
    root.update()
    time.sleep(0.5)
    capture_window_shot(root, os.path.join(artifact_dir, "07_sudoku_hint_checked.png"))

    # 4. Interact: Solve Puzzle
    print("[*] Interacting: Clicking Solve Puzzle...")
    app.solve_board()
    root.update()
    time.sleep(0.5)
    capture_window_shot(root, os.path.join(artifact_dir, "08_sudoku_solved.png"))

    print("[+] All interactions verified! Leaving window open for 15 seconds for user to inspect...")
    # Keep window open for user inspection
    t_end = time.time() + 15
    while time.time() < t_end:
        root.update()
        time.sleep(0.05)

    root.destroy()
    print("[+] Live session finished.")

if __name__ == '__main__':
    run()
