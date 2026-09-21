
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from chess_logic import Game

class ChessHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open("index.html", "r") as f:
                self.wfile.write(f.read().encode())
        elif self.path.startswith("/state"):
            game = Game()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(game.state()).encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == "/move":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())
            try:
                game = Game()
                game.move(data["uci"])
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(game.state()).encode())
            except ValueError as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        elif self.path == "/reset":
            game = Game()
            game.reset()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(game.state()).encode())
        elif self.path.startswith("/position"):
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())
            try:
                game = Game(data["fen"])
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(game.state()).encode())
            except ValueError as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

def create_server(host, port):
    return HTTPServer((host, port), ChessHandler)

if __name__ == "__main__":
    print("Starting server...")
    server = create_server("127.0.0.1", 8765)
    server.serve_forever()