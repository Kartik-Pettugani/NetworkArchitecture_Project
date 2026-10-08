"""Verify that bcurl skips an unknown frame before the RESPONSE."""

import socket
import subprocess
import sys
import threading
from pathlib import Path

from protocol import FLAG_END, TYPE_DATA, TYPE_RESPONSE, encode_frame, encode_headers


def fake_server(listener, expected_body):
    conn, _ = listener.accept()
    with conn:
        request = conn.recv(4096)
        if not request:
            raise RuntimeError("client sent no request")

        unknown = encode_frame(0x99, 0, 65535, b"ignored")
        response = encode_frame(
            TYPE_RESPONSE,
            0,
            1,
            encode_headers([
                (":status", "200"),
                ("content-length", str(len(expected_body))),
                ("content-type", "text/plain"),
                ("server", "test-server"),
                ("date", "Thu, 08 Oct 2026 00:00:00 GMT"),
            ]),
        )
        data = encode_frame(TYPE_DATA, FLAG_END, 1, expected_body)
        conn.sendall(unknown + response + data)


def main():
    root = Path(__file__).resolve().parent
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    body = b"unknown-frame-test\n"

    errors = []
    thread = threading.Thread(target=lambda: _run_server(listener, body, errors), daemon=True)
    thread.start()

    cmd = [sys.executable, str(root / "bcurl.py"), f"127.0.0.1:{port}/test.txt"]
    result = subprocess.run(cmd, capture_output=True)
    thread.join(timeout=2)
    listener.close()

    if errors:
        raise errors[0]
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    if result.stdout != body:
        raise RuntimeError(f"unexpected stdout: {result.stdout!r}")

    print("PASS: bcurl skipped unknown frame and received the response body")


def _run_server(listener, body, errors):
    try:
        fake_server(listener, body)
    except Exception as exc:
        errors.append(exc)


if __name__ == "__main__":
    main()
