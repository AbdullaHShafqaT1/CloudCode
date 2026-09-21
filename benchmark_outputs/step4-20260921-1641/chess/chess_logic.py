
import json
from chess import Board, Move, Piece, Color, CastleRights, Status

class Game:
    def __init__(self, fen=None):
        self.board = Board(fen=fen) if fen else Board()
        self.turn = 'white' if self.board.turn == Color.white else 'black'
        self.pieces = {square: str(self.board.piece_at(square)) for square in self.board.squares()}
        self.legal_moves = list(self.board.legal_moves)
        self.status = self.board.status.name.lower()
        self.winner = None if self.status != 'checkmate' else self.board.result()

    def move(self, uci):
        try:
            move = Move.from_uci(uci)
            if move in self.legal_moves:
                self.board.push(move)
                self.turn = 'white' if self.board.turn == Color.black else 'black'
                self.pieces = {square: str(self.board.piece_at(square)) for square in self.board.squares()}
                self.legal_moves = list(self.board.legal_moves)
                self.status = self.board.status.name.lower()
                self.winner = None if self.status != 'checkmate' else self.board.result()
                return self.state()
            else:
                raise ValueError("Illegal move")
        except ValueError as e:
            raise ValueError(str(e))

    def reset(self):
        self.board.reset()
        self.turn = 'white' if self.board.turn == Color.white else 'black'
        self.pieces = {square: str(self.board.piece_at(square)) for square in self.board.squares()}
        self.legal_moves = list(self.board.legal_moves)
        self.status = self.board.status.name.lower()
        self.winner = None
        return self.state()

    def state(self):
        return {
            'fen': self.board.fen(),
            'turn': self.turn,
            'pieces': self.pieces,
            'legal_moves': [str(move) for move in self.legal_moves],
            'status': self.status,
            'winner': self.winner
        }