    # HTTP, in Binary — Protocol Specification

> **Purpose:** This document completely defines the wire protocol so that another developer can implement `bserve` and `bcurl` without asking for additional protocol clarifications.

---

## 1. Design Goal

The project is a small **binary HTTP-like protocol over TCP**.

We keep the useful ideas from HTTP:

- `GET`
- paths
- status codes
- headers
- content length
- content type
- persistent connections

but replace text-based HTTP message framing with an explicit binary frame format.

The protocol is **inspired by HTTP/2 framing**, especially the idea of a fixed frame header containing:

```text
Length | Type | Flags | ID
```

It is **not HTTP/2**, and it does not attempt to implement HTTP/2's full stream, HPACK, or multiplexing machinery.

### Design principle

> **Keep the protocol small enough to implement from scratch, but explicit enough that every byte on the wire has a defined meaning.**

---

# 2. Connection Model

The protocol uses:

```text
TCP
```

The connection is persistent.

A single TCP connection can carry multiple sequential requests.

```text
Client                                      Server
  |                                           |
  |------------ TCP connection -------------->|
  |                                           |
  |------------ REQUEST --------------------->|
  |<----------- RESPONSE --------------------|
  |<----------- DATA -------------------------|
  |                                           |
  |------------ REQUEST --------------------->|
  |<----------- RESPONSE --------------------|
  |<----------- DATA -------------------------|
  |                                           |
  |------------ TCP close ------------------->|
```

## Why TCP?

TCP already provides:

- reliable delivery
- ordered bytes
- retransmission
- duplicate suppression

Therefore, the application protocol does **not** need to implement reliability itself.

The protocol only needs to solve the application-level problem:

> **Where does one frame end, what does it mean, and which request does it belong to?**

---

# 3. Why There Is No Connection Preface

The protocol does **not** use a connection preface.

A possible alternative would have been:

```text
BHTTP/1
```

before the first frame.

We deliberately do not use it.

### Reason

The project already has a complete binary framing mechanism. Adding a preface would introduce another wire-level structure that the protocol does not otherwise need.

The first 8 bytes of every protocol message already identify:

```text
Length
Type
Flags
ID
```

Unknown-frame skipping also gives us an extension mechanism.

Therefore:

```text
TCP connection
    ↓
first frame
```

is sufficient.

This keeps connection setup simpler and makes the frame format the single fundamental unit of the protocol.

---

# 4. Frame Format

Every frame begins with exactly **8 bytes**.

```text
  Byte 0                Byte 4   Byte 5   Byte 6       Byte 8
     |                     |        |        |            |
     v                     v        v        v            v
    +---------------------+--------+--------+------------+
    |      Length (4)     | Type   | Flags  | ID (2)     |
    +---------------------+--------+--------+------------+
```

| Field | Size | Meaning |
|---|---:|---|
| Length | 4 bytes | Number of payload bytes |
| Type | 1 byte | Identifies the frame |
| Flags | 1 byte | Additional frame information |
| ID | 2 bytes | Request/message identifier |

All integer fields use **big-endian/network byte order**.

Therefore:

```text
Frame = 8-byte Header + Payload
```

The `Length` field counts **only the payload**.

---

# 5. Why an 8-Byte Header?

The header contains exactly four pieces of information:

```text
Length
Type
Flags
ID
```

The sizes are:

```text
Length = 4 bytes
Type   = 1 byte
Flags  = 1 byte
ID     = 2 bytes
----------------
Total  = 8 bytes
```

This is deliberately similar in structure to HTTP/2's fixed framing header, while using sizes appropriate for this project.

### Why fixed-size framing?

The receiver can always do:

```text
read 8 bytes
    ↓
decode header
    ↓
read exactly Length bytes
```

This is much simpler than searching for delimiters or trying to determine where a variable-length message ends.

### Why not a variable-length header?

A variable-length frame header would itself need another mechanism to determine where the header ends.

The fixed 8-byte header avoids that recursive problem.

---

# 6. Length Field

```text
Length = 4 bytes
```

The value represents the payload size only.

Example:

```text
00 00 00 C5
```

means:

```text
Length = 197 bytes
```

The complete frame therefore occupies:

```text
8 + 197 = 205 bytes
```

## Why 4 bytes?

Three bytes would be sufficient for a 1 MiB maximum payload.

However, four bytes provide a natural 32-bit integer for parsing and leave plenty of numeric space for future protocol versions.

The implementation still imposes a much smaller safety limit:

```text
MAX_FRAME_SIZE = 1 MiB
```

Therefore the 4-byte field provides flexibility while the 1 MiB limit provides resource protection.

---

# 7. Maximum Frame Size

Every implementation MUST enforce:

```text
MAX_FRAME_SIZE = 1,048,576 bytes
                 = 1 MiB
```

A received frame with:

```text
Length > 1 MiB
```

is a malformed frame.

### Why have a limit?

The length field is supplied by the peer.

Without a limit, a malicious peer could advertise something like:

```text
Length = 4,000,000,000
```

and cause the receiver to attempt an unreasonable allocation/read.

The 1 MiB limit gives predictable resource usage.

---

# 8. Frame Types

The protocol defines:

| Type | Hex | Meaning |
|---|---:|---|
| REQUEST | `0x01` | Client request |
| RESPONSE | `0x02` | Server response headers |
| DATA | `0x03` | File/body bytes |
| Unknown | `0x04–0xFF` | Reserved for future use |

---

# 9. Why Separate REQUEST, RESPONSE and DATA?

A response contains two fundamentally different kinds of information:

### Metadata

For example:

```text
:status = 200
content-length = 197
content-type = text/html
```

### Actual file bytes

For example:

```text
<!doctype html>
...
```

We therefore separate them:

```text
RESPONSE
    ↓
metadata/header block

DATA
    ↓
raw file bytes
```

This is cleaner than mixing headers and arbitrary file bytes in the same structure.

It also allows the DATA payload to contain **any binary data**, including:

```text
images
PDFs
archives
executables
```

without escaping.

---

# 10. Unknown Frame Types

Types `0x04–0xFF` are currently unknown/reserved.

If a receiver encounters an unknown type, it MUST:

1. Read the 8-byte frame header.
2. Check `Length <= MAX_FRAME_SIZE`.
3. Read exactly `Length` payload bytes.
4. Discard the payload.
5. Continue processing the connection.

Example:

```text
UNKNOWN FRAME
     |
     v
read header
     |
     v
read 20-byte payload
     |
     v
discard
     |
     v
continue
```

### Why?

This gives the protocol **forward compatibility**.

Suppose V2 introduces:

```text
0x04 = METADATA
```

A the protocol implementation does not understand the new frame, but it can still safely skip it and continue processing frames it does understand.

This is one of the most important benefits of explicit length-based framing.

---

# 11. Flags

The protocol defines one flag:

```text
0x01 = END
```

All other bits are reserved.

```text
0x01       END
0x02-0x80  Reserved
0x80-0xFF  Reserved
```

More precisely, implementations treat every bit except bit `0` as undefined.

### Sender rule

Senders MUST send zero for undefined flags.

### Receiver rule

Receivers MUST ignore undefined flags.

---

# 12. Why Do We Need END If Length Already Exists?

The `Length` field answers:

> **Where does this frame end?**

The `END` flag answers:

> **Is this the final frame belonging to this logical message?**

These are different questions.

For example, a 100 KiB file might be divided into:

```text
RESPONSE
DATA 16 KiB
DATA 16 KiB
DATA 16 KiB
DATA 16 KiB
DATA 16 KiB
DATA 16 KiB
DATA 4 KiB + END
```

Each frame has its own length.

But only the final DATA frame tells us:

```text
the complete response body is finished
```

Therefore:

```text
Length = frame boundary
END    = message boundary
```

This separation is intentional.

---

# 13. Frame IDs

The ID field is:

```text
2 bytes
```

giving:

```text
0–65535
```

The values are defined as:

| ID | Meaning |
|---:|---|
| `0` | Connection-level / malformed-frame response |
| `1–65535` | Request IDs |

The server echoes the client's request ID.

Example:

```text
REQUEST  ID=7
RESPONSE ID=7
DATA     ID=7
```

---

# 14. Why Only 2 Bytes for ID?

The protocol deliberately supports:

```text
one request outstanding at a time
```

There is therefore no need for a huge identifier space.

A 16-bit ID provides:

```text
65,535 usable request IDs
```

which is more than enough for sequential requests on one connection.

A larger ID would increase the frame header without providing useful functionality in this protocol.

### Future use

A future protocol version could use the ID to support multiplexing or multiple simultaneous requests.

the protocol does not require that complexity.

---

# 15. ID 0

ID `0` is reserved.

A normal client request MUST NOT use:

```text
ID = 0
```

If a client sends a request with ID 0, the server responds:

```text
400 Bad Request
ID = 0
END
```

ID 0 is also used when the frame header itself is invalid enough that the request ID cannot safely be trusted.

---

# 16. Request ID Lifecycle

The client starts with:

```text
ID = 1
```

and increments for each request.

For example:

```text
Request 1 -> ID 1
Request 2 -> ID 2
Request 3 -> ID 3
```

When reaching:

```text
65535
```

the next available ID may wrap to:

```text
1
```

because the protocol never has an older request outstanding at the same time.

---

# 17. Header Design

The protocol has a small static header table.

| Index | Header |
|---:|---|
| 1 | `:method` |
| 2 | `:path` |
| 3 | `:status` |
| 4 | `content-length` |
| 5 | `content-type` |
| 6 | `server` |
| 7 | `date` |
| 8 | `host` |
| 9 | `user-agent` |
| 10 | `accept` |

This gives exactly **10 defined header indexes** in this protocol.

---

# 18. Why Use a Header Table?

HTTP-style headers repeat frequently.

For example, every request contains:

```text
:method
:path
host
user-agent
accept
```

and every successful response contains:

```text
:status
content-length
content-type
server
date
```

Sending the full names every time wastes bytes.

Instead of:

```text
content-length
```

we can send:

```text
04
```

followed by its value.

This uses a small version of the idea behind HTTP/2's header compression mechanisms.

We are **not implementing HPACK**.

We only borrow the useful idea of assigning compact numeric identifiers to frequently used header names.

---

# 19. Indexed Header Encoding

The format is:

```text
+-------------+------------------+----------------+
| Index (1 B) | Value Length(1 B)| Value          |
+-------------+------------------+----------------+
```

Example:

```text
01 03 47 45 54
```

means:

```text
01       index 1 = :method
03       value length
47 45 54  "GET"
```

Another example:

```text
04 03 31 39 37
```

means:

```text
04       index 4 = content-length
03       length = 3
31 39 37  "197"
```

---

# 20. Literal Header Encoding

Not every possible header can be placed in the static table.

Custom headers therefore use:

```text
FF
Name-Length
Name
Value-Length
Value
```

Example:

```text
FF 0C 78 2D 72 65 71 75 65 73 74 2D 69 64 04 61 62 63 64
```

means:

```text
x-request-id: abcd
```

Breakdown:

```text
FF                  literal marker
0C                  name length = 12
78 2d ... 69 64    x-request-id
04                  value length = 4
61 62 63 64         abcd
```

---

# 21. Why Use 0xFF as the Literal Marker?

The static table currently uses:

```text
1–10
```

and:

```text
11–254
```

are reserved for future indexed headers.

Therefore:

```text
0xFF
```

can safely be dedicated to the literal representation.

This makes the first byte immediately tell the decoder:

```text
1–254 -> indexed header
255   -> literal header
```

---

# 22. Header Lengths

Name and value lengths are each:

```text
1 byte
```

Therefore:

```text
maximum name length  = 255 bytes
maximum value length = 255 bytes
```

### Why one byte?

The headers used by this project are small:

```text
:path
host
user-agent
content-type
content-length
```

There is no need for a larger length field for every header.

Using one byte keeps header encoding compact and easy to parse.

---

# 23. Header Rules

Implementations MUST follow these rules:

1. Header names are lowercase.
2. Index `0` is invalid.
3. Indexes `1–10` are defined.
4. Indexes `11–254` are reserved/future.
5. `0xFF` means literal header.
6. Header name length is one byte.
7. Header value length is one byte.
8. Duplicate headers are malformed in this protocol.
9. A literal header is allowed even if its name exists in the static table.
10. Senders SHOULD use indexed encoding whenever possible.

---

# 24. Why Are Header Values Length-Prefixed?

A delimiter such as:

```text
name:value\r\n
```

would require special handling if arbitrary binary bytes could appear in values.

Length-prefixing instead tells the receiver exactly how many bytes belong to the value:

```text
value length = N
read N bytes
```

This is binary-safe and does not require escaping delimiter characters.

The same general principle is used for frame payloads.

---

# 25. REQUEST Frame

A REQUEST frame uses:

```text
Type = 0x01
```

Its payload is a header block.

There is **no request body in this protocol**.

A valid request MUST contain exactly one:

```text
:method
:path
```

It may also contain:

```text
host
user-agent
accept
```

The implementation also sends:

```text
x-request-id: abcd
```

to demonstrate literal header encoding.

---

# 26. Why Does the protocol Support Only GET?

The project is primarily a file-serving protocol.

The required operation is:

```text
GET /path
```

Adding:

```text
POST
PUT
DELETE
PATCH
```

would require defining request bodies, body framing, write semantics, and additional server behavior.

Those features are outside the the protocol goal.

Therefore The protocol deliberately keeps the method space small:

```text
GET only
```

Unsupported methods receive:

```text
400 Bad Request
```

---

# 27. Request Path Rules

The `:path` header MUST:

- exist exactly once
- be non-empty
- start with `/`
- contain no NUL byte
- not contain `?`
- not use percent-decoding
- not contain `..` components
- not use backslash traversal
- resolve inside the configured server root

Example:

```text
/index.html
```

is valid.

Example:

```text
/../../secret.txt
```

is invalid.

---

# 28. Why Are Query Strings Not Supported?

the protocol is a file-serving protocol.

The mapping is intentionally simple:

```text
URL path
    ↓
file under server root
```

Supporting:

```text
/index.html?x=10
```

would require defining whether:

```text
?x=10
```

is part of the filename or request metadata.

the protocol avoids this ambiguity.

Therefore query strings are rejected.

---

# 29. Why Is Percent-Decoding Not Supported?

A path such as:

```text
/%2e%2e/secret.txt
```

can represent:

```text
/../secret.txt
```

after URL decoding.

If different parts of the server decode at different stages, path traversal checks can become incorrect.

the protocol therefore does not implement percent-decoding.

A path containing `%` is rejected.

This keeps path validation deterministic.

---

# 30. File Root and Path Security

The server is started with:

```text
python bserve.py ./www 9000
```

The request:

```text
/index.html
```

maps to:

```text
./www/index.html
```

The server resolves the final filesystem path and verifies that it remains inside the configured root.

This protects against:

```text
../
```

traversal and symlinks pointing outside the root.

A directory is not served as a listing.

A directory request returns:

```text
404
```

---

# 31. RESPONSE Frame

A RESPONSE frame uses:

```text
Type = 0x02
```

Its payload contains response headers.

A successful response contains:

```text
:status
content-length
content-type
server
date
```

Example:

```text
:status: 200
content-length: 197
content-type: text/html
server: bserve-python/1.0
date: Thu, 08 Oct 2026 05:28:44 GMT
```

---

# 32. Why Is content-length Included If DATA Already Has Length?

Each DATA frame already has a frame length.

However, that tells us only:

> How large is this particular DATA frame?

`content-length` tells us:

> How large should the complete response body be?

For example:

```text
content-length = 197
```

could be transmitted as:

```text
DATA 100 bytes
DATA 97 bytes + END
```

The client can verify:

```text
100 + 97 = 197
```

Therefore `content-length` provides an end-to-end integrity check for the body.

---

# 33. DATA Frame

A DATA frame uses:

```text
Type = 0x03
```

The payload contains raw file bytes.

No encoding is applied.

For a 197-byte file:

```text
RESPONSE
DATA + END
```

For a larger file:

```text
RESPONSE
DATA
DATA
DATA
DATA + END
```

All DATA frames use the request ID.

---

# 34. Why Is DATA Limited to 16 KiB Chunks?

The server uses:

```text
DATA_CHUNK_SIZE = 16 KiB
```

for normal file transmission.

This is a practical streaming size:

- large enough to avoid excessive frame overhead
- small enough that the server does not need to load an entire large file into memory
- simple to demonstrate in a frame-based protocol

The 16 KiB value is a **sending policy**, not the maximum frame size.

The maximum legal frame is still:

```text
1 MiB
```

---

# 35. Empty Files

An empty file has:

```text
content-length = 0
```

The server sends:

```text
RESPONSE + END
```

and does **not** send:

```text
DATA with zero payload
```

### Why?

An empty DATA frame carries no useful information.

The response itself can simply say:

```text
200
content-length = 0
END
```

This keeps the wire representation minimal.

---

# 36. Response Message Structure

## Non-empty file

```text
+-------------------+
| RESPONSE          |
| ID = N            |
| END = 0           |
+-------------------+
          |
          v
+-------------------+
| DATA              |
| ID = N            |
| END = 0           |
+-------------------+
          |
          v
       ...
          |
          v
+-------------------+
| DATA              |
| ID = N            |
| END = 1           |
+-------------------+
```

## Empty file

```text
+-------------------+
| RESPONSE          |
| ID = N            |
| END = 1           |
+-------------------+
```

---

# 37. Status Codes

The protocol defines:

| Status | Meaning |
|---:|---|
| `200` | Successful file response |
| `400` | Malformed request/protocol |
| `404` | File not found or directory |
| `500` | Server-side I/O error |

The server does not need to implement the complete HTTP status-code space.

This is intentional: The protocol defines only the behavior needed by the project.

---

# 38. 400 Bad Request

Use `400` when the request is understandable as a frame but its contents are invalid.

Examples:

```text
missing :method
missing :path
duplicate :path
unsupported method
invalid path
path traversal
request ID = 0
invalid header encoding
unexpected known frame type
```

The server sends:

```text
RESPONSE + END
```

with the request ID when the frame boundary is intact.

The connection remains open.

This is important for persistent connections:

```text
bad request
    ↓
400
    ↓
connection remains open
    ↓
next valid request
    ↓
200
```

---

# 39. 404 Not Found

`404` means the request was syntactically valid, but the requested resource does not exist as a regular file.

Examples:

```text
/www/missing.html
```

or:

```text
/www/some-directory/
```

The response is:

```text
RESPONSE + END
```

with:

```text
:status = 404
```

The connection remains open.

---

# 40. 500 Internal Server Error

`500` is used when a server-side I/O problem occurs before a response can be sent.

For example:

```text
file cannot be opened
```

If the RESPONSE has already been sent and a later file read fails, the server cannot send another status code for the same response.

It therefore closes the connection.

The client detects a truncated body because:

```text
received DATA bytes < content-length
```

---

# 41. Malformed Frame vs Malformed Request

These are deliberately different.

## Case A — Frame is valid, payload is invalid

Example:

```text
Length = 20
Type = REQUEST
ID = 7
Payload = malformed headers
```

The boundary is known.

Therefore:

```text
400 ID=7 END
```

and keep the connection open.

## Case B — Frame itself is invalid

Example:

```text
Length = 10,000,000
```

which exceeds the maximum.

The frame cannot safely be processed.

The server sends, when possible:

```text
400 ID=0 END
```

and closes the connection.

---

# 42. Why Does a Malformed Frame Use ID 0?

If the frame header itself is invalid, the server must not rely on the received ID as a valid request identifier.

For example, if a frame is malformed or truncated, the ID may not represent a valid request.

Therefore:

```text
ID = 0
```

means:

> This error is connection-level / the request ID could not safely be trusted.

---

# 43. TCP Partial Reads

TCP is a byte stream.

A sender may transmit:

```text
8-byte header
```

but the receiver might get:

```text
3 bytes
```

followed by:

```text
5 bytes
```

The receiver therefore MUST NOT assume:

```python
sock.recv(8)
```

returns exactly 8 bytes.

Instead it must repeatedly read until all required bytes arrive.

Conceptually:

```text
read_exactly(8)
      |
      +-- recv
      +-- recv
      +-- recv
      |
      v
complete header
```

The same applies to the payload.

---

# 44. EOF Rules

## Clean EOF

If TCP closes exactly at a frame boundary:

```text
no partial frame
```

the connection has ended normally.

## Truncated frame

If TCP closes after only part of:

```text
header
```

or:

```text
payload
```

the frame is truncated.

This is a protocol error.

The receiver must not treat the partial message as valid.

---

# 45. Client State Machine

The client follows:

```text
CONNECTED
    |
    v
SEND REQUEST
    |
    v
WAIT RESPONSE
    |
    +---- unknown frame ----> skip
    |
    v
RESPONSE
    |
    +---- END ----> COMPLETE
    |
    v
WAIT DATA
    |
    +---- unknown frame ----> skip
    |
    v
DATA
    |
    +---- END ----> COMPLETE
    |
    +---- no END ----> WAIT DATA
```

The client MUST reject:

- DATA before RESPONSE
- RESPONSE with wrong ID
- DATA with wrong ID
- REQUEST from the server
- malformed headers
- truncated DATA
- content-length mismatch
- DATA after the response has ended

---

# 46. Server State Machine

The server follows:

```text
CONNECTED
    |
    v
WAIT REQUEST
    |
    +---- unknown frame ----> skip
    |
    +---- malformed request ----> 400
    |                              |
    |                              v
    |                         WAIT REQUEST
    |
    v
RESPONSE
    |
    +---- no body ----> END
    |                    |
    |                    v
    |               WAIT REQUEST
    |
    v
DATA
    |
    v
FINAL DATA + END
    |
    v
WAIT REQUEST
```

the protocol processes one request at a time.

---

# 47. Client Verbose Mode

The client supports:

```text
-v
```

Verbose mode prints every transmitted and received frame as hexadecimal.

The hex dump is written to:

```text
stderr
```

The actual file body remains on:

```text
stdout
```

This distinction is important because it allows:

```powershell
python bcurl.py localhost:9000/index.html > output.html
```

without diagnostic output corrupting the file.

---

# 48. Example Wire Format

Consider:

```text
GET /index.html
```

The first 8 bytes of the request are:

```text
00 00 00 47 01 01 00 01
```

Decode:

```text
00 00 00 47   Length = 71
01            Type = REQUEST
01            Flags = END
00 01         ID = 1
```

The payload then contains the encoded headers.

---

# 49. Example Response Wire Format

Suppose the file size is 197 bytes.

The response frame begins:

```text
00 00 00 47 02 00 00 01
```

Decode:

```text
00 00 00 47   Length = 71
02            Type = RESPONSE
00            Flags = none
00 01         ID = 1
```

The response payload contains:

```text
:status = 200
content-length = 197
content-type = text/html
server = bserve-python/1.0
date = ...
```

Then the DATA frame begins:

```text
00 00 00 C5 03 01 00 01
```

Decode:

```text
00 00 00 C5   Length = 197
03            Type = DATA
01            Flags = END
00 01         ID = 1
```

Therefore the entire file is contained in that DATA payload.

---

# 50. Example Unknown Frame

A test frame may begin:

```text
00 00 00 14 99 00 00 01
```

Decode:

```text
Length = 20
Type   = 0x99
Flags  = 0
ID     = 1
```

Because `0x99` is unknown, the receiver:

```text
reads 20 bytes
     ↓
discards them
     ↓
continues
```

A valid REQUEST immediately afterward must still work.

This proves that the frame format is extensible.

---

# 51. Persistent Connection Example

The following client command:

```powershell
python bcurl.py localhost:9000/index.html localhost:9000/hello.txt
```

uses one TCP connection:

```text
TCP connection
      |
      +-- REQUEST ID=1 /index.html
      |      |
      |      +-- RESPONSE ID=1
      |      +-- DATA ID=1 END
      |
      +-- REQUEST ID=2 /hello.txt
             |
             +-- RESPONSE ID=2
             +-- DATA ID=2 END
```

The connection is closed only after the final request completes.

---

# 52. Client Output Rules

For:

```text
200
```

the client writes the response body to stdout.

For:

```text
400
404
500
```

the client does not write a response body because error responses have no body.

The client exits non-zero for all `4xx` and `5xx` responses.

Protocol errors also result in a non-zero exit status.

---

# 53. Content Types

The server uses:

| Extension | Content-Type |
|---|---|
| `.html` | `text/html` |
| `.txt` | `text/plain` |
| `.css` | `text/css` |
| `.js` | `application/javascript` |
| `.jpg` | `image/jpeg` |
| `.png` | `image/png` |
| Other | `application/octet-stream` |

### Why a fallback?

The server must still be able to serve an unknown file extension.

Rather than failing the request, it uses:

```text
application/octet-stream
```

which represents generic binary data.

---

# 54. Date Header

The server sends the date using the HTTP-style format:

```text
Thu, 08 Oct 2026 05:28:44 GMT
```

Although the overall protocol is binary, using the familiar HTTP date representation keeps the field human-readable and easy to inspect during debugging.

---

# 55. Why Some Values Are Still Text

The protocol is **binary framed**, not necessarily binary encoded in every field.

For example:

```text
content-length = "197"
```

rather than a binary integer.

### Why?

The project goal is to demonstrate:

- binary framing
- explicit lengths
- frame types
- IDs
- extensibility

There is little benefit in creating a separate binary representation for every small HTTP-style value.

Using:

```text
[length][bytes]
```

keeps header parsing uniform.

Therefore:

```text
Frame structure = binary
Header names     = indexed/literal binary representation
Header values    = length-prefixed byte strings
File body        = raw bytes
```

---

# 56. Design Relationship to HTTP/2

The protocol takes inspiration from HTTP/2 in two main areas.

## Framing

HTTP/2 uses a fixed frame header containing:

```text
Length
Type
Flags
Stream Identifier
```

Our protocol similarly uses:

```text
Length
Type
Flags
ID
```

## Header indexing

HTTP/2's HPACK uses indexed representations for common headers.

Our protocol uses a much simpler static table:

```text
1 -> :method
2 -> :path
...
10 -> accept
```

We deliberately do **not** implement:

- HTTP/2 streams
- multiplexing
- HPACK dynamic tables
- HPACK Huffman coding
- HTTP/2 connection preface
- HTTP/2 SETTINGS
- HTTP/2 flow control

The goal is to understand and implement the underlying ideas, not reproduce HTTP/2.

---

# 57. Why We Do Not Implement HPACK

HPACK provides powerful compression mechanisms:

- static table
- dynamic table
- Huffman coding

For this project, the important idea is indexed representation for frequently used headers:

```text
frequently used header name
        ↓
small numeric index
```

Implementing dynamic tables and Huffman coding would substantially increase protocol complexity without being necessary for file serving.

Therefore The protocol uses:

```text
small fixed static table
+
literal fallback
```

---

# 58. Error Handling Summary

| Situation | Response | Connection |
|---|---|---|
| Valid GET | `200` | Keep open |
| Missing file | `404` | Keep open |
| Directory | `404` | Keep open |
| Malformed request payload | `400` | Keep open |
| Unsupported method | `400` | Keep open |
| Invalid path | `400` | Keep open |
| ID = 0 request | `400` | Keep open |
| Unexpected known frame | `400` | Keep open |
| Oversized frame | `400 ID=0` if possible | Close |
| Truncated frame | None if peer already closed | Close |
| Server I/O error before response | `500` | Keep/close as appropriate |
| Server I/O error after response | Cannot change status | Close |
| Content-length mismatch at client | Client error | Close |

---

# 59. Implementation Limits

the protocol intentionally has the following limits:

```text
Transport              TCP
Frame header            8 bytes
Maximum frame payload  1 MiB
DATA chunk              16 KiB
Request method          GET only
Request body            None
Request concurrency     One outstanding request
Request ID              16 bits
Header name length      255 bytes
Header value length     255 bytes
Static header entries   10
```

These are protocol decisions, not implementation accidents.

---

# 60. Complete Wire-Level Contract

A conforming implementation must therefore follow this exact model:

```text
TCP connection
      |
      v
Read 8-byte frame header
      |
      +-- Length > 1 MiB --> malformed frame
      |
      v
Read exactly Length bytes
      |
      +-- Unknown Type --> discard and continue
      |
      +-- REQUEST
      |       |
      |       +-- validate headers
      |       +-- validate ID
      |       +-- validate method
      |       +-- validate path
      |       |
      |       +-- error --> RESPONSE 400/404/500 + END
      |       |
      |       +-- success
      |              |
      |              +-- RESPONSE 200
      |              |
      |              +-- empty file --> RESPONSE + END
      |              |
      |              +-- non-empty file
      |                     |
      |                     +-- DATA
      |                     +-- DATA
      |                     +-- DATA + END
      |
      v
Return to waiting for the next request
```

---

# 61. Reference Commands

Start server:

```powershell
python .\bserve.py .\www 9000
```

Normal request:

```powershell
python .\bcurl.py localhost:9000/index.html
```

Verbose request:

```powershell
python .\bcurl.py -v localhost:9000/index.html
```

Multiple requests over one TCP connection:

```powershell
python .\bcurl.py localhost:9000/index.html localhost:9000/hello.txt
```

404:

```powershell
python .\bcurl.py -v localhost:9000/missing.html
```

Unknown-frame compatibility test:

```powershell
python .\test_unknown.py 9000
```

---

# 62. Final Design Decisions

The following decisions are **fixed for V1**:

| Decision | the protocol choice | Reason |
|---|---|---|
| Transport | TCP | Reliable ordered byte stream |
| Connection | Persistent | Demonstrates keep-alive behavior |
| Preface | None | Unnecessary additional wire format |
| Frame header | 8 bytes | Fixed, simple, HTTP/2-inspired |
| Length | 4 bytes | Simple 32-bit parsing + future room |
| Max frame | 1 MiB | Resource protection |
| Type | 1 byte | 256 possible frame types |
| Flags | 1 byte | 8 flag bits |
| ID | 2 bytes | Sufficient for one outstanding request |
| Byte order | Big-endian | Standard network byte order |
| Request type | `0x01` | Simple fixed assignment |
| Response type | `0x02` | Simple fixed assignment |
| DATA type | `0x03` | Separates body from metadata |
| Unknown types | Skip | Forward compatibility |
| END | `0x01` | Separates frame boundary from message boundary |
| Method | GET only | File-serving scope |
| Request body | None | Keeps the protocol simple |
| Header table | 10 entries | Compress common names without HPACK complexity |
| Literal marker | `0xFF` | Outside current static indexes |
| Header lengths | 1 byte | Compact for small HTTP-style headers |
| DATA chunk | 16 KiB | Streaming without large memory use |
| Body check | content-length | End-to-end body-size validation |
| Empty body | RESPONSE + END | Avoid useless empty DATA frame |
| Query strings | Unsupported | Simple deterministic path-to-file mapping |
| Percent decoding | Unsupported | Avoid traversal ambiguity |
| Directories | 404 | No directory listing |
| Error body | None | Simple error framing |
| Malformed payload | 400, keep connection | Frame boundary remains usable |
| Malformed frame | 400 ID 0 + close | Frame cannot safely be trusted |
| Client verbose output | stderr | Keeps stdout usable for file data |

---

# 63. Conformance Checklist

An implementation is conformant when all of the following are true:

- [ ] Uses TCP.
- [ ] Uses the 8-byte frame header.
- [ ] Uses big-endian integers.
- [ ] Treats Length as payload-only.
- [ ] Enforces the 1 MiB frame limit.
- [ ] Implements REQUEST, RESPONSE and DATA.
- [ ] Skips unknown frame types.
- [ ] Implements the END flag.
- [ ] Reserves ID 0.
- [ ] Echoes request IDs.
- [ ] Implements the 10-entry static header table.
- [ ] Implements literal headers with `0xFF`.
- [ ] Rejects invalid header encodings.
- [ ] Supports GET.
- [ ] Validates `:method` and `:path`.
- [ ] Prevents path traversal.
- [ ] Does not perform percent-decoding.
- [ ] Returns 404 for missing files/directories.
- [ ] Returns 200 for successful files.
- [ ] Sends `content-length`.
- [ ] Sends DATA as raw bytes.
- [ ] Uses END on the final DATA frame.
- [ ] Uses RESPONSE + END for empty files.
- [ ] Validates received body length.
- [ ] Keeps the TCP connection persistent.
- [ ] Correctly handles TCP partial reads.
- [ ] Returns 400 for malformed request payloads.
- [ ] Uses ID 0 for malformed-frame errors.
- [ ] Closes after malformed frame headers.
- [ ] Returns non-zero from the client for 4xx/5xx.
- [ ] Supports verbose hexadecimal frame output.

---

# 64. One-Sentence Protocol Definition

> **HTTP, in Binary the protocol is a persistent TCP protocol in which HTTP-like requests and responses are represented as length-prefixed binary frames with explicit types, flags and request IDs, using a small indexed header table and raw DATA frames for binary-safe file transfer.**
