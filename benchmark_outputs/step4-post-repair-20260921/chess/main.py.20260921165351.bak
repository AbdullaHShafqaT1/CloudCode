
import argparse
from chess_gui import create_server
from chess_logic import Game

def main():
    parser = argparse.ArgumentParser(description="Start the chess server.")
    parser.add_argument('--host', default='127.0.0.1', help='Host address to bind the server to')
    parser.add_argument('--port', type=int, default=8765, help='Port number to bind the server to')
    args = parser.parse_args()

    game = Game()
    server = create_server(args.host, args.port)
    server.serve_forever()

if __name__ == '__main__':
    main()