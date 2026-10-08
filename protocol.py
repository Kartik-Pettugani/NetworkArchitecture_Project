"""
HTTP, in Binary - V1 protocol implementation.

Wire format:
    Frame header = 8 bytes, big-endian
      Length : 4 bytes  (payload length only)
      Type   : 1 byte
      Flags  : 1 byte
      ID     : 2 bytes

Types:
    0x01 REQUEST
    0x02 RESPONSE
    0x03 DATA
    0x04-0xFF unknown/reserved

Flags:
    0x01 END
"""

import socket
import struct
from dataclasses import dataclass

HEADER_SIZE = 8
MAX_FRAME_SIZE = 1024 * 1024
DATA_CHUNK_SIZE = 16 * 1024

TYPE_REQUEST = 0x01
TYPE_RESPONSE = 0x02
TYPE_DATA = 0x03

FLAG_END = 0x01

HEADER_NAMES = {
    1: ":method",
    2: ":path",
    3: ":status",
    4: "content-length",
    5: "content-type",
    6: "server",
    7: "date",
    8: "host",
    9: "user-agent",
    10: "accept",
}
HEADER_INDEX = {v: k for k, v in HEADER_NAMES.items()}
LITERAL_ESCAPE = 0xFF


class ProtocolError(Exception):
    """Protocol error while the frame boundary is still intact."""


class TruncatedFrame(Exception):
    """TCP EOF occurred in the middle of a frame."""


@dataclass
class Frame:
    length: int
    type: int
    flags: int
    id: int
    payload: bytes


def read_exact(sock, n):
    """Read exactly n bytes, unless TCP closes first."""
    chunks = []
    remaining = n
    while remaining:
        data = sock.recv(remaining)
        if not data:
            if not chunks:
                return None
            raise TruncatedFrame(f"EOF after {n - remaining} of {n} bytes")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def encode_frame(frame_type, flags, frame_id, payload=b""):
    if not (0 <= frame_type <= 255):
        raise ValueError("type must fit in one byte")
    if not (0 <= flags <= 255):
        raise ValueError("flags must fit in one byte")
    if not (0 <= frame_id <= 65535):
        raise ValueError("id must fit in two bytes")
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError("payload exceeds MAX_FRAME_SIZE")
    return struct.pack("!IBBH", len(payload), frame_type, flags, frame_id) + payload


def read_frame(sock):
    """
    Read one complete frame.

    Returns None for clean EOF exactly at a frame boundary.
    Raises TruncatedFrame for a partial header/payload.
    Raises ProtocolError for an oversized frame.
    """
    header = read_exact(sock, HEADER_SIZE)
    if header is None:
        return None

    length, frame_type, flags, frame_id = struct.unpack("!IBBH", header)

    if length > MAX_FRAME_SIZE:
        raise ProtocolError(f"frame length {length} exceeds MAX_FRAME_SIZE")

    payload = read_exact(sock, length)
    if payload is None:
        raise TruncatedFrame("EOF after frame header")
    return Frame(length, frame_type, flags, frame_id, payload)


def encode_indexed_header(index, value):
    if index not in HEADER_NAMES:
        raise ValueError(f"invalid header index: {index}")
    value_b = value.encode("utf-8")
    if len(value_b) > 255:
        raise ValueError("header value exceeds 255 bytes")
    return bytes([index, len(value_b)]) + value_b


def encode_literal_header(name, value):
    name_b = name.encode("ascii")
    value_b = value.encode("utf-8")
    if not name_b or len(name_b) > 255 or len(value_b) > 255:
        raise ValueError("header name/value exceeds 255 bytes")
    if name.lower() != name:
        raise ValueError("header names must be lowercase")
    return bytes([LITERAL_ESCAPE, len(name_b)]) + name_b + bytes([len(value_b)]) + value_b


def encode_headers(headers):
    """
    Encode a list of (name, value) pairs.
    Static-table names use indexed encoding by default.
    Unknown/custom names use literal encoding.
    """
    out = bytearray()
    for name, value in headers:
        if name in HEADER_INDEX:
            out += encode_indexed_header(HEADER_INDEX[name], value)
        else:
            out += encode_literal_header(name, value)
    return bytes(out)


def decode_headers(payload):
    """
    Decode a header block.

    Unknown indexed header numbers 11-254 are skipped after consuming
    their length-prefixed value, providing forward compatibility.
    """
    headers = []
    pos = 0

    while pos < len(payload):
        index = payload[pos]
        pos += 1

        if index == LITERAL_ESCAPE:
            if pos >= len(payload):
                raise ProtocolError("truncated literal header name length")
            name_len = payload[pos]
            pos += 1
            if name_len == 0 or pos + name_len > len(payload):
                raise ProtocolError("invalid literal header name")
            name = payload[pos:pos + name_len].decode("ascii", errors="strict")
            pos += name_len

            if pos >= len(payload):
                raise ProtocolError("truncated literal header value length")
            value_len = payload[pos]
            pos += 1
            if pos + value_len > len(payload):
                raise ProtocolError("truncated literal header value")
            value = payload[pos:pos + value_len].decode("utf-8", errors="strict")
            pos += value_len

            if name.lower() != name:
                raise ProtocolError("header names must be lowercase")
            headers.append((name, value))
            continue

        if index == 0:
            raise ProtocolError("header index 0 is invalid")

        if pos >= len(payload):
            raise ProtocolError("truncated indexed header value length")
        value_len = payload[pos]
        pos += 1

        if pos + value_len > len(payload):
            raise ProtocolError("truncated indexed header value")
        value_bytes = payload[pos:pos + value_len]
        pos += value_len

        # 1-10 are defined. 11-254 are reserved/future and ignored.
        if index <= 10:
            name = HEADER_NAMES[index]
            try:
                value = value_bytes.decode("utf-8")
            except UnicodeDecodeError as e:
                raise ProtocolError("invalid UTF-8 header value") from e
            headers.append((name, value))

    return headers


def headers_to_dict(headers):
    result = {}
    for name, value in headers:
        result.setdefault(name, []).append(value)
    return result


def hexdump(data):
    """Compact annotated-friendly hexdump."""
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"{offset:04x}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)
