"""Turns `reshape([1, 4, 2, 5, 3, 6], [2, 3])` (and the same with `transpose` for Fortran's `order=[2, 1]`) into
the matrix literal `[1, 2, 3; 4, 5, 6]`, for `boreas convert`, when the data and the shape are plain values."""
from dataclasses import fields, is_dataclass
from .parser import Apply, ArrayLit, Gen, MatrixLit, Num


def plain_shape(e):
    """(rows, cols) of a literal shape `[r, c]` of two whole numbers, or None."""
    if isinstance(e, ArrayLit) and len(e.items) == 2 and all(
            isinstance(x, Num) and isinstance(x.value, int) and not isinstance(x.value, bool) and x.value > 0
            for x in e.items):
        return e.items[0].value, e.items[1].value
    return None


def plain_items(e, count):
    """The items of a literal list of `count` single values (no nested lists, no generators), or None."""
    if isinstance(e, ArrayLit) and len(e.items) == count and not any(
            isinstance(x, (ArrayLit, MatrixLit, Gen)) for x in e.items):
        return e.items
    return None


def as_matrix(e):
    """`e` as a MatrixLit when it is a reshape (or a transposed reshape) of plain data to a plain shape, else None."""
    if isinstance(e, Apply) and e.name == "reshape" and len(e.args) == 2:
        shape = plain_shape(e.args[1])
        items = plain_items(e.args[0], shape[0] * shape[1]) if shape else None
        if items is not None:
            rows, cols = shape                                  # the data runs down the columns
            return MatrixLit([[items[j * rows + i] for j in range(cols)] for i in range(rows)], e.line)
    if isinstance(e, Apply) and e.name == "transpose" and len(e.args) == 1:
        inner = e.args[0]                                       # reshape(data, [c, r]) flipped: the data runs along the rows
        if isinstance(inner, Apply) and inner.name == "reshape" and len(inner.args) == 2:
            shape = plain_shape(inner.args[1])
            items = plain_items(inner.args[0], shape[0] * shape[1]) if shape else None
            if items is not None:
                cols, rows = shape
                return MatrixLit([[items[i * cols + j] for j in range(cols)] for i in range(rows)], e.line)
    return None


def matrixify(node):
    """Rewrite every such call inside `node` (a unit, a statement, an expression, a list or a dict)."""
    if isinstance(node, list):
        node[:] = [matrixify(x) for x in node]                         # in place: the lists are shared with their owners
        return node
    if isinstance(node, tuple):
        return tuple(matrixify(x) for x in node)
    if isinstance(node, dict):
        for k, v in list(node.items()):
            node[k] = matrixify(v)
        return node
    if is_dataclass(node) and not isinstance(node, type):
        if type(node).__dataclass_params__.frozen:                      # types such as ProcType hold no expressions
            return node
        if isinstance(node, Apply) and node.name == "transpose":      # before its reshape inside becomes a literal
            flipped = as_matrix(node)
            if flipped is not None:
                return matrixify(flipped)
        for f in fields(node):
            old = getattr(node, f.name)
            new = matrixify(old)
            if new is not old:
                setattr(node, f.name, new)
        return as_matrix(node) or node
    return node
