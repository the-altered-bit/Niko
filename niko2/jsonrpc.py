"""Minimal JSON-RPC framing over stdio, shared by the LSP language server
(`niko2/lsp.py`) and the DAP debug adapter (`niko2/dap.py`).

Both protocols send one JSON object per message, prefixed with
`Content-Length: N` headers. This module only frames bytes; each server
implements its own dispatch.
"""
import json


def read_message(stream):
    """Read one framed message from a binary stream. Returns the decoded
    object, or None on EOF."""
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if b':' in line:
            k, v = line.split(b':', 1)
            headers[k.strip().lower()] = v.strip()
    try:
        length = int(headers.get(b'content-length', 0))
    except ValueError:
        return None
    if not length:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode('utf-8'))


def write_message(stream, obj):
    """Write one framed message to a binary stream."""
    body = json.dumps(obj).encode('utf-8')
    stream.write(b'Content-Length: ' + str(len(body)).encode('ascii') + b'\r\n\r\n')
    stream.write(body)
    stream.flush()
