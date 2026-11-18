"""One place that knows how the syntax tree is shaped, so a pass that walks it does not need its own list of node
kinds. A node is a dataclass; its children are the dataclass values in its fields, looked for inside lists, tuples
and dicts. (Types such as `MapType` are frozen dataclasses and are not part of the tree.)

Conventions a new statement should follow, so that every pass sees inside it without being edited:
  * its sub-statements are in a list field called `body` or `orelse`, in `branches` (a list of `(condition, body)`),
    or in `cases` (a list of nodes that each have a `body`);
  * every node class is listed in STATEMENTS, EXPRESSIONS or STRUCTURE below (a test fails otherwise)."""
from dataclasses import fields, is_dataclass
from . import parser as P

STATEMENTS = [P.Decl, P.Assign, P.If, P.Do, P.DoEach, P.DoWhile, P.Where, P.Select, P.SelectType, P.Break,
              P.Print, P.Close, P.Stop, P.Return, P.ReturnMany, P.MultiAssign, P.Exit, P.Cycle, P.ExprStmt,
              P.Call, P.Allocate, P.Deallocate, P.Comment]
EXPRESSIONS = [P.Num, P.Str, P.Bool, P.Var, P.Null, P.ArrayLit, P.MatrixLit, P.Marked, P.CollLit, P.Gen, P.Slice,
               P.Apply, P.Unary, P.Binary, P.Arm, P.SelectExpr, P.FieldAccess, P.NamedArg, P.IsType]
STRUCTURE = [P.RecordDef, P.InterfaceDef, P.Case, P.Function, P.Program, P.Use, P.Module, P.Unit]


def register(kind, cls):
    """Another module's node class (the converter's Goto, for one): `kind` is STATEMENTS, EXPRESSIONS or STRUCTURE."""
    if cls not in kind:
        kind.append(cls)


def is_node(x):
    return is_dataclass(x) and not isinstance(x, type) and not type(x).__dataclass_params__.frozen


def _inside(value):
    if is_node(value):
        yield value
    elif isinstance(value, (list, tuple)):
        for x in value:
            yield from _inside(x)
    elif isinstance(value, dict):
        for x in value.values():
            yield from _inside(x)


def children(node):
    """The nodes directly inside `node`, in the order its fields list them."""
    if is_node(node):
        for f in fields(node):
            yield from _inside(getattr(node, f.name))


def walk(node):
    """`node` (a node, or a list of them) and every node inside it, outer ones first."""
    for n in _inside(node):
        yield n
        for c in children(n):
            yield from walk(c)


def statements_in(body):
    """Every statement in `body`, the ones nested inside others included."""
    return [n for n in walk(body) if isinstance(n, tuple(STATEMENTS))]


def bodies(s):
    """The statement lists nested directly inside the statement `s`."""
    out = []
    for f in fields(s) if is_node(s) else ():
        v = getattr(s, f.name)
        if f.name in ("body", "orelse") and isinstance(v, list):
            out.append(v)
        elif f.name == "branches":
            out += [b for _, b in v]
        elif f.name == "cases":
            out += [c.body for c in v]
    return out


def rewrite(node, fn, first=None):
    """Replace each node inside `node` (a node, list, tuple or dict) by `fn(node)`, children first; the nodes are
    changed in place and the replacement for `node` itself is returned. `first(node)`, when given, is tried on a
    node before its children are visited: a result other than None replaces the node (and is rewritten in turn)."""
    if isinstance(node, list):
        node[:] = [rewrite(x, fn, first) for x in node]
        return node
    if isinstance(node, tuple):
        return tuple(rewrite(x, fn, first) for x in node)
    if isinstance(node, dict):
        for k, v in list(node.items()):
            node[k] = rewrite(v, fn, first)
        return node
    if is_node(node):
        if first is not None:
            early = first(node)
            if early is not None:
                return rewrite(early, fn, first)
        for f in fields(node):
            old = getattr(node, f.name)
            new = rewrite(old, fn, first)
            if new is not old:
                setattr(node, f.name, new)
        return fn(node)
    return node
