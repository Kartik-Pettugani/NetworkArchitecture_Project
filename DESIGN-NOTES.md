# Design Notes

These notes explain the main choices behind the wire protocol. They are intentionally separate from `PROTOCOL.md`, whose job is to be a compact implementation contract.

## Why TCP?

TCP gives us a reliable, ordered byte stream. It does not preserve application message boundaries, so the protocol adds a fixed-size frame header and a payload length.

## Why an 8-byte frame header?

An implementation can always perform the same two-step parse:

1. read exactly 8 bytes;
2. decode the length and read exactly that many payload bytes.

The header is small enough not to dominate small control frames while remaining easy to parse with standard binary packing.

## Why a 4-byte length instead of 3 bytes?

Three bytes would be sufficient for a smaller maximum frame, but a 32-bit field is simpler to parse and leaves room for growth. The implementation still enforces a 1 MiB maximum so a peer cannot request an arbitrarily large allocation.

## Why 1-byte type and flags?

The protocol only needs three frame types today. One byte gives 256 possible types, while one byte of flags leaves eight independent flag bits. Both fields therefore have extension room without making the frame header larger.

## Why a 2-byte ID?

The protocol intentionally permits only one outstanding request at a time. 65,535 request IDs are therefore more than enough. A 31-bit HTTP/2-style stream identifier would add four bytes of header space without providing a useful capability here.

## Why no connection preface?

The frame header already gives the receiver a deterministic binary interpretation of the first bytes. A separate magic/version preface would add bytes and another parsing rule without being required by the project.

## Why these ten indexed headers?

The course asks for the ten names actually sent to be numbered, while other names are length-prefixed. The table therefore contains exactly the names used by this implementation: the request pseudo-headers, response status headers, and the common request/response metadata.

## Why one-byte header lengths?

The project headers are short. A 255-byte maximum for an individual name or value is enough for the implemented messages and keeps header encoding compact. A larger field would add complexity without helping the current application.

## Why indexed + literal headers?

Repeated names such as `:method`, `:path`, `content-type`, and `server` do not need their full names transmitted every time. The small static table demonstrates the basic idea behind indexed header compression. Literal encoding keeps the protocol extensible for names such as `x-request-id` without implementing full HPACK.

## Why unknown-frame skipping?

A receiver can use the frame length to consume an unknown frame safely. Skipping unknown types means a future sender can add a frame type without making an older receiver lose synchronization with the TCP stream.

## Why separate RESPONSE and DATA?

Response metadata and file bytes have different roles. The RESPONSE tells the client the status and expected body size; DATA streams the actual file bytes. This avoids placing a potentially large file inside a single frame.

## Why 16 KiB DATA chunks?

A fixed chunk size bounds the amount of file data held in memory at once while keeping the number of frames reasonable for normal files. The final chunk is marked with `END`.

## Why persistent connections?

The assignment explicitly requires the client not to open a second connection for sequential requests. Keeping the connection open also demonstrates why explicit application framing is necessary over TCP.

## Why open the file before sending 200?

A successful response promises that the server can produce the requested file. Opening the file first lets an initial I/O failure become a `500` response instead of sending `200` and discovering the failure afterward. If a later read fails, the response has already started, so the server closes and the client detects truncation.

## Why ID 0?

A normal request ID is echoed by the server. If the frame header itself is malformed, however, the ID may not be trustworthy. ID 0 is reserved for the connection-level `400` case, and the client accepts ID 0 only for that specific response.

## Why threaded connections?

The protocol allows a connection to remain idle while the client is between requests. Serving each accepted connection in a thread prevents one idle or slow connection from blocking every other client during a pair demonstration.
