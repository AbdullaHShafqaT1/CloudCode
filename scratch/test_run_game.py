import sys
import os
import types
import tkinter as tk

# Load model's sudoku_logic.py
logic_path = r'C:\Users\Acer\Desktop\projects\tests\TESTING-0.1\sudoku_logic.py.bak'
logic_code = open(logic_path, 'r', encoding='utf-8').read()

# Board class compatibility
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

# Separate keypad into its own frame to prevent pack/grid conflict
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

root = tk.Tk()
root.title("Sudoku Game - Built by NodeCore Autonomous Orchestrator")
logic = logic_mod.SudokuLogic()
app = gui_mod.SudokuGUI(root, logic)
root.update()

print("[+] GUI Window initialized successfully!")
print("[*] Generating new game (Easy)...")
app.start_new_game('easy')
root.update()

row0 = [app.entries[0][j].get() for j in range(9)]
print("[+] Initial board Row 0:", row0)

print("[*] Testing hint check on first empty cell...")
app.check_hint()
root.update()
print("[+] Status after hint:", app.status_label.cget('text'))

print("[*] Testing solve_board...")
app.solve_board()
root.update()
solved_row0 = [app.entries[0][j].get() for j in range(9)]
print("[+] Solved board Row 0:", solved_row0)
print("[+] Status after solve:", app.status_label.cget('text'))

root.destroy()
print("[+] Test completed successfully!")
