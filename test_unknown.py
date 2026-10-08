"""
Sends an unknown frame type first, then a valid request, on the SAME TCP
connection. The server must skip the unknown frame and still serve the file.
"""

import socket
import sys

from protocol import (
    TYPE_REQUEST, FLAG_END, encode_frame, encode_headers,
    read_frame, decode_headers, hexdump,
)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000

    with socket.create_connection(("127.0.0.1", port)) as s:
        unknown = encode_frame(0x99, 0, 1, b"future-frame-payload")
        print("UNKNOWN FRAME:")
        print(hexdump(unknown))
        s.sendall(unknown)

        payload = encode_headers([
            (":method", "GET"),
            (":path", "/index.html"),
            ("host", "localhost"),
            ("user-agent", "unknown-test/1.0"),
            ("accept", "*/*"),
        ])
        request = encode_frame(TYPE_REQUEST, FLAG_END, 2, payload)
        print("\nREQUEST:")
        print(hexdump(request))
        s.sendall(request)

        response = read_frame(s)
        if response is None:
            raise RuntimeError("server closed connection")

        print("\nRESPONSE:")
        raw = (
            response.length.to_bytes(4, "big")
            + bytes([response.type, response.flags])
            + response.id.to_bytes(2, "big")
            + response.payload
        )
        print(hexdump(raw))
        print("headers:", decode_headers(response.payload))

        # Consume DATA frames.
        while not (response.flags & FLAG_END):
            response = read_frame(s)
            if response is None:
                raise RuntimeError("truncated response")
            if response.type == 3:
                print(f"DATA: {len(response.payload)} bytes, flags=0x{response.flags:02x}")


if __name__ == "__main__":
    main()
