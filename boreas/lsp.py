"""A minimal language server: `boreas lsp` speaks LSP over stdin/stdout and reports the
errors `boreas check` finds as diagnostics, whenever a document is opened or changed."""
import json
import sys

PARSE_ERROR, METHOD_NOT_FOUND, INVALID_REQUEST = -32700, -32601, -32600


def diagnostics(src):
    """The LSP diagnostics for a document: [] when it is clean, else one error. The range
    runs from the error's column to the end of its line, or covers the line if the
    error has no column."""
    from .cli import check_source
    err = check_source(src)
    if err is None:
        return []
    line, col, msg = err
    lines = src.split("\n")
    row = min(max((line or 1) - 1, 0), len(lines) - 1)
    text = lines[row].rstrip("\r")
    start = (col or 1) - 1
    if start >= len(text):                # at the line's end: underline its last character
        start = max(len(text) - 1, 0)
    return [{"range": {"start": {"line": row, "character": start},
                       "end": {"line": row, "character": len(text)}},
             "severity": 1, "source": "boreas", "message": msg}]


class Server:
    def __init__(self, inp, out):
        self.inp, self.out = inp, out
        self.docs = {}                    # uri -> text
        self.shutting_down = False

    # --- transport ---------------------------------------------------------
    def read_message(self):
        """The next JSON message, or None at end of input."""
        length = None
        while True:
            line = self.inp.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                if length is None:
                    continue
                break
            name, _, value = line.partition(b":")
            if name.lower() == b"content-length":
                length = int(value)
        body = self.inp.read(length)
        if len(body) < length:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return {"_invalid": True}

    def send(self, msg):
        body = json.dumps({"jsonrpc": "2.0", **msg}).encode()
        self.out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.out.flush()

    def reply(self, id, result=None):
        self.send({"id": id, "result": result})

    def error(self, id, code, message):
        self.send({"id": id, "error": {"code": code, "message": message}})

    def publish(self, uri):
        self.send({"method": "textDocument/publishDiagnostics",
                   "params": {"uri": uri, "diagnostics": diagnostics(self.docs[uri])
                              if uri in self.docs else []}})

    # --- handlers ----------------------------------------------------------
    def handle(self, msg):
        method, id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if msg.get("_invalid"):
            return self.error(None, PARSE_ERROR, "invalid JSON")
        if method is None:                # a response to us; we send no requests
            return
        if method == "initialize":
            self.reply(id, {"capabilities": {"textDocumentSync": {"openClose": True, "change": 1}},
                            "serverInfo": {"name": "boreas"}})
        elif method == "shutdown":
            self.shutting_down = True
            self.reply(id)
        elif method == "textDocument/didOpen":
            doc = params["textDocument"]
            self.docs[doc["uri"]] = doc["text"]
            self.publish(doc["uri"])
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            changes = params.get("contentChanges") or []
            if changes:                   # full-document sync: the last change is the whole text
                self.docs[uri] = changes[-1]["text"]
                self.publish(uri)
        elif method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.docs.pop(uri, None)
            self.publish(uri)             # clears the squiggles
        elif id is not None:              # a request we do not support
            self.error(id, METHOD_NOT_FOUND, f"method not supported: {method}")

    def run(self):
        """Serve until `exit` or end of input. Returns the process exit status."""
        while True:
            msg = self.read_message()
            if msg is None:
                return 1
            if msg.get("method") == "exit":
                return 0 if self.shutting_down else 1
            self.handle(msg)


def serve():
    sys.exit(Server(sys.stdin.buffer, sys.stdout.buffer).run())
