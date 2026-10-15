"""Static file server for a project folder, bound to 127.0.0.1, with HTTP Range support.

Python's built-in http.server ignores Range requests, so the review page's "play this clip" buttons would always start
from 0:00 on a long WAV. This adds 206 Partial Content responses and quiets client disconnects.
"""
import functools, http.server, os, re, socketserver, sys, time

def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [serve] {msg}", file=sys.stderr, flush=True)

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or os.path.isdir(path) or not os.path.isfile(path):
            return super().send_head()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        size = os.path.getsize(path)
        if not m or (not m.group(1) and not m.group(2)):
            return super().send_head()
        if m.group(1):
            start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
        else:                                            # suffix range: the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers()
            return None
        f = open(path, "rb"); f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        n = getattr(self, "_remaining", None)
        try:
            if n is None:
                return super().copyfile(source, outputfile)
            while n > 0:
                buf = source.read(min(65536, n))
                if not buf:
                    break
                outputfile.write(buf); n -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self._remaining = None

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if not self.headers.get("Range"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def serve(root, port=8801):
    handler = functools.partial(RangeHandler, directory=str(root))
    with Server(("127.0.0.1", port), handler) as httpd:
        log(f"serving {root} at http://127.0.0.1:{port}/review.html (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log("stopped")
