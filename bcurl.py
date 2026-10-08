import socket
import sys
import tempfile

from protocol import (
    TYPE_REQUEST, TYPE_RESPONSE, TYPE_DATA,
    FLAG_END, ProtocolError, TruncatedFrame,
    encode_frame, encode_headers, read_frame, decode_headers, hexdump,
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
        print("<<", file=sys.stderr)
        print(hexdump(frame.raw), file=sys.stderr)
    return frame


def request_one(sock, host, path, request_id, verbose):
    headers = [
        (":method", "GET"),
        (":path", path),
        ("host", host),
        ("user-agent", "bcurl-python/1.0"),
        ("accept", "*/*"),
        ("x-request-id", "abcd"),
    ]
    payload = encode_headers(headers)
    request = encode_frame(TYPE_REQUEST, FLAG_END, request_id, payload)
    send_frame(sock, request, verbose)

    # Unknown frame types are explicitly skipped while waiting for RESPONSE.
    while True:
        frame = recv_frame(sock, verbose)
        if frame is None:
            raise RuntimeError("server closed connection before response")
        if frame.type not in (TYPE_RESPONSE, TYPE_DATA, TYPE_REQUEST):
            continue
        break

    if frame.type != TYPE_RESPONSE:
        raise RuntimeError(f"expected RESPONSE, got type 0x{frame.type:02x}")

    try:
        response_headers = decode_headers(frame.payload)
    except (ProtocolError, UnicodeError) as e:
        raise RuntimeError("invalid response headers") from e

    header_dict = {}
    for name, value in response_headers:
        if name in header_dict:
            raise RuntimeError(f"duplicate response header: {name}")
        header_dict[name] = value

    if ":status" not in header_dict:
        raise RuntimeError("response missing :status")

    try:
        status = int(header_dict[":status"])
    except ValueError as e:
        raise RuntimeError("invalid response status") from e

    # ID 0 is valid only for the server's 400 response to a malformed frame.
    if frame.id == 0:
        if not (status == 400 and (frame.flags & FLAG_END)):
            raise RuntimeError("ID 0 is only valid for a malformed-frame 400")
        return status

    if frame.id != request_id:
        raise RuntimeError(
            f"response ID mismatch: expected {request_id}, got {frame.id}"
        )

    if status >= 400:
        if not (frame.flags & FLAG_END):
            raise RuntimeError("error response must carry END")
        return status

    if status != 200:
        raise RuntimeError(f"unsupported response status {status}")

    if "content-length" not in header_dict:
        raise RuntimeError("200 response missing content-length")

    try:
        expected = int(header_dict["content-length"])
    except ValueError as e:
        raise RuntimeError("invalid content-length") from e
    if expected < 0:
        raise RuntimeError("negative content-length")

    body_received = 0
    # Buffer the body until framing and content-length have been validated.
    # SpooledTemporaryFile keeps small bodies in memory and spills large ones to disk.
    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b") as body:
        if frame.flags & FLAG_END:
            if expected != 0:
                raise RuntimeError("response ended before body was received")
        else:
            while True:
                frame = recv_frame(sock, verbose)
                if frame is None:
                    raise RuntimeError("connection closed during response")

                if frame.type not in (TYPE_DATA, TYPE_REQUEST, TYPE_RESPONSE):
                    continue

                if frame.type != TYPE_DATA:
                    raise RuntimeError(f"unexpected known frame type 0x{frame.type:02x}")

                if frame.id != request_id:
                    raise RuntimeError("DATA frame ID mismatch")

                body.write(frame.payload)
                body_received += len(frame.payload)

                if body_received > expected:
                    raise RuntimeError("received more DATA bytes than content-length")

                if frame.flags & FLAG_END:
                    break

        if body_received != expected:
            raise RuntimeError(
                f"content-length mismatch: expected {expected}, got {body_received}"
            )

        body.seek(0)
        while True:
            chunk = body.read(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()

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
                    print(f"bcurl: HTTP-Binary status {status}", file=sys.stderr)
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
