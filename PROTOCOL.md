# HTTP, in Binary — Protocol Specification

## 1. Purpose and transport

This protocol serves files over **one persistent TCP connection**. The server is started as `bserve <root> <port>`; the client is `bcurl -v host:port/path`. Multiple requests may use the same connection, one request at a time.

All integers are **unsigned big-endian**. The frame header is always 8 bytes:

```text
+----------------+------+-------+----------+
| Length (4 B)   | Type | Flags | ID (2 B) |
+----------------+------+-------+----------+
```

`Length` is payload bytes only. Frames larger than **1 MiB** are rejected. The 4-byte length is deliberately wider than the 1 MiB limit: a 3-byte field would be sufficient for that limit, but 4 bytes gives simple fixed-width parsing and natural integer alignment.

## 2. Frame types, flags and IDs

| Type | Meaning |
|---|---|
| `0x01` | REQUEST |
| `0x02` | RESPONSE |
| `0x03` | DATA |
| `0x04–0xFF` | reserved / unknown |

Only flag `0x01` (`END`) is defined. Senders set undefined flags to zero; receivers ignore undefined flags.

`ID=0` is reserved for a connection-level malformed-frame response. Client request IDs are `1–65535`. The server echoes the request ID. A REQUEST with ID 0 is a `400`.

Unknown frame types **must be consumed and skipped** using their declared length, then parsing continues. This allows future frame types without breaking this implementation.

## 3. Header encoding

Exactly ten names are assigned indexes:

```text
1 :method          6 server
2 :path            7 date
3 :status          8 host
4 content-length   9 user-agent
5 content-type    10 accept
```

Indexed header:

```text
[index:1][value_length:1][value]
```

Literal header:

```text
[FF][name_length:1][name][value_length:1][value]
```

`0xFF` is the literal marker. Index `0` is invalid. Indexes `11–254` are reserved: the receiver consumes their value and skips the header. Header names are lowercase ASCII; values are UTF-8. Each name and value is at most 255 bytes. A literal header may use a static-table name, but senders should use its index. Duplicate headers are rejected except that pseudo-headers are separately checked for their required singleton rules.

## 4. REQUEST

The REQUEST payload is a complete header block. It contains exactly one `:method` and one `:path`. Only `GET` is supported. A normal request carries `END`; because requests have no body in this protocol, a missing END is ignored rather than treated as a request-body continuation.

The path must be non-empty, start with `/`, contain no NUL, `?`, `%`, backslash, or traversal component `..`. Query strings and percent-decoding are unsupported. The resolved path must remain inside the configured root.

Malformed payload, missing/duplicate required headers, duplicate ordinary headers, unsupported method, invalid path, or a known frame type other than REQUEST received by the server produces `400` when the frame boundary is intact.

## 5. RESPONSE

A `200` RESPONSE contains these headers:

```text
:status
content-length
content-type
server
date
```

If the file is non-empty, RESPONSE does **not** carry END; DATA frames follow. If the file is empty, RESPONSE carries END and no DATA frame is sent.

A `404` response is used for a missing file or directory. A `400` response is used for a malformed request. A `500` response is used when the server cannot open/stat a valid requested file before sending the response. Error responses carry END and have no body or `content-length`.

## 6. DATA

DATA payload is raw file bytes. The server normally sends chunks of at most **16 KiB**. Every DATA frame uses the request ID. The final DATA frame carries `END`.

The client sums DATA payload lengths and requires the total to equal `content-length`. An empty file therefore has `content-length: 0` and no DATA frame.

## 7. Errors and connection rules

| Situation | Result |
|---|---|
| Valid GET | `200` + DATA / empty `200` |
| Missing file/directory | `404` + END |
| Malformed request payload | `400` + END; keep connection open |
| REQUEST ID 0 | `400`, ID 0; keep connection open |
| Known wrong frame type | `400`; keep connection open |
| Frame length > 1 MiB | `400`, ID 0 if possible; close |
| Truncated frame | close connection |
| I/O failure after RESPONSE | close connection; client detects truncation |

For an oversized/malformed frame header, the server sends a `400` with ID 0 when possible, shuts down its write side and drains briefly before closing so the peer can receive the error without a TCP reset.

## 8. State machines

**Server:**

```text
CONNECTED → WAIT REQUEST
              │
              ├─ unknown → WAIT REQUEST
              ├─ malformed → 400 → WAIT REQUEST
              ├─ valid 200 empty → WAIT REQUEST
              └─ valid 200 body → DATA* → WAIT REQUEST
```

**Client:**

```text
SEND REQUEST → WAIT RESPONSE
                 │
                 ├─ unknown → WAIT RESPONSE
                 ├─ 400/404 → finish request, exit non-zero
                 └─ 200 → DATA* → finish request
```

A client accepts ID 0 only for a `400` RESPONSE with END; otherwise response/data IDs must match the outstanding request. A DATA frame before RESPONSE or another known frame where DATA is expected is a protocol error.

## 9. Client contract and worked example

`bcurl` writes **only response body bytes to stdout**. With `-v`, complete transmitted and received frame bytes are written to **stderr**. A `4xx` or `5xx` response causes a non-zero exit status.

For `GET /index.html`, the request begins:

```text
00 00 00 47 01 01 00 01
```

meaning length 71, REQUEST, END, ID 1. The successful response begins:

```text
00 00 00 47 02 00 00 01
```

and the body frame begins:

```text
00 00 00 c5 03 01 00 01
```

meaning length 197, DATA, END, ID 1. A complete byte-level annotation is in `examples/annotated-hexdump.md`; design rationale is in `DESIGN-NOTES.md`.
