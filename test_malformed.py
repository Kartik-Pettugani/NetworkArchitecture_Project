"""Send a malformed request, then a valid request on the same connection."""

import socket
import sys

from protocol import (
    TYPE_REQUEST, FLAG_END, encode_frame, encode_headers,
    read_frame, decode_headers, hexdump,
)


def raw(frame):
    return (
        frame.length.to_bytes(4, "big")
        + bytes([frame.type, frame.flags])
        + frame.id.to_bytes(2, "big")
        + frame.payload
    )


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000

    with socket.create_connection(("127.0.0.1", port)) as s:
        malformed_payload = encode_headers([
            (":method", "GET"),
            (":path", "/index.html"),
            ("host", "localhost"),
            ("host", "duplicate.example"),
        ])
        malformed = encode_frame(TYPE_REQUEST, FLAG_END, 3, malformed_payload)
        print("MALFORMED REQUEST:")
        print(hexdump(malformed))
        s.sendall(malformed)

        response = read_frame(s)
        if response is None:
            raise RuntimeError("server closed after malformed request")
        print("\n400 RESPONSE:")
        print(hexdump(raw(response)))
        print("headers:", decode_headers(response.payload))

        valid_payload = encode_headers([
            (":method", "GET"),
            (":path", "/hello.txt"),
            ("host", "localhost"),
            ("user-agent", "malformed-test/1.0"),
            ("accept", "*/*"),
        ])
        valid = encode_frame(TYPE_REQUEST, FLAG_END, 4, valid_payload)
        print("\nVALID REQUEST AFTER 400:")
        print(hexdump(valid))
        s.sendall(valid)

        response = read_frame(s)
        if response is None:
            raise RuntimeError("server closed before success response")
        print("\n200 RESPONSE:")
        print(hexdump(raw(response)))
        print("headers:", decode_headers(response.payload))

        while not (response.flags & FLAG_END):
            response = read_frame(s)
            if response is None:
                raise RuntimeError("truncated response")
            print(f"DATA: {len(response.payload)} bytes, flags=0x{response.flags:02x}")


if __name__ == "__main__":
    main()
