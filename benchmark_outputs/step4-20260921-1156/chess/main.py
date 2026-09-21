
import argparse
from chess_gui import create_server

def main():
    parser = argparse.ArgumentParser(description="Start the chess GUI server.")
    parser.add_argument('--host', default='127.0.0.1', help='Host address to bind to')
    parser.add_argument('--port', type=int, default=8765, help='Port number to listen on')
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    print(f"Starting server on http://{args.host}:{args.port}")
    server.serve_forever()

if __name__ == '__main__':
    main()