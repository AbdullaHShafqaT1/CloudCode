import os
import sys

DEST_DIR = r"C:\Users\Acer\Desktop\projects\TESTING-0.5"
os.makedirs(DEST_DIR, exist_ok=True)

# 1. chess_logic.py
CHESS_LOGIC_CODE = '''import json

class ChessGame:
    def __init__(self):
        self.reset()

    def reset(self):
        self.board = [
            ['bR', 'bN', 'bB', 'bQ', 'bK', 'bB', 'bN', 'bR'],
            ['bP', 'bP', 'bP', 'bP', 'bP', 'bP', 'bP', 'bP'],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            ['wP', 'wP', 'wP', 'wP', 'wP', 'wP', 'wP', 'wP'],
            ['wR', 'wN', 'wB', 'wQ', 'wK', 'wB', 'wN', 'wR']
        ]
        self.current_turn = 'W'
        self.move_history = []

    def get_piece(self, row, col):
        if 0 <= row < 8 and 0 <= col < 8:
            return self.board[row][col]
        return None

    def find_king(self, color):
        target = f"{color.lower()}K"
        for r in range(8):
            for c in range(8):
                if self.board[r][c] == target:
                    return (r, c)
        return None

    def get_pseudo_legal_moves(self, r, c):
        piece = self.get_piece(r, c)
        if not piece:
            return []

        color = piece[0] # 'w' or 'b'
        ptype = piece[1] # 'P','R','N','B','Q','K'
        opp_color = 'b' if color == 'w' else 'w'
        moves = []

        if ptype == 'P':
            dir_r = -1 if color == 'w' else 1
            start_row = 6 if color == 'w' else 1
            # 1 step forward
            fr = r + dir_r
            if 0 <= fr < 8 and self.board[fr][c] is None:
                moves.append((fr, c))
                # 2 steps forward from home rank
                fr2 = r + 2 * dir_r
                if r == start_row and self.board[fr2][c] is None:
                    moves.append((fr2, c))
            # Diagonal captures
            for dc in [-1, 1]:
                fc = c + dc
                if 0 <= fr < 8 and 0 <= fc < 8:
                    target = self.board[fr][fc]
                    if target and target.startswith(opp_color):
                        moves.append((fr, fc))

        elif ptype == 'N':
            knight_offsets = [(-2,-1), (-2,1), (-1,-2), (-1,2), (1,-2), (1,2), (2,-1), (2,1)]
            for dr, dc in knight_offsets:
                nr, nc = r + dr, c + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    target = self.board[nr][nc]
                    if target is None or target.startswith(opp_color):
                        moves.append((nr, nc))

        elif ptype in ['B', 'R', 'Q']:
            directions = []
            if ptype in ['R', 'Q']:
                directions.extend([(-1,0), (1,0), (0,-1), (0,1)])
            if ptype in ['B', 'Q']:
                directions.extend([(-1,-1), (-1,1), (1,-1), (1,1)])

            for dr, dc in directions:
                nr, nc = r + dr, c + dc
                while 0 <= nr < 8 and 0 <= nc < 8:
                    target = self.board[nr][nc]
                    if target is None:
                        moves.append((nr, nc))
                    elif target.startswith(opp_color):
                        moves.append((nr, nc))
                        break
                    else:
                        break # Own piece
                    nr += dr
                    nc += dc

        elif ptype == 'K':
            king_offsets = [(-1,-1), (-1,0), (-1,1), (0,-1), (0,1), (1,-1), (1,0), (1,1)]
            for dr, dc in king_offsets:
                nr, nc = r + dr, c + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    target = self.board[nr][nc]
                    if target is None or target.startswith(opp_color):
                        moves.append((nr, nc))

        return moves

    def is_in_check(self, color):
        col_char = color.lower()
        king_pos = self.find_king(col_char)
        if not king_pos:
            return False

        opp_color = 'b' if col_char == 'w' else 'w'
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece and piece.startswith(opp_color):
                    if king_pos in self.get_pseudo_legal_moves(r, c):
                        return True
        return False

    def get_legal_moves(self, r, c):
        piece = self.get_piece(r, c)
        if not piece or piece[0].upper() != self.current_turn:
            return []

        color = piece[0]
        pseudo_moves = self.get_pseudo_legal_moves(r, c)
        legal_moves = []

        for to_r, to_c in pseudo_moves:
            # Simulate move
            orig_dest = self.board[to_r][to_c]
            self.board[to_r][to_c] = piece
            self.board[r][c] = None

            in_check = self.is_in_check(color)

            # Revert move
            self.board[r][c] = piece
            self.board[to_r][to_c] = orig_dest

            if not in_check:
                legal_moves.append((to_r, to_c))

        return legal_moves

    def get_all_legal_moves(self, color):
        col_char = color.lower()
        all_moves = []
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece and piece.startswith(col_char):
                    for to_pos in self.get_legal_moves(r, c):
                        all_moves.append(((r, c), to_pos))
        return all_moves

    def is_checkmate(self):
        return self.is_in_check(self.current_turn) and len(self.get_all_legal_moves(self.current_turn)) == 0

    def is_stalemate(self):
        return not self.is_in_check(self.current_turn) and len(self.get_all_legal_moves(self.current_turn)) == 0

    def make_move(self, from_pos, to_pos):
        from_r, from_c = from_pos
        to_r, to_c = to_pos
        piece = self.get_piece(from_r, from_c)

        if not piece or piece[0].upper() != self.current_turn:
            return False

        if (to_r, to_c) not in self.get_legal_moves(from_r, from_c):
            return False

        # Execute move
        captured = self.board[to_r][to_c]
        self.board[to_r][to_c] = piece
        self.board[from_r][from_c] = None

        # Pawn promotion to Queen
        if piece == 'wP' and to_r == 0:
            self.board[to_r][to_c] = 'wQ'
        elif piece == 'bP' and to_r == 7:
            self.board[to_r][to_c] = 'bQ'

        self.move_history.append((from_pos, to_pos, piece, captured))
        self.current_turn = 'B' if self.current_turn == 'W' else 'W'
        return True

    def save_game(self, filepath):
        data = {
            'board': self.board,
            'current_turn': self.current_turn,
            'move_history': [((fr, fc), (tr, tc), p, c) for (fr, fc), (tr, tc), p, c in self.move_history]
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def load_game(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.board = data['board']
        self.current_turn = data['current_turn']
        self.move_history = data.get('move_history', [])
'''

# 2. chess_gui.py
CHESS_GUI_CODE = '''import os
import tkinter as tk
from tkinter import messagebox
from chess_logic import ChessGame

UNICODE_PIECES = {
    'wK': '♔', 'wQ': '♕', 'wR': '♖', 'wB': '♗', 'wN': '♘', 'wP': '♙',
    'bK': '♚', 'bQ': '♛', 'bR': '♜', 'bB': '♝', 'bN': '♞', 'bP': '♟'
}

LIGHT_SQUARE = '#EEEED2'
DARK_SQUARE = '#769656'
HIGHLIGHT_SELECTED = '#F6F669'
HIGHLIGHT_MOVE = '#BACA44'
HIGHLIGHT_CHECK = '#FF7777'

class ChessGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LegacyNode Chess (2-Player Hotseat)")
        self.geometry("640x740")
        self.resizable(False, False)
        self.configure(bg="#262522")

        self.game = ChessGame()
        self.selected_pos = None
        self.legal_moves = []
        self.flipped = False
        self.square_size = 70

        self.setup_ui()
        self.draw_board()

    def setup_ui(self):
        # 1. Top Status Banner
        self.header_frame = tk.Frame(self, bg="#262522", pady=10)
        self.header_frame.pack(fill=tk.X)

        self.turn_label = tk.Label(
            self.header_frame,
            text="White's Turn",
            font=("Helvetica", 16, "bold"),
            fg="#FFFFFF",
            bg="#262522"
        )
        self.turn_label.pack()

        self.status_sublabel = tk.Label(
            self.header_frame,
            text="Select a piece to move",
            font=("Helvetica", 11),
            fg="#AAAAAA",
            bg="#262522"
        )
        self.status_sublabel.pack()

        # 2. Main 8x8 Canvas
        canvas_dim = self.square_size * 8
        self.canvas = tk.Canvas(
            self,
            width=canvas_dim,
            height=canvas_dim,
            bg="#312E2B",
            highlightthickness=2,
            highlightbackground="#454341"
        )
        self.canvas.pack(pady=5)
        self.canvas.bind("<Button-1>", self.on_square_clicked)

        # 3. Bottom Control Bar
        self.controls_frame = tk.Frame(self, bg="#262522", pady=12)
        self.controls_frame.pack(fill=tk.X)

        btn_style = {
            "font": ("Helvetica", 10, "bold"),
            "bg": "#4A4844",
            "fg": "#FFFFFF",
            "activebackground": "#605E5A",
            "activeforeground": "#FFFFFF",
            "relief": tk.FLAT,
            "padx": 12,
            "pady": 6,
            "cursor": "hand2"
        }

        self.new_btn = tk.Button(self.controls_frame, text="New Game", command=self.new_game, **btn_style)
        self.new_btn.pack(side=tk.LEFT, padx=10)

        self.flip_btn = tk.Button(self.controls_frame, text="Flip Board", command=self.flip_board, **btn_style)
        self.flip_btn.pack(side=tk.LEFT, padx=10)

        self.save_btn = tk.Button(self.controls_frame, text="Save Game", command=self.save_game, **btn_style)
        self.save_btn.pack(side=tk.RIGHT, padx=10)

        self.load_btn = tk.Button(self.controls_frame, text="Load Game", command=self.load_game, **btn_style)
        self.load_btn.pack(side=tk.RIGHT, padx=10)

    def to_display_coords(self, r, c):
        if self.flipped:
            return 7 - r, 7 - c
        return r, c

    def from_display_coords(self, dr, dc):
        if self.flipped:
            return 7 - dr, 7 - dc
        return dr, dc

    def draw_board(self):
        self.canvas.delete("all")
        sz = self.square_size

        in_check_king_pos = None
        if self.game.is_in_check(self.game.current_turn):
            in_check_king_pos = self.game.find_king(self.game.current_turn.lower())

        for r in range(8):
            for c in range(8):
                dr, dc = self.to_display_coords(r, c)
                x1 = dc * sz
                y1 = dr * sz
                x2 = x1 + sz
                y2 = y1 + sz

                # Default tile color
                tile_color = LIGHT_SQUARE if (r + c) % 2 == 0 else DARK_SQUARE

                # Check highlight
                if in_check_king_pos == (r, c):
                    tile_color = HIGHLIGHT_CHECK
                # Selected highlight
                elif self.selected_pos == (r, c):
                    tile_color = HIGHLIGHT_SELECTED
                # Legal destination highlight
                elif (r, c) in self.legal_moves:
                    tile_color = HIGHLIGHT_MOVE

                self.canvas.create_rectangle(x1, y1, x2, y2, fill=tile_color, outline="")

                # Rank and file annotations on borders
                if dc == 0:
                    rank_char = str(r + 1) if self.flipped else str(8 - r)
                    txt_color = DARK_SQUARE if tile_color == LIGHT_SQUARE else LIGHT_SQUARE
                    self.canvas.create_text(x1 + 4, y1 + 10, text=rank_char, fill=txt_color, font=("Helvetica", 8, "bold"), anchor="nw")
                if dr == 7:
                    file_char = chr(ord('h') - c) if self.flipped else chr(ord('a') + c)
                    txt_color = DARK_SQUARE if tile_color == LIGHT_SQUARE else LIGHT_SQUARE
                    self.canvas.create_text(x2 - 4, y2 - 4, text=file_char, fill=txt_color, font=("Helvetica", 8, "bold"), anchor="se")

                # Render piece
                piece = self.game.get_piece(r, c)
                if piece:
                    glyph = UNICODE_PIECES.get(piece, "?")
                    glyph_color = "#FFFFFF" if piece.startswith("w") else "#000000"
                    self.canvas.create_text(
                        x1 + sz // 2,
                        y1 + sz // 2,
                        text=glyph,
                        font=("Segoe UI Symbol", int(sz * 0.58)),
                        fill=glyph_color
                    )

                # Small dot indicator for legal empty moves
                if (r, c) in self.legal_moves and not piece:
                    radius = 8
                    cx, cy = x1 + sz // 2, y1 + sz // 2
                    self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill="#1B6634", outline="")

        self.update_status_banner()

    def update_status_banner(self):
        turn_str = "White" if self.game.current_turn == 'W' else "Black"
        in_check = self.game.is_in_check(self.game.current_turn)
        checkmate = self.game.is_checkmate()
        stalemate = self.game.is_stalemate()

        if checkmate:
            winner = "Black" if self.game.current_turn == 'W' else "White"
            self.turn_label.config(text=f"CHECKMATE! {winner} Wins!", fg="#FF5555")
            self.status_sublabel.config(text="Game Over. Click New Game to play again.")
        elif stalemate:
            self.turn_label.config(text="STALEMATE - Draw", fg="#FFCC00")
            self.status_sublabel.config(text="Game drawn by stalemate.")
        elif in_check:
            self.turn_label.config(text=f"{turn_str}'s Turn - CHECK!", fg="#FF6666")
            self.status_sublabel.config(text="Your King is under attack!")
        else:
            self.turn_label.config(text=f"{turn_str}'s Turn", fg="#FFFFFF")
            if self.selected_pos:
                piece = self.game.get_piece(*self.selected_pos)
                self.status_sublabel.config(text=f"Selected {piece} ({len(self.legal_moves)} legal moves)")
            else:
                self.status_sublabel.config(text="Click a piece to select, then click a target square")

    def on_square_clicked(self, event):
        dc = event.x // self.square_size
        dr = event.y // self.square_size
        if not (0 <= dr < 8 and 0 <= dc < 8):
            return

        r, c = self.from_display_coords(dr, dc)

        # If a piece was already selected and user clicks a legal destination
        if self.selected_pos and (r, c) in self.legal_moves:
            success = self.game.make_move(self.selected_pos, (r, c))
            self.selected_pos = None
            self.legal_moves = []
            self.draw_board()

            if self.game.is_checkmate():
                winner = "Black" if self.game.current_turn == 'W' else "White"
                messagebox.showinfo("Checkmate", f"Checkmate! {winner} wins the game!")
            elif self.game.is_stalemate():
                messagebox.showinfo("Stalemate", "Game drawn by stalemate.")
            return

        # Select a new piece of active turn
        piece = self.game.get_piece(r, c)
        if piece and piece[0].upper() == self.game.current_turn:
            self.selected_pos = (r, c)
            self.legal_moves = self.game.get_legal_moves(r, c)
        else:
            self.selected_pos = None
            self.legal_moves = []

        self.draw_board()

    def new_game(self):
        if messagebox.askyesno("New Game", "Are you sure you want to start a new game?"):
            self.game.reset()
            self.selected_pos = None
            self.legal_moves = []
            self.draw_board()

    def flip_board(self):
        self.flipped = not self.flipped
        self.draw_board()

    def save_game(self):
        filepath = os.path.join(os.path.dirname(__file__), "chess_save.json")
        try:
            self.game.save_game(filepath)
            messagebox.showinfo("Save Game", f"Game saved successfully to:\\n{filepath}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save game:\\n{e}")

    def load_game(self):
        filepath = os.path.join(os.path.dirname(__file__), "chess_save.json")
        if not os.path.exists(filepath):
            messagebox.showwarning("Load Game", "No saved game found (chess_save.json missing).")
            return
        try:
            self.game.load_game(filepath)
            self.selected_pos = None
            self.legal_moves = []
            self.draw_board()
            messagebox.showinfo("Load Game", "Saved game loaded successfully!")
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load game:\\n{e}")


def start_game():
    app = ChessGUI()
    app.mainloop()


if __name__ == "__main__":
    start_game()
'''

# 3. main.py
MAIN_CODE = '''"""
Application Launcher for LegacyNode 2-Player Chess Game
"""
import sys
from chess_gui import start_game

if __name__ == "__main__":
    start_game()
'''

# 4. test_chess.py
TEST_CHESS_CODE = '''"""
Headless Automated Unit Test Suite for ChessGame Logic
"""
import os
import unittest
from chess_logic import ChessGame

class TestChessLogic(unittest.TestCase):
    def setUp(self):
        self.game = ChessGame()

    def test_initial_board_layout(self):
        """Verify 32 pieces correctly positioned on standard ranks."""
        white_count = sum(1 for r in range(8) for c in range(8) if self.game.board[r][c] and self.game.board[r][c].startswith('w'))
        black_count = sum(1 for r in range(8) for c in range(8) if self.game.board[r][c] and self.game.board[r][c].startswith('b'))
        self.assertEqual(white_count, 16)
        self.assertEqual(black_count, 16)
        self.assertEqual(self.game.get_piece(7, 4), 'wK')
        self.assertEqual(self.game.get_piece(0, 4), 'bK')
        self.assertEqual(self.game.current_turn, 'W')

    def test_pawn_opening_moves(self):
        """Verify pawn can advance 1 or 2 squares from home rank."""
        # White e2 pawn is at row 6, col 4
        moves = self.game.get_legal_moves(6, 4)
        self.assertIn((5, 4), moves)  # e3
        self.assertIn((4, 4), moves)  # e4

        # Execute e2 -> e4
        success = self.game.make_move((6, 4), (4, 4))
        self.assertTrue(success)
        self.assertEqual(self.game.get_piece(4, 4), 'wP')
        self.assertIsNone(self.game.get_piece(6, 4))
        self.assertEqual(self.game.current_turn, 'B')

    def test_knight_leap(self):
        """Verify knight can leap over pawns."""
        # White b1 knight is at row 7, col 1
        moves = self.game.get_legal_moves(7, 1)
        self.assertIn((5, 0), moves)  # Na3
        self.assertIn((5, 2), moves)  # Nc3

        success = self.game.make_move((7, 1), (5, 2))
        self.assertTrue(success)
        self.assertEqual(self.game.get_piece(5, 2), 'wN')
        self.assertIsNone(self.game.get_piece(7, 1))

    def test_illegal_move_rejected(self):
        """Verify moving opponent's piece or invalid coordinate returns False."""
        # Black piece on White's turn
        self.assertFalse(self.game.make_move((1, 4), (3, 4)))
        # Moving pawn backwards
        self.assertFalse(self.game.make_move((6, 4), (7, 4)))

    def test_check_detection(self):
        """Verify check is correctly detected when king is under threat."""
        # Quick Scholar's mate attack path to verify check
        self.game.make_move((6, 4), (4, 4)) # 1. e4
        self.game.make_move((1, 4), (3, 4)) # 1... e5
        self.game.make_move((7, 5), (4, 2)) # 2. Bc4
        self.game.make_move((0, 1), (2, 2)) # 2... Nc6
        self.game.make_move((7, 3), (3, 7)) # 3. Qh5
        self.game.make_move((0, 6), (2, 5)) # 3... Nf6
        self.assertFalse(self.game.is_in_check('B'))

        # 4. Qxf7+ (row 3 col 7 to row 1 col 5)
        success = self.game.make_move((3, 7), (1, 5))
        self.assertTrue(success)
        self.assertTrue(self.game.is_in_check('B'))
        self.assertTrue(self.game.is_checkmate())

    def test_save_and_load_round_trip(self):
        """Verify state is preserved after save and load."""
        test_file = os.path.join(os.path.dirname(__file__), "test_save.json")
        self.game.make_move((6, 3), (4, 3)) # 1. d4
        self.game.save_game(test_file)

        new_game = ChessGame()
        new_game.load_game(test_file)

        self.assertEqual(new_game.current_turn, 'B')
        self.assertEqual(new_game.get_piece(4, 3), 'wP')
        self.assertIsNone(new_game.get_piece(6, 3))

        if os.path.exists(test_file):
            os.remove(test_file)

if __name__ == '__main__':
    unittest.main()
'''

# 5. RunningGUIDE.txt
RUNNING_GUIDE_TEXT = '''======================================================================
 LegacyNode 2-Player Desktop Chess Game
======================================================================

1. Overview
-----------
A complete, responsive desktop Chess application built in Python using
standard `tkinter`. Features full legal move calculations for all 6 piece
types, check and checkmate detection, Unicode chess glyph rendering, and
a JSON save/load system.

2. Launching the Application
----------------------------
Run the entrypoint script using Python:
    python main.py

Alternatively, run tests directly headlessly:
    python test_chess.py

3. Controls & Gameplay
----------------------
- Active Turn: The header banner indicates whose turn it is (White or Black).
- Select Piece: Left-click on any piece belonging to the active player.
  The selected tile highlights in yellow (#F6F669), and legal target squares
  are marked with green circular markers (#1B6634).
- Execute Move: Left-click on any highlighted destination square.
- Check Warning: When a King is under attack, the tile turns red (#FF7777)
  and the status header warns "White's Turn - CHECK!".
- Checkmate Announcement: If a player is in check with no legal moves,
  a victory popup appears and the game concludes.
- Flip Board: Click [Flip Board] to rotate the perspective between White and Black.
- New Game: Click [New Game] to reset the board.
- Save Game: Click [Save Game] to export the active board to `chess_save.json`.
- Load Game: Click [Load Game] to resume your saved session.

4. Requirements
---------------
- Standard Python 3.8+
- Tkinter (included with standard Windows Python installations)
'''

files = {
    "chess_logic.py": CHESS_LOGIC_CODE,
    "chess_gui.py": CHESS_GUI_CODE,
    "main.py": MAIN_CODE,
    "test_chess.py": TEST_CHESS_CODE,
    "RunningGUIDE.txt": RUNNING_GUIDE_TEXT
}

for fname, content in files.items():
    p = os.path.join(DEST_DIR, fname)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    print(f"[+] Successfully generated: {p} ({len(content)} chars)")

print("[*] All 5 files written successfully!")
