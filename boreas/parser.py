from dataclasses import dataclass, field
from .lexer import lex, LangError

TYPES = {"integer", "real", "logical", "string"}


# --- AST ---------------------------------------------------------------
@dataclass
class Num:  value: object
@dataclass
class Str:  value: str
@dataclass
class Bool: value: bool
@dataclass
class Var:  name: str
@dataclass
class ArrayLit: items: list
@dataclass
class Slice: lo: object; hi: object
@dataclass
class Apply: name: str; args: list; line: int   # index, slice, or call
@dataclass
class Unary: op: str; operand: object
@dataclass
class Binary: op: str; left: object; right: object

@dataclass
class Decl:   # dims: list of exprs or None
    type: str; name: str; dims: object; init: object; line: int; const: bool = False
    public: bool = False       # only meaningful for a module's constants
@dataclass
class Assign: target: object; value: object; line: int; update: bool = False
@dataclass
class If: branches: list; orelse: list   # branches: [(cond, body)]
@dataclass
class Do:
    var: str; lo: object; hi: object; step: object; body: list; line: int
    label: str = None          # `outer: do ...` / `end do outer`
@dataclass
class DoWhile: cond: object; body: list; label: str = None; line: int = 0
@dataclass
class Print: args: list
@dataclass
class Return: value: object
@dataclass
class Exit: label: str = None; line: int = 0      # label names an enclosing loop
@dataclass
class Cycle: label: str = None; line: int = 0
@dataclass
class ExprStmt: expr: object
@dataclass
class Call: name: str; args: list; line: int
@dataclass
class Comment: text: str          # only produced by `boreas convert`; the interpreter skips it

@dataclass
class Function:
    """A function or, when is_sub is set, a subroutine."""
    name: str
    params: list          # [(type, name, intent)], intent in {"in", "out", "inout"}
    ret: str | None
    body: list
    is_sub: bool = False
    comments: list = field(default_factory=list)   # printed above the header by `convert`
    uses: list = field(default_factory=list)
    public: bool = False       # only meaningful inside a module
@dataclass
class Program:
    name: str
    body: list
    comments: list = field(default_factory=list)
    uses: list = field(default_factory=list)
@dataclass
class Use: module: str; names: list; line: int
@dataclass
class Module:
    name: str
    uses: list
    consts: list          # [Decl], all const
    functions: dict       # name -> Function
    line: int
    comments: list = field(default_factory=list)   # printed above the header by `convert`
@dataclass
class Unit:
    functions: dict       # top-level procedures (outside any module)
    program: Program      # None for a library-only file
    modules: dict = field(default_factory=dict)


def check_writes(body, params):
    """`update` marks exactly the writes that reach the caller: assignments to
    out/inout parameters. `params` maps parameter name -> intent."""
    for s in body:
        if isinstance(s, list):
            check_writes(s, params)
        elif isinstance(s, Assign):
            name = s.target.name
            escapes = params.get(name) in ("out", "inout")
            if escapes and not s.update:
                raise LangError(f"line {s.line}: '{name}' is an {params[name]} parameter, so the caller sees "
                                f"this write; write 'update {name} = ...'")
            if s.update and not escapes:
                what = "an 'in' parameter" if params.get(name) == "in" else "a local variable"
                raise LangError(f"line {s.line}: 'update' is for out and inout parameters, but "
                                f"'{name}' is {what}")
        elif isinstance(s, If):
            for _, b in s.branches:
                check_writes(b, params)
            check_writes(s.orelse, params)
        elif isinstance(s, Do):
            if s.var in params:
                raise LangError(f"line {s.line}: loop variable '{s.var}' cannot be a parameter")
            check_writes(s.body, params)
        elif isinstance(s, DoWhile):
            check_writes(s.body, params)


def sub_bodies(s):
    """The statement lists nested directly inside `s`."""
    if isinstance(s, If):
        return [b for _, b in s.branches] + [s.orelse]
    if isinstance(s, (Do, DoWhile)):
        return [s.body]
    return []


def check_loops(body, enclosing=()):
    """`exit L` / `cycle L` must name an enclosing loop, and a label cannot be
    reused by a loop inside the loop that already carries it."""
    for s in body:
        if isinstance(s, list):
            check_loops(s, enclosing)
        elif isinstance(s, (Exit, Cycle)) and s.label is not None and s.label not in enclosing:
            word = "exit" if isinstance(s, Exit) else "cycle"
            if enclosing:
                known = "enclosing loops are " + ", ".join(f"'{l}'" for l in enclosing)
            else:
                known = "this is not inside a labeled loop"
            raise LangError(f"line {s.line}: '{word} {s.label}' does not name an enclosing loop ({known})")
        else:
            inner = enclosing
            if isinstance(s, (Do, DoWhile)) and s.label is not None:
                if s.label in enclosing:
                    raise LangError(f"line {s.line}: loop label '{s.label}' is already "
                                    f"used by an enclosing loop")
                inner = enclosing + (s.label,)
            for b in sub_bodies(s):
                check_loops(b, inner)


# --- Parser ------------------------------------------------------------
class Parser:
    def __init__(self, src):
        self.toks, self.i = lex(src), 0

    @property
    def tok(self):
        return self.toks[self.i]

    def error(self, msg):
        raise LangError(f"line {self.tok.line}: {msg} (at {self.tok.text!r})")

    def next(self):
        t = self.tok
        self.i += 1
        return t

    def at(self, *texts):
        return self.tok.kind in ("ident", "op") and self.tok.text in texts

    def accept(self, text):
        if self.at(text):
            return self.next()

    def expect(self, text):
        if not self.at(text):
            self.error(f"expected {text!r}")
        return self.next()

    def ident(self):
        if self.tok.kind != "ident":
            self.error("expected a name")
        return self.next().text

    def newline(self):
        if self.tok.kind not in ("nl", "eof"):
            self.error("expected end of line")
        while self.tok.kind == "nl":
            self.next()

    def skip_newlines(self):
        while self.tok.kind == "nl":
            self.next()

    # -- top level
    def parse(self):
        funcs, mods, prog = {}, {}, None
        self.skip_newlines()
        while self.tok.kind != "eof":
            if self.at("public", "private"):
                self.error("'public' is only for the contents of a module")
            if self.at("function", "subroutine"):
                f = self.procedure()
                if f.name in funcs:
                    raise LangError(f"'{f.name}' is defined more than once")
                funcs[f.name] = f
            elif self.at("module"):
                m = self.module()
                if m.name in mods:
                    raise LangError(f"line {m.line}: module '{m.name}' is defined more than once")
                mods[m.name] = m
            elif self.at("program"):
                if prog:
                    self.error("only one program allowed")
                prog = self.program()
            else:
                self.error("expected 'module', 'function', 'subroutine' or 'program'")
            self.skip_newlines()
        if not prog:
            raise LangError("no program found")
        return Unit(funcs, prog, mods)

    def program(self):
        self.expect("program")
        name = self.ident()
        self.newline()
        uses = self.uses()
        body = self.block("program")
        self.expect("program")
        if self.tok.kind == "ident":
            self.next()
        check_writes(body, {})
        check_loops(body)
        return Program(name, body, uses=uses)

    def uses(self):
        """`use geometry: area, pi` lines at the top of a module, procedure or program."""
        uses = []
        self.skip_newlines()
        while self.at("use"):
            line = self.next().line
            module = self.ident()
            self.expect(":")
            names = [self.ident()]
            while self.accept(","):
                names.append(self.ident())
            self.newline()
            uses.append(Use(module, names, line))
        return uses

    def module(self):
        line = self.expect("module").line
        name = self.ident()
        self.newline()
        uses = self.uses()
        consts, funcs = [], {}
        taken = {}                                     # name -> line, across constants and procedures

        def claim(n, at):
            if n in taken:
                raise LangError(f"line {at}: '{n}' is defined more than once in module '{name}'")
            taken[n] = at

        while not (self.at("end") and self.toks[self.i + 1].text == "module"):
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end module'")
            if self.at("private"):
                self.error("names in a module are private unless marked 'public'")
            public = bool(self.accept("public"))
            if self.at("const"):
                self.next()
                d = self.decl(const=True)
                for x in (d if isinstance(d, list) else [d]):
                    x.public = public
                    claim(x.name, x.line)
                    consts.append(x)
            elif self.at("function", "subroutine"):
                at = self.tok.line
                f = self.procedure()
                f.public = public
                claim(f.name, at)
                funcs[f.name] = f
            elif self.tok.kind == "ident" and self.tok.text in TYPES:
                self.error("a module holds constants and procedures; declare this with 'const'")
            else:
                self.error("expected 'const', 'function', 'subroutine' or 'end module'")
            self.skip_newlines()
        self.expect("end")
        self.expect("module")
        if self.tok.kind == "ident":
            self.next()
        return Module(name, uses, consts, funcs, line)

    def procedure(self):
        kind = self.next().text           # "function" or "subroutine"
        is_sub = kind == "subroutine"
        name = self.ident()
        params = self.params(is_sub)
        ret = None
        if not is_sub and self.accept("->"):
            ret = self.type_name()
        self.newline()
        uses = self.uses()
        body = self.block(kind)
        self.expect(kind)
        if self.tok.kind == "ident":
            self.next()
        check_writes(body, {n: intent for _, n, intent in params})
        check_loops(body)
        return Function(name, params, ret, body, is_sub, uses=uses)

    def params(self, is_sub):
        """`(real :: x, inout integer :: n)`; the mode defaults to `in`."""
        self.expect("(")
        params = []
        if not self.at(")"):
            while True:
                intent = "in"
                if self.at("in", "out", "inout"):
                    intent = self.next().text
                    if intent != "in" and not is_sub:
                        self.error("only subroutines may have out or inout parameters")
                t = self.type_name()
                self.expect("::")
                params.append((t, self.ident(), intent))
                if not self.accept(","):
                    break
        self.expect(")")
        return params

    def type_name(self):
        if self.tok.kind != "ident" or self.tok.text not in TYPES:
            self.error("expected a type (integer, real, logical, string)")
        return self.next().text

    # -- statements
    def block(self, *closers):
        """Parse statements until `end <closer>` / else / etc. Leaves 'end' consumed
        only when followed by one of `closers`."""
        body = []
        self.skip_newlines()
        while True:
            if self.at("end") and self.toks[self.i + 1].text in closers:
                self.next()
                return body
            if self.at("else") and "if" in closers:
                return body
            if self.tok.kind == "eof":
                self.error(f"unexpected end of file, missing 'end {closers[0]}'")
            body.append(self.statement())
            self.skip_newlines()

    def statement(self):
        t = self.tok
        if t.kind == "ident" and t.text == "const":
            self.next()
            return self.decl(const=True)
        if t.kind == "ident" and t.text in TYPES:
            return self.decl()
        if self.at("if"):
            return self.if_stmt()
        if self.at("do"):
            return self.do_stmt()
        if self.at("print"):
            self.next()
            args = [self.expr()]
            while self.accept(","):
                args.append(self.expr())
            self.newline()
            return Print(args)
        if self.at("call"):
            self.next()
            name = self.ident()
            args = []
            if self.accept("("):
                if not self.at(")"):
                    args.append(self.arg())
                    while self.accept(","):
                        args.append(self.arg())
                self.expect(")")
            self.newline()
            return Call(name, args, t.line)
        if self.at("return"):
            self.next()
            val = None if self.tok.kind == "nl" else self.expr()
            self.newline()
            return Return(val)
        if self.at("exit", "cycle"):
            word = self.next().text
            label = self.ident() if self.tok.kind == "ident" else None
            self.newline()
            return (Exit if word == "exit" else Cycle)(label, t.line)
        if t.kind == "ident" and self.toks[self.i + 1].text == ":" and self.toks[self.i + 2].text == "do":
            return self.do_stmt()                  # `outer: do ...`
        update = bool(self.accept("update"))
        e = self.expr()
        if update or self.at("="):
            if not isinstance(e, (Var, Apply)):
                self.error("cannot assign to this expression")
            self.expect("=")
            v = self.expr()
            self.newline()
            return Assign(e, v, t.line, update)
        self.newline()
        return ExprStmt(e)

    def decl(self, const=False):
        line = self.tok.line
        typ = self.type_name()
        self.expect("::")
        decls = []
        while True:
            name = self.ident()
            dims = None
            if self.accept("("):
                dims = [self.expr()]
                while self.accept(","):
                    dims.append(self.expr())
                self.expect(")")
            init = self.expr() if self.accept("=") else None
            if const and init is None:
                self.error(f"constant '{name}' needs a value")
            decls.append(Decl(typ, name, dims, init, line, const))
            if not self.accept(","):
                break
        self.newline()
        return decls if len(decls) > 1 else decls[0]

    def if_stmt(self):
        self.expect("if")
        branches, orelse = [], []
        cond = self.expr()
        self.expect("then")
        self.newline()
        branches.append((cond, self.block("if")))
        # block() stops at `else` (unconsumed) or after consuming `end`
        while self.at("else"):
            self.expect("else")
            if self.accept("if"):
                cond = self.expr()
                self.expect("then")
                self.newline()
                branches.append((cond, self.block("if")))
            else:
                self.newline()
                orelse = self.block("if")
        self.expect("if")
        self.newline()
        return If(branches, orelse)

    def do_stmt(self):
        label = None
        if self.tok.kind == "ident" and self.toks[self.i + 1].text == ":":
            label = self.next().text
            if label in ("end", "else"):
                self.error(f"'{label}' cannot be used as a loop label")
            self.next()                            # the ':'
        line = self.expect("do").line
        if self.accept("while"):
            cond = self.expr()
            self.newline()
            body = self.block("do")
            self.expect("do")
            self.end_label(label)
            return DoWhile(cond, body, label, line)
        var = self.ident()
        self.expect("=")
        lo = self.expr()
        self.expect(",")
        hi = self.expr()
        step = self.expr() if self.accept(",") else None
        self.newline()
        body = self.block("do")
        self.expect("do")
        self.end_label(label)
        return Do(var, lo, hi, step, body, line, label)

    def end_label(self, label):
        """After `end do`: a labeled loop must repeat its label, an unlabeled one must not have one."""
        if label is not None:
            if self.tok.kind != "ident" or self.tok.text != label:
                self.error(f"loop '{label}' must close with 'end do {label}'")
            self.next()
        elif self.tok.kind == "ident":
            self.error("this loop has no label, so it closes with plain 'end do'")
        self.newline()

    # -- expressions (lowest to highest precedence)
    def expr(self):
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.accept("or"):
            e = Binary("or", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.accept("and"):
            e = Binary("and", e, self.not_())
        return e

    def not_(self):
        if self.accept("not"):
            return Unary("not", self.not_())
        return self.compare()

    def compare(self):
        e = self.add()
        while self.at("==", "/=", "<", "<=", ">", ">="):
            op = self.next().text
            e = Binary(op, e, self.add())
        return e

    def add(self):
        e = self.mul()
        while self.at("+", "-"):
            op = self.next().text
            e = Binary(op, e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.at("*", "/"):
            op = self.next().text
            e = Binary(op, e, self.unary())
        return e

    def unary(self):
        if self.at("-", "+"):
            op = self.next().text
            return Unary(op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.accept("**"):
            return Binary("**", base, self.unary())  # right-associative
        return base

    def primary(self):
        t = self.tok
        if t.kind == "int":
            self.next(); return Num(int(t.text))
        if t.kind == "real":
            self.next(); return Num(float(t.text))
        if t.kind == "str":
            self.next(); return Str(t.text[1:-1])
        if self.accept("true"):
            return Bool(True)
        if self.accept("false"):
            return Bool(False)
        if self.accept("("):
            e = self.expr()
            self.expect(")")
            return e
        if self.accept("["):
            items = []
            if not self.at("]"):
                items.append(self.expr())
                while self.accept(","):
                    items.append(self.expr())
            self.expect("]")
            return ArrayLit(items)
        if t.kind == "ident":
            name = self.next().text
            if self.accept("("):
                args = []
                if not self.at(")"):
                    while True:
                        args.append(self.arg())
                        if not self.accept(","):
                            break
                self.expect(")")
                return Apply(name, args, t.line)
            return Var(name)
        self.error("expected an expression")

    def arg(self):
        lo = None if self.at(":") else self.expr()
        if self.accept(":"):
            hi = None if self.at(",", ")") else self.expr()
            return Slice(lo, hi)
        return lo


def parse(src):
    return Parser(src).parse()
