#!/usr/bin/env python3
"""Tiny local server to receive confirmation results from browser HTML.
Usage: python save_result_server.py <output_json_path> [port]
The server auto-shuts down after receiving the first POST."""
import json, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

OUTPUT_PATH = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 18765

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length))
            with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f'[server] Saved confirmed_rules.json ({len(data.get("rules",{}))} rules)')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'path': OUTPUT_PATH}).encode())
            # Shutdown after saving
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        except Exception as e:
            print(f'[server] Error: {e}')
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())

    def log_message(self, format, *args):
        print(f'[server] {args[0]}')

server = HTTPServer(('127.0.0.1', PORT), Handler)
print(f'[server] Listening on http://127.0.0.1:{PORT}')
print(f'[server] Will save to: {OUTPUT_PATH}')
server.serve_forever()
