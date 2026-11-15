"""Adds `@` to the arguments of call statements that an `out` or `inout` parameter changes, in Boreas source
written before call sites were marked. `python3 tools/add_call_marks.py FILE...` rewrites the files in place.
`migrate(text)` does one string and returns (new text, [(old line, new line)])."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.parser import Call, parse
from boreas.callmarks import all_procedures, bodies, needed, CHANGES
from boreas.effects import statements


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
    for line_no, add_recv, add_args, s in sorted(edits, key=lambda e: -e[0]):
        old = lines[line_no - 1]
        low = old.lower()
        at = low.find("call")
        if at < 0:
            raise ValueError(f"line {line_no}: no 'call' on the line: {old.strip()}")
        open_at = old.find("(", at)
        if open_at < 0:
            raise ValueError(f"line {line_no}: no arguments on the line: {old.strip()}")
        # the arguments end at the matching close parenthesis
        depth, end = 0, None
        for i in range(open_at, len(old)):
            if old[i] == "(":
                depth += 1
            elif old[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            raise ValueError(f"line {line_no}: the call continues on the next line: {old.strip()}")
        inner = old[open_at + 1:end]
        pieces = split_args(inner)
        out, last = [], 0
        for n, (a, b) in enumerate(pieces):
            out.append(inner[last:a])
            piece = inner[a:b]
            if n in add_args and inner[a:b].strip():
                lead = len(piece) - len(piece.lstrip())
                piece = piece[:lead] + "@" + piece[lead:]
            out.append(piece)
            last = b
        out.append(inner[last:])
        head = old[:open_at]
        if add_recv:
            word = head[at:at + 4]
            after = head[at + 4:]
            lead = len(after) - len(after.lstrip())
            head = head[:at + 4] + after[:lead] + "@" + after[lead:]
        new = head + "(" + "".join(out) + old[end:]
        lines[line_no - 1] = new
        changes.append((old, new))
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
