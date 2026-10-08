# HTTP, in Binary — Python

A Windows-friendly implementation of the custom binary HTTP protocol.

## Requirements

- Python 3.10+ recommended
- No WSL required
- No third-party packages

## Project structure

```text
binary_http/
├── bserve.py
├── bcurl.py
├── protocol.py
├── test_unknown.py
├── test_malformed.py
├── PROTOCOL.md
├── DESIGN-NOTES.md
├── examples/
│   └── annotated-hexdump.md
└── www/
    ├── index.html
    └── hello.txt
```

## Protocol summary

Every frame has an 8-byte big-endian header:

| Field | Bytes |
|---|---:|
| Length | 4 |
| Type | 1 |
| Flags | 1 |
| ID | 2 |

`Length` counts only the payload.

Frame types:

- `0x01` REQUEST
- `0x02` RESPONSE
- `0x03` DATA
- `0x04–0xFF` reserved/unknown

Flag:

- `0x01` END

Maximum received frame size: 1 MiB.

Normal DATA chunks: 16 KiB.

The TCP connection is persistent. Multiple requests can use the same connection.

## Header encoding

Ten common header names have indexes:

1. `:method`
2. `:path`
3. `:status`
4. `content-length`
5. `content-type`
6. `server`
7. `date`
8. `host`
9. `user-agent`
10. `accept`

Indexed form:

```text
[index:1][value_length:1][value]
```

Literal form:

```text
[FF][name_length:1][name][value_length:1][value]
```

`0xFF` is the literal escape.

Header names are lowercase. Header values are UTF-8. Name/value lengths are one byte.

## Run the server

Open PowerShell in this directory:

```powershell
python .\bserve.py .\www 9000
```

Expected:

```text
bserve listening on 0.0.0.0:9000, root=...
```

## Run the client

Open a second PowerShell:

```powershell
python .\bcurl.py localhost:9000/index.html
```

Verbose mode:

```powershell
python .\bcurl.py -v localhost:9000/index.html
```

`-v` prints complete transmitted/received frames to stderr. The response body remains on stdout.

## Multiple requests on one TCP connection

```powershell
python .\bcurl.py localhost:9000/index.html localhost:9000/hello.txt
```

The client creates one TCP connection and sends both requests through it.

## 404 test

```powershell
python .\bcurl.py -v localhost:9000/missing.html
echo $LASTEXITCODE
```

The client exits non-zero for 4xx/5xx responses.

## Unknown frame test

With the server running:

```powershell
python .\test_unknown.py 9000
```

The test sends type `0x99`, which the server must skip, followed by a valid request on the same TCP connection.

## Important implementation points

- `recv()` may return fewer bytes than requested, so `read_exact()` loops until the requested amount is received.
- Unknown frame types are skipped using their declared length.
- A malformed payload with an intact frame boundary receives `400` and keeps the connection open.
- An invalid/oversized frame header receives `400` with ID 0 and the connection is closed.
- Successful responses use `content-length` plus DATA frames.
- The final DATA frame carries END.
- An empty file uses RESPONSE+END and sends no empty DATA frame.
- Path resolution rejects traversal outside the configured root.

## Malformed-request recovery test

```powershell
python .\test_malformed.py 9000
```

The test sends a malformed request, verifies `400`, then sends a valid request on the same TCP connection and verifies `200`.

## Client unknown-frame test

```powershell
python .\test_client_unknown.py
```

This starts a small local fake server, sends an unknown frame before the RESPONSE, and verifies that `bcurl` skips it and still receives the body.
