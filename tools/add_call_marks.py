"""Adds `@` to the arguments of call statements that an `out` or `inout` parameter changes, in Boreas source
written before call sites were marked. `python3 tools/add_call_marks.py FILE...` rewrites the files in place.
`migrate(text)` does one string and returns (new text, [(old line, new line)])."""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.parser import Call, parse
from boreas.callmarks import all_procedures, bodies, needed, CHANGES
from boreas.effects import statements


SKIP = re.compile(r"(\s|&[ \t]*\n)*")


def split_args(text):
    """Top-level comma-separated pieces of `text`, as (start, end) offsets."""
    parts, depth, start, quote = [], 0, 0, None
    for i, c in enumerate(text):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append((start, i))
            start = i + 1
    parts.append((start, len(text)))
    return parts


def migrate(text):
    unit = parse(text)
    plain, methods = all_procedures(unit)
    lines = text.split("\n")
    edits = []
    for owner, body in bodies(unit):
        for s in statements(body):
            if not isinstance(s, Call):
                continue
            intents = needed(s, owner, plain, methods)
            if intents is None:
                continue
            marks = list(s.marks or [False] * len(s.args))
            recv = False
            if s.receiver is not None:
                recv_need, rest = intents[0] in CHANGES, intents[1:]
            else:
                recv_need, rest = False, intents
            want = [(i in CHANGES) for i in rest[:len(s.args)]]
            want += [False] * (len(s.args) - len(want))
            add_recv = s.receiver is not None and recv_need and not s.receiver_mark
            add_args = [n for n, (w, m) in enumerate(zip(want, marks)) if w and not m]
            if add_recv or add_args:
                edits.append((s.line, add_recv, add_args, s))
    changes = []
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    for line_no, add_recv, add_args, s in sorted(edits, key=lambda e: -e[0]):
        old = lines[line_no - 1]
        at = old.lower().find("call")
        if at < 0:
            raise ValueError(f"line {line_no}: no 'call' on the line: {old.strip()}")
        base = starts[line_no - 1]
        open_at = text.find("(", base + at)
        if open_at < 0:
            raise ValueError(f"line {line_no}: no arguments on the line: {old.strip()}")
        # the arguments end at the matching close parenthesis, which may be on a later line
        depth, end, quote = 0, None, None
        for i in range(open_at, len(text)):
            c = text[i]
            if quote:
                quote = None if c == quote else quote
            elif c in "\"'":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            raise ValueError(f"line {line_no}: the call never closes: {old.strip()}")
        inner = text[open_at + 1:end]
        out, last = [], 0
        for n, (a, b) in enumerate(split_args(inner)):
            out.append(inner[last:a])
            piece = inner[a:b]
            if n in add_args and piece.strip():
                lead = SKIP.match(piece).end()
                piece = piece[:lead] + "@" + piece[lead:]
            out.append(piece)
            last = b
        out.append(inner[last:])
        head = text[base:open_at]
        if add_recv:
            word = head[at:at + 4]
            after = head[at + 4:]
            lead = len(after) - len(after.lstrip())
            head = head[:at + 4] + after[:lead] + "@" + after[lead:]
        new = head + "(" + "".join(out) + text[end:end + 1]
        text = text[:base] + new + text[end + 1:]
        changes.append((old, new.split("\n")[0]))
    lines = text.split("\n")
    return "\n".join(lines), changes


def main(paths):
    for path in paths:
        text = open(path).read()
        try:
            new, changes = migrate(text)
        except Exception as err:
            print(f"{path}: {err}", file=sys.stderr)
            continue
        if changes:
            open(path, "w").write(new)
            print(f"{path}: {len(changes)} call(s) marked")


if __name__ == "__main__":
    main(sys.argv[1:])
