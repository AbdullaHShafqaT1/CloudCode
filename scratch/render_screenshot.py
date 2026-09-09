import os
import sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, r"C:\Users\Acer\Desktop\projects\TESTING-0.5")
from chess_logic import ChessGame

def render_gui_screenshot():
    game = ChessGame()
    # 1. e4 e5 2. Nf3
    game.make_move((6, 4), (4, 4))
    game.make_move((1, 4), (3, 4))
    game.make_move((7, 6), (5, 5))

    # Black selects Knight at b8
    selected = (0, 1)
    legal_moves = game.get_legal_moves(0, 1)

    width, height = 640, 740
    img = Image.new('RGB', (width, height), color='#262522')
    draw = ImageDraw.Draw(img)

    # Fonts
    font_path = r"C:\Windows\Fonts\seguisym.ttf"
    bold_font_path = r"C:\Windows\Fonts\arialbd.ttf"
    regular_font_path = r"C:\Windows\Fonts\arial.ttf"

    font_title = ImageFont.truetype(bold_font_path, 20) if os.path.exists(bold_font_path) else ImageFont.load_default()
    font_sub = ImageFont.truetype(regular_font_path, 12) if os.path.exists(regular_font_path) else ImageFont.load_default()
    font_btn = ImageFont.truetype(bold_font_path, 12) if os.path.exists(bold_font_path) else ImageFont.load_default()
    font_piece = ImageFont.truetype(font_path, 42) if os.path.exists(font_path) else ImageFont.load_default()
    font_coords = ImageFont.truetype(bold_font_path, 10) if os.path.exists(bold_font_path) else ImageFont.load_default()

    # 1. Header Banner
    draw.text((width // 2, 24), "Black's Turn", fill='#FFFFFF', font=font_title, anchor='mm')
    draw.text((width // 2, 48), "Selected Black Knight b8 (2 legal moves: Nc6, Na6)", fill='#BACA44', font=font_sub, anchor='mm')

    # 2. Chess Board
    board_size = 560
    board_x = (width - board_size) // 2
    board_y = 75
    sz = board_size // 8

    LIGHT = '#EEEED2'
    DARK = '#769656'
    HIGHLIGHT_SEL = '#F6F669'
    HIGHLIGHT_MOVE = '#BACA44'

    UNICODE_PIECES = {
        'wK': '♔', 'wQ': '♕', 'wR': '♖', 'wB': '♗', 'wN': '♘', 'wP': '♙',
        'bK': '♚', 'bQ': '♛', 'bR': '♜', 'bB': '♝', 'bN': '♞', 'bP': '♟'
    }

    # Board border
    draw.rectangle([board_x - 3, board_y - 3, board_x + board_size + 2, board_y + board_size + 2], outline='#454341', width=3)

    for r in range(8):
        for c in range(8):
            x1 = board_x + c * sz
            y1 = board_y + r * sz
            x2 = x1 + sz
            y2 = y1 + sz

            sq_color = LIGHT if (r + c) % 2 == 0 else DARK
            if (r, c) == selected:
                sq_color = HIGHLIGHT_SEL
            elif (r, c) in legal_moves:
                sq_color = HIGHLIGHT_MOVE

            draw.rectangle([x1, y1, x2, y2], fill=sq_color)

            if c == 0:
                rank_txt = str(8 - r)
                txt_color = DARK if sq_color == LIGHT else LIGHT
                draw.text((x1 + 4, y1 + 3), rank_txt, fill=txt_color, font=font_coords)
            if r == 7:
                file_txt = chr(ord('a') + c)
                txt_color = DARK if sq_color == LIGHT else LIGHT
                draw.text((x2 - 10, y2 - 14), file_txt, fill=txt_color, font=font_coords)

            piece = game.get_piece(r, c)
            if piece:
                glyph = UNICODE_PIECES.get(piece, '')
                fill_col = '#FFFFFF' if piece.startswith('w') else '#1A1A1A'
                draw.text((x1 + sz // 2, y1 + sz // 2), glyph, fill=fill_col, font=font_piece, anchor='mm')

            if (r, c) in legal_moves and not piece:
                radius = 10
                cx, cy = x1 + sz // 2, y1 + sz // 2
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill='#1B6634')

    # 3. Control Buttons
    btn_y1 = board_y + board_size + 24
    btn_h = 36
    buttons = [
        ("New Game", 40, 120),
        ("Flip Board", 180, 120),
        ("Save Game", 360, 110),
        ("Load Game", 490, 110),
    ]

    for label, bx, bw in buttons:
        draw.rectangle([bx, btn_y1, bx + bw, btn_y1 + btn_h], fill='#4A4844', outline='#5A5854', width=1)
        draw.text((bx + bw // 2, btn_y1 + btn_h // 2), label, fill='#FFFFFF', font=font_btn, anchor='mm')

    artifact_dir = r"C:\Users\Acer\.gemini\antigravity-ide\brain\655578ac-d19c-4a50-96f0-620ef8c71d56"
    os.makedirs(artifact_dir, exist_ok=True)
    out_path = os.path.join(artifact_dir, "chess_gameplay_screenshot.png")
    img.save(out_path, "PNG")
    print(f"[+] Successfully saved screenshot: {out_path} ({img.size})")

if __name__ == "__main__":
    render_gui_screenshot()
