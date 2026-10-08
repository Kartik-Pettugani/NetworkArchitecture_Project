import socket
import sys

from protocol import (
    TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA,
    FLAG_END, ProtocolError, TruncatedFrame,
    encode_frame, encode_headers, read_frame, hexdump,
)


def usage():
    print("usage: python bcurl.py [-v] host:port/path [host:port/path ...]",
          file=sys.stderr)


def parse_target(target):
    if "/" not in target:
        raise ValueError("target must be host:port/path")
    authority, path = target.split("/", 1)
    if ":" not in authority:
        raise ValueError("target must contain host:port")
    host, port_s = authority.rsplit(":", 1)
    return host, int(port_s), "/" + path


def send_frame(sock, frame, verbose):
    if verbose:
        print(">>", file=sys.stderr)
        print(hexdump(frame), file=sys.stderr)
    sock.sendall(frame)


def recv_frame(sock, verbose):
    frame = read_frame(sock)
    if frame is not None and verbose:
        raw = (
            frame.length.to_bytes(4, "big")
            + bytes([frame.type, frame.flags])
            + frame.id.to_bytes(2, "big")
            + frame.payload
        )
        print("<<", file=sys.stderr)
        print(hexdump(raw), file=sys.stderr)
    return frame


def request_one(sock, host, path, request_id, verbose):
    headers = [
        (":method", "GET"),
        (":path", path),
        ("host", host),
        ("user-agent", "bcurl-python/1.0"),
        ("accept", "*/*"),
        ("x-request-id", "abcd"),  # demonstrates literal encoding
    ]
    payload = encode_headers(headers)
    request = encode_frame(TYPE_REQUEST, FLAG_END, request_id, payload)
    send_frame(sock, request, verbose)

    frame = recv_frame(sock, verbose)
    if frame is None:
        raise RuntimeError("server closed connection before response")

    if frame.type != TYPE_RESPONSE:
        raise RuntimeError(f"expected RESPONSE, got type 0x{frame.type:02x}")

    if frame.id != request_id:
        # ID 0 is reserved for malformed-frame responses.
        raise RuntimeError(f"response ID mismatch: expected {request_id}, got {frame.id}")

    try:
        headers = dict(__import__("protocol").decode_headers(frame.payload))
    except Exception as e:
        raise RuntimeError("invalid response headers") from e

    if ":status" not in headers:
        raise RuntimeError("response missing :status")

    status = int(headers[":status"])

    if status >= 400:
        if not (frame.flags & FLAG_END):
            raise RuntimeError("error response must carry END")
        return status

    if status != 200:
        raise RuntimeError(f"unsupported response status {status}")

    if "content-length" not in headers:
        raise RuntimeError("200 response missing content-length")

    expected = int(headers["content-length"])
    body_received = 0

    if frame.flags & FLAG_END:
        if expected != 0:
            raise RuntimeError("response ended before body was received")
        return status

    while True:
        frame = recv_frame(sock, verbose)
        if frame is None:
            raise RuntimeError("connection closed during response")

        if frame.type != TYPE_DATA:
            if frame.type not in (TYPE_REQUEST, TYPE_RESPONSE):
                # Unknown types are skipped cleanly.
                continue
            raise RuntimeError(f"unexpected known frame type 0x{frame.type:02x}")

        if frame.id != request_id:
            raise RuntimeError("DATA frame ID mismatch")

        sys.stdout.buffer.write(frame.payload)
        sys.stdout.buffer.flush()
        body_received += len(frame.payload)

        if body_received > expected:
            raise RuntimeError("received more DATA bytes than content-length")

        if frame.flags & FLAG_END:
            break

    if body_received != expected:
        raise RuntimeError(
            f"content-length mismatch: expected {expected}, got {body_received}"
        )

    return status


def main():
    args = sys.argv[1:]
    verbose = False
    if "-v" in args:
        verbose = True
        args.remove("-v")

    if not args:
        usage()
        return 2

    # All targets in this invocation use one persistent TCP connection.
    first_host, first_port, _ = parse_target(args[0])

    request_id = 1
    try:
        with socket.create_connection((first_host, first_port)) as sock:
            for target in args:
                host, port, path = parse_target(target)

                if host != first_host or port != first_port:
                    raise RuntimeError(
                        "all targets in one invocation must use the same host:port"
                    )

                status = request_one(sock, host, path, request_id, verbose)
                if status >= 400:
                    return 1

                request_id += 1
                if request_id > 65535:
                    request_id = 1

    except (OSError, ProtocolError, TruncatedFrame, RuntimeError, ValueError) as e:
        print(f"bcurl: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
