import os
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from protocol import (
    TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA,
    FLAG_END, DATA_CHUNK_SIZE,
    ProtocolError, TruncatedFrame,
    read_frame, encode_frame, encode_headers, decode_headers, headers_to_dict,
)


def content_type(path):
    suffix = path.suffix.lower()
    return {
        ".html": "text/html",
        ".txt": "text/plain",
        ".css": "text/css",
        ".js": "application/javascript",
        ".jpg": "image/jpeg",
        ".png": "image/png",
    }.get(suffix, "application/octet-stream")


def http_date():
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def error_payload(status):
    return encode_headers([
        (":status", str(status)),
        ("server", "bserve-python/1.0"),
        ("date", http_date()),
    ])


def send_error(conn, status, frame_id):
    conn.sendall(encode_frame(TYPE_RESPONSE, FLAG_END, frame_id, error_payload(status)))


def drain_after_write_shutdown(conn):
    """Drain peer data after our write side is closed, avoiding a reset."""
    try:
        conn.settimeout(0.25)
        while conn.recv(4096):
            pass
    except (socket.timeout, OSError):
        pass
    finally:
        try:
            conn.settimeout(None)
        except OSError:
            pass


def send_400_and_close(conn):
    try:
        conn.sendall(encode_frame(TYPE_RESPONSE, FLAG_END, 0, error_payload(400)))
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            return
        drain_after_write_shutdown(conn)
    except OSError:
        pass


def safe_resolve(root, request_path):
    if not request_path or not request_path.startswith("/"):
        raise ValueError("invalid path")
    if "\x00" in request_path:
        raise ValueError("NUL in path")
    if "?" in request_path or "%" in request_path or "\\" in request_path:
        raise ValueError("query strings, percent-decoding, and backslashes unsupported")

    relative = request_path.lstrip("/")
    if any(part == ".." for part in Path(relative).parts):
        raise ValueError("path traversal")

    candidate = root / relative
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
    except OSError as e:
        raise FileNotFoundError from e

    root_prefix = str(resolved_root) + os.sep
    if str(resolved) != str(resolved_root) and not str(resolved).startswith(root_prefix):
        raise ValueError("path traversal")

    return resolved


def validate_request_headers(headers):
    """Enforce singleton pseudo headers and no duplicate non-pseudo headers."""
    h = headers_to_dict(headers)

    if len(h.get(":method", [])) != 1 or len(h.get(":path", [])) != 1:
        raise ValueError("missing or duplicate required pseudo header")

    for name, values in h.items():
        if not name.startswith(":") and len(values) > 1:
            raise ValueError(f"duplicate header: {name}")

    return h


def handle_request(conn, root, frame):
    if frame.type != TYPE_REQUEST:
        send_error(conn, 400, frame.id)
        return

    # ID 0 is reserved for connection-level malformed-frame errors.
    if frame.id == 0:
        send_error(conn, 400, 0)
        return

    try:
        headers = decode_headers(frame.payload)
        h = validate_request_headers(headers)
    except (ProtocolError, UnicodeError, ValueError):
        send_error(conn, 400, frame.id)
        return

    method = h[":method"][0]
    path = h[":path"][0]

    if method != "GET":
        send_error(conn, 400, frame.id)
        return

    try:
        file_path = safe_resolve(root, path)
    except ValueError:
        send_error(conn, 400, frame.id)
        return
    except FileNotFoundError:
        send_error(conn, 404, frame.id)
        return

    if not file_path.is_file():
        send_error(conn, 404, frame.id)
        return

    # Open before sending any response bytes. If opening/statting fails,
    # the client can still receive the specified 500 response.
    try:
        f = file_path.open("rb")
        try:
            size = os.fstat(f.fileno()).st_size
        except OSError:
            f.close()
            raise
    except OSError:
        send_error(conn, 500, frame.id)
        return

    try:
        response_headers = encode_headers([
            (":status", "200"),
            ("content-length", str(size)),
            ("content-type", content_type(file_path)),
            ("server", "bserve-python/1.0"),
            ("date", http_date()),
        ])

        if size == 0:
            conn.sendall(encode_frame(TYPE_RESPONSE, FLAG_END, frame.id, response_headers))
            return

        conn.sendall(encode_frame(TYPE_RESPONSE, 0, frame.id, response_headers))

        bytes_sent = 0
        while bytes_sent < size:
            chunk = f.read(min(DATA_CHUNK_SIZE, size - bytes_sent))
            if not chunk:
                # The file changed after stat/open. Never advertise a complete
                # response when fewer bytes than content-length were sent.
                raise OSError("file shrank while being served")
            bytes_sent += len(chunk)
            flags = FLAG_END if bytes_sent == size else 0
            conn.sendall(encode_frame(TYPE_DATA, flags, frame.id, chunk))

        if bytes_sent != size:
            raise OSError("file size changed while being served")

    except OSError:
        # Response bytes were already sent; closing makes the client detect
        # truncation rather than falsely treating the response as complete.
        raise
    finally:
        f.close()


def serve_client(conn, root):
    while True:
        try:
            frame = read_frame(conn)
        except ProtocolError:
            send_400_and_close(conn)
            return
        except TruncatedFrame:
            return

        if frame is None:
            return

        if frame.type not in (TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA):
            continue

        handle_request(conn, root, frame)


def client_thread(conn, addr, root):
    try:
        print(f"connection from {addr}")
        serve_client(conn, root)
    except (ConnectionError, OSError):
        pass
    finally:
        conn.close()


def main():
    if len(sys.argv) != 3:
        print("usage: python bserve.py <www-root> <port>")
        return 2

    root = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])

    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind(("0.0.0.0", port))
        server.listen(16)
        print(f"bserve listening on 0.0.0.0:{port}, root={root}")

        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=client_thread,
                args=(conn, addr, root),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
