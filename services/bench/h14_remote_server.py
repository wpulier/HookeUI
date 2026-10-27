"""The T13 remote data server (h14-study-design.md §4.1 T13): serves an OME-Zarr folder over HTTP and cuts it once.

After `--cut-after` requests it refuses every request for `--cut-seconds` (the connection is closed with no reply),
then serves normally again. The participant must notice and get the app working again. Both apps get the same cut,
at the same request count, on the site's local wired network (§5). It binds to the given address only.
"""
from __future__ import annotations

import argparse
import functools
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class CutState:
    def __init__(self, after: int, seconds: float):
        self.after, self.seconds = after, seconds
        self.count, self.cut_at = 0, None
        self.lock = threading.Lock()

    def refuse(self) -> bool:
        with self.lock:
            self.count += 1
            if self.cut_at is None and self.count > self.after:
                self.cut_at = time.monotonic()
            return self.cut_at is not None and time.monotonic() - self.cut_at < self.seconds


def handler(root: Path, state: CutState):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if state.refuse():
                self.close_connection = True
                return
            super().do_GET()

        def log_message(self, fmt, *args):
            print(f"{time.strftime('%H:%M:%S')} #{state.count} {fmt % args}", flush=True)
    return functools.partial(Handler, directory=str(root))


def serve(root: Path, host: str = "127.0.0.1", port: int = 8799, after: int = 40, seconds: float = 30.0):
    server = ThreadingHTTPServer((host, port), handler(root, CutState(after, seconds)))
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--cut-after", type=int, default=40)
    ap.add_argument("--cut-seconds", type=float, default=30.0)
    args = ap.parse_args(argv)
    server = serve(args.root, args.host, args.port, args.cut_after, args.cut_seconds)
    print(f"serving {args.root} on http://{args.host}:{args.port}/ (cut after {args.cut_after} requests "
          f"for {args.cut_seconds:.0f} s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
