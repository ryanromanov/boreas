"""`boreas lsp`: the language server reports check errors as diagnostics."""
import io, json, os, subprocess, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.lsp import Server, diagnostics

ROOT = os.path.join(os.path.dirname(__file__), "..")
GOOD = 'program t\n  print "hi"\nend program\n'
BAD = "program t\n  print 1 +\nend program\n"
URI = "file:///tmp/x.bor"


def frame(msg):
    body = json.dumps({"jsonrpc": "2.0", **msg}).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def talk(*msgs, raw=b""):
    """Send messages to an in-process server; return (replies, exit status)."""
    out = io.BytesIO()
    status = Server(io.BytesIO(b"".join(map(frame, msgs)) + raw), out).run()
    data, replies = out.getvalue(), []
    while data:
        head, _, rest = data.partition(b"\r\n\r\n")
        n = int(head.split(b":")[1])
        replies.append(json.loads(rest[:n]))
        data = rest[n:]
    return replies, status


def open_doc(text, uri=URI):
    return {"method": "textDocument/didOpen",
            "params": {"textDocument": {"uri": uri, "languageId": "boreas", "version": 1, "text": text}}}


def change(text, uri=URI):
    return {"method": "textDocument/didChange",
            "params": {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": text}]}}


class DiagnosticsTests(unittest.TestCase):
    def test_clean_source_has_none(self):
        self.assertEqual(diagnostics(GOOD), [])

    def test_error_range_starts_at_the_column_and_runs_to_line_end(self):
        (d,) = diagnostics(BAD)
        self.assertEqual(d["range"], {"start": {"line": 1, "character": 10}, "end": {"line": 1, "character": 11}})
        self.assertEqual((d["severity"], d["source"]), (1, "boreas"))
        self.assertIn("expected an expression", d["message"])

    def test_error_without_a_column_covers_the_line(self):
        (d,) = diagnostics("record r\n  integer :: a\n  integer :: a\nend record\n" + GOOD)
        self.assertEqual(d["range"]["start"], {"line": 2, "character": 0})
        self.assertEqual(d["range"]["end"], {"line": 2, "character": len("  integer :: a")})

    def test_crlf_does_not_leak_into_the_range(self):
        (d,) = diagnostics(BAD.replace("\n", "\r\n"))
        self.assertEqual(d["range"]["end"]["character"], 11)


class ServerTests(unittest.TestCase):
    def test_initialize_advertises_full_sync(self):
        (r,), status = talk({"id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["id"], 1)
        self.assertEqual(r["result"]["capabilities"]["textDocumentSync"], {"openClose": True, "change": 1})

    def test_open_then_fix_then_close(self):
        replies, _ = talk(open_doc(BAD), change(GOOD), {"method": "textDocument/didClose",
                                                        "params": {"textDocument": {"uri": URI}}})
        counts = [len(r["params"]["diagnostics"]) for r in replies]
        self.assertEqual(counts, [1, 0, 0])
        self.assertTrue(all(r["method"] == "textDocument/publishDiagnostics" and r["params"]["uri"] == URI
                            for r in replies))

    def test_documents_are_independent(self):
        replies, _ = talk(open_doc(BAD, "file:///a.bor"), open_doc(GOOD, "file:///b.bor"))
        self.assertEqual([(r["params"]["uri"], len(r["params"]["diagnostics"])) for r in replies],
                         [("file:///a.bor", 1), ("file:///b.bor", 0)])

    def test_unknown_request_gets_an_error_but_unknown_notification_is_ignored(self):
        replies, _ = talk({"id": 7, "method": "textDocument/hover", "params": {}}, {"method": "$/whatever"})
        self.assertEqual(len(replies), 1)
        self.assertEqual((replies[0]["id"], replies[0]["error"]["code"]), (7, -32601))

    def test_exit_status_depends_on_shutdown(self):
        self.assertEqual(talk({"id": 1, "method": "shutdown"}, {"method": "exit"})[1], 0)
        self.assertEqual(talk({"method": "exit"})[1], 1)
        self.assertEqual(talk()[1], 1)               # input ended without exit

    def test_bad_json_is_reported_and_the_server_keeps_going(self):
        replies, _ = talk(raw=b"Content-Length: 3\r\n\r\n{{{" + frame({"id": 2, "method": "initialize"}))
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 2)

    def test_non_ascii_text_survives_framing(self):
        replies, _ = talk(open_doc('program t\n  print "é→"\nend program\n'))
        self.assertEqual(replies[0]["params"]["diagnostics"], [])


class ProcessTests(unittest.TestCase):
    def test_boreas_lsp_command_over_real_pipes(self):
        data = b"".join(map(frame, [{"id": 1, "method": "initialize", "params": {}}, open_doc(BAD),
                                     {"id": 2, "method": "shutdown"}, {"method": "exit"}]))
        p = subprocess.run([sys.executable, "-m", "boreas", "lsp"], input=data, capture_output=True,
                           cwd=ROOT, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count(b"Content-Length"), 3)
        self.assertIn(b"publishDiagnostics", p.stdout)


if __name__ == "__main__":
    unittest.main()
