import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from protocol import (
    TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA,
    FLAG_END, MAX_FRAME_SIZE, DATA_CHUNK_SIZE,
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


def send_error(conn, status, frame_id):
    payload = encode_headers([(":status", str(status))])
    frame = encode_frame(TYPE_RESPONSE, FLAG_END, frame_id, payload)
    conn.sendall(frame)


def send_400_and_close(conn):
    try:
        conn.sendall(encode_frame(TYPE_RESPONSE, FLAG_END, 0,
                                   encode_headers([(":status", "400")])))
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    except OSError:
        pass


def safe_resolve(root, request_path):
    # V1 does not support query strings or percent-decoding.
    if not request_path or not request_path.startswith("/"):
        raise ValueError("invalid path")
    if "\x00" in request_path:
        raise ValueError("NUL in path")
    if "?" in request_path or "%" in request_path:
        raise ValueError("query strings and percent-decoding unsupported")

    # Convert URL-like path to a filesystem-relative path.
    relative = request_path.lstrip("/")
    candidate = root / relative

    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
    except OSError:
        raise FileNotFoundError

    root_prefix = str(resolved_root) + os.sep
    if str(resolved) != str(resolved_root) and not str(resolved).startswith(root_prefix):
        raise ValueError("path traversal")

    # Explicit .. components are rejected by the protocol.
    if any(part == ".." for part in Path(relative).parts):
        raise ValueError("path traversal")

    return resolved


def handle_request(conn, root, frame):
    if frame.type != TYPE_REQUEST:
        send_error(conn, 400, frame.id)
        return

    if frame.flags & ~FLAG_END:
        # Undefined flags are ignored by the protocol.
        pass

    try:
        headers = decode_headers(frame.payload)
    except (ProtocolError, UnicodeError):
        send_error(conn, 400, frame.id)
        return

    h = headers_to_dict(headers)

    # Required and singleton pseudo headers.
    if len(h.get(":method", [])) != 1 or len(h.get(":path", [])) != 1:
        send_error(conn, 400, frame.id)
        return

    if len(h.get(":method", [])) > 1 or len(h.get(":path", [])) > 1:
        send_error(conn, 400, frame.id)
        return

    if "content-length" in h and len(h["content-length"]) > 1:
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

    try:
        size = file_path.stat().st_size
        response_headers = encode_headers([
            (":status", "200"),
            ("content-length", str(size)),
            ("content-type", content_type(file_path)),
            ("server", "bserve-python/1.0"),
            ("date", http_date()),
        ])

        if size == 0:
            conn.sendall(encode_frame(TYPE_RESPONSE, FLAG_END, frame.id,
                                      response_headers))
            return

        conn.sendall(encode_frame(TYPE_RESPONSE, 0, frame.id,
                                   response_headers))

        with file_path.open("rb") as f:
            while True:
                chunk = f.read(DATA_CHUNK_SIZE)
                if not chunk:
                    break
                # END belongs only to the final DATA frame.
                next_pos = f.tell()
                flags = FLAG_END if next_pos == size else 0
                conn.sendall(encode_frame(TYPE_DATA, flags, frame.id, chunk))

    except OSError:
        # If response bytes were already sent, closing causes the client
        # to detect a truncated response.
        try:
            conn.close()
        except OSError:
            pass
        raise


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

        # A request frame is expected. Unknown frame types are skipped
        # cleanly and do not disturb the persistent connection.
        if frame.type not in (TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA):
            continue

        handle_request(conn, root, frame)


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
            print(f"connection from {addr}")
            try:
                serve_client(conn, root)
            except (ConnectionError, OSError):
                pass
            finally:
                conn.close()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
