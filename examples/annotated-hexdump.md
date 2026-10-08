# Annotated Hexdump Examples

These examples are byte-level annotations of frames produced by the implementation. The `date` value changes between runs, so its bytes are shown as an example.

## 1. Normal GET `/index.html`

```text
00 00 00 47 01 01 00 01
```

```text
00 00 00 47  = payload length 71
01           = REQUEST
01           = END
00 01        = ID 1
```

Payload:

```text
01 03 47 45 54
02 0b 2f 69 6e 64 65 78 2e 68 74 6d 6c
08 09 6c 6f 63 61 6c 68 6f 73 74
09 10 62 63 75 72 6c 2d 70 79 74 68 6f 6e 2f 31 2e 30
0a 03 2a 2f 2a
ff 0c 78 2d 72 65 71 75 65 73 74 2d 69 64 04 61 62 63 64
```

```text
01 03 GET                    = :method = GET
02 0b /index.html            = :path = /index.html
08 09 localhost              = host = localhost
09 10 bcurl-python/1.0       = user-agent = bcurl-python/1.0
0a 03 */*                    = accept = */*
ff 0c x-request-id 04 abcd  = literal x-request-id = abcd
```

Successful RESPONSE:

```text
00 00 00 47 02 00 00 01
03 03 32 30 30
04 03 31 39 37
05 09 74 65 78 74 2f 68 74 6d 6c
06 11 62 73 65 72 76 65 2d 70 79 74 68 6f 6e 2f 31 2e 30
07 1d 54 68 75 2c 20 30 38 20 4f 63 74 20 32 30 32 36 20 30 39 3a 32 35 3a 32 32 20 47 4d 54
```

Header:

```text
00 00 00 47 = length 71
02          = RESPONSE
00          = END not set; DATA follows
00 01       = ID 1
```

Payload fields:

```text
03 03 32 30 30              = index 3, length 3, ":status" = "200"
04 03 31 39 37              = index 4, length 3, "content-length" = "197"
05 09 74 65 78 74 2f 68 74 6d 6c
                              = index 5, length 9, "content-type" = "text/html"
06 11 62 73 65 72 76 65 2d 70 79 74 68 6f 6e 2f 31 2e 30
                              = index 6, length 17, "server" = "bserve-python/1.0"
07 1d <29 date bytes>        = index 7, length 0x1d = 29 bytes, "date"
```

`1d` is hexadecimal for decimal **29**.

DATA:

```text
00 00 00 c5 03 01 00 01
```

```text
00 00 00 c5 = length 197
03          = DATA
01          = END
00 01       = ID 1
```

The following 197 bytes are the raw contents of `www/index.html`.

## 2. Unknown `0x99`, then valid request

First frame:

```text
00 00 00 14 99 00 00 01 66 75 74 75 72 65 2d 66
72 61 6d 65 2d 70 61 79 6c 6f 61 64
```

```text
00 00 00 14 = length 20
99          = unknown type
00          = flags 0
00 01       = ID 1
```

The 20-byte payload is consumed and discarded. The connection remains open. The next REQUEST uses ID 2 and is processed normally; the server returns RESPONSE ID 2 followed by DATA ID 2.

This demonstrates **unknown-frame skipping without closing the TCP connection**.

## 3. 404

For `/missing.html`, the response is:

```text
00 00 00 37 02 01 00 01
03 03 34 30 34
06 11 62 73 65 72 76 65 2d 70 79 74 68 6f 6e 2f 31 2e 30
07 1d <29 date bytes>
```

```text
00 00 00 37 = length 55
02          = RESPONSE
01          = END
00 01       = ID 1

03 03 34 30 34 = index 3, length 3, :status = 404
06 11 ...     = index 6, length 17, server = bserve-python/1.0
07 1d ...     = index 7, length 29, date
```

There is no DATA frame. `bcurl` reports the 404 on stderr and exits non-zero.

## 4. Malformed payload → 400 → success on same connection

The first request deliberately sends duplicate `host` headers:

```text
08 09 localhost
08 11 duplicate.example
```

The frame boundary is intact, so the server returns:

```text
00 00 00 37 02 01 00 03
03 03 34 30 30
06 11 <17 server bytes>
07 1d <29 date bytes>
```

```text
ID = 3
:status = 400
END = 1
```

The server **does not close the connection**. A second valid REQUEST with ID 4 follows on the same TCP connection and receives a normal `200` RESPONSE and DATA. This demonstrates recovery after a malformed payload.

## 5. Oversized frame / ID 0

A frame header beginning with:

```text
ff ff ff ff 01 01 00 01
```

declares a payload larger than the 1 MiB limit. The server cannot safely trust the advertised request ID, so it sends, when possible:

```text
RESPONSE: type=0x02, flags=END, ID=0, :status=400
```

It then shuts down its write side, drains briefly, and closes the connection.
