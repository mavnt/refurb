from collections.abc import Callable
from itertools import chain, combinations, starmap

from mypy.nodes import (
    ArgKind,
    AssignmentStmt,
    Block,
    BytesExpr,
    CallExpr,
    ComparisonExpr,
    ComplexExpr,
    DictExpr,
    DictionaryComprehension,
    Expression,
    FloatExpr,
    ForStmt,
    GeneratorExpr,
    IndexExpr,
    IntExpr,
    LambdaExpr,
    ListExpr,
    MemberExpr,
    MypyFile,
    NameExpr,
    Node,
    OpExpr,
    ReturnStmt,
    SetExpr,
    SliceExpr,
    StarExpr,
    Statement,
    StrExpr,
    TupleExpr,
    UnaryExpr,
)

from refurb.error import Error
from refurb.visitor import TraverserVisitor


def extract_binary_oper(oper: str, node: OpExpr) -> tuple[Expression, Expression] | None:
    match node:
        case OpExpr(
            op=op,
            left=lhs,
            right=rhs,
        ) if op == oper:
            match rhs:
                case OpExpr(op=op, left=rhs) if op == oper:
                    return lhs, rhs

                case OpExpr():
                    return None

                case Expression():
                    return lhs, rhs

    return None


def check_block_like(
    func: Callable[[list[Statement], list[Error]], None],
    node: Block | MypyFile,
    errors: list[Error],
) -> None:
    match node:
        case Block():
            func(node.body, errors)

        case MypyFile():
            func(node.defs, errors)


def check_for_loop_like(
    func: Callable[[Node, Node, list[Node], list[Error]], None],
    node: ForStmt | GeneratorExpr | DictionaryComprehension,
    errors: list[Error],
) -> None:
    match node:
        case ForStmt(index=index, expr=expr):
            func(index, expr, [node.body], errors)

        case GeneratorExpr(
            indices=[index],
            sequences=[expr],
            condlists=condlists,
        ):
            func(
                index,
                expr,
                list(chain([node.left_expr], *condlists)),
                errors,
            )

        case DictionaryComprehension(
            indices=[index],
            sequences=[expr],
            condlists=condlists,
        ):
            func(
                index,
                expr,
                list(chain([node.key, node.value], *condlists)),
                errors,
            )


def unmangle_name(name: str | None) -> str:
    return (name or "").rstrip("'*")


def is_equivalent(lhs: Node | None, rhs: Node | None) -> bool:
    match (lhs, rhs):
        case None, None:
            return True

        case NameExpr() as lhs, NameExpr() as rhs:
            return unmangle_name(lhs.fullname) == unmangle_name(rhs.fullname)

        case MemberExpr() as lhs, MemberExpr() as rhs:
            return (
                lhs.name == rhs.name
                and unmangle_name(lhs.fullname) == unmangle_name(rhs.fullname)
                and is_equivalent(lhs.expr, rhs.expr)
            )

        case IndexExpr() as lhs, IndexExpr() as rhs:
            return is_equivalent(lhs.base, rhs.base) and is_equivalent(lhs.index, rhs.index)

        case CallExpr() as lhs, CallExpr() as rhs:
            return (
                is_equivalent(lhs.callee, rhs.callee)
                and all(starmap(is_equivalent, zip(lhs.args, rhs.args)))
                and lhs.arg_kinds == rhs.arg_kinds
                and lhs.arg_names == rhs.arg_names
            )

        case (
            (ListExpr() as lhs, ListExpr() as rhs)
            | (TupleExpr() as lhs, TupleExpr() as rhs)
            | (SetExpr() as lhs, SetExpr() as rhs)
        ):
            return len(lhs.items) == len(rhs.items) and all(  # type: ignore
                starmap(is_equivalent, zip(lhs.items, rhs.items))  # type: ignore
            )

        case DictExpr() as lhs, DictExpr() as rhs:
            return len(lhs.items) == len(rhs.items) and all(
                is_equivalent(lhs_item[0], rhs_item[0]) and is_equivalent(lhs_item[1], rhs_item[1])
                for lhs_item, rhs_item in zip(lhs.items, rhs.items)
            )

        case StarExpr() as lhs, StarExpr() as rhs:
            return is_equivalent(lhs.expr, rhs.expr)

        case UnaryExpr() as lhs, UnaryExpr() as rhs:
            return lhs.op == rhs.op and is_equivalent(lhs.expr, rhs.expr)

        case OpExpr() as lhs, OpExpr() as rhs:
            return (
                lhs.op == rhs.op
                and is_equivalent(lhs.left, rhs.left)
                and is_equivalent(lhs.right, rhs.right)
            )

        case ComparisonExpr() as lhs, ComparisonExpr() as rhs:
            return lhs.operators == rhs.operators and all(
                starmap(is_equivalent, zip(lhs.operands, rhs.operands))
            )

        case SliceExpr() as lhs, SliceExpr() as rhs:
            return (
                is_equivalent(lhs.begin_index, rhs.begin_index)
                and is_equivalent(lhs.end_index, rhs.end_index)
                and is_equivalent(lhs.stride, rhs.stride)
            )

    return str(lhs) == str(rhs)


def get_common_expr_positions(*exprs: Expression) -> tuple[int, int] | None:
    for lhs, rhs in combinations(exprs, 2):
        if is_equivalent(lhs, rhs):
            return exprs.index(lhs), exprs.index(rhs)

    return None


def get_common_expr_in_comparison_chain(
    node: OpExpr, oper: str, cmp_oper: str = "=="
) -> tuple[Expression, tuple[int, int]] | None:
    """
    This function finds the first expression shared between 2 comparison
    expressions in the binary operator `oper`.

    For example, an OpExpr that looks like the following:

    1 == 2 or 3 == 1

    Will return a tuple containing the first common expression (`IntExpr(1)` in
    this case), and the indices of the common expressions as they appear in the
    source (`0` and `3` in this case). The indices are to be used for display
    purposes by the caller.

    If the binary operator is not composed of 2 comparison operators, or if
    there are no common expressions, `None` is returned.
    """

    match extract_binary_oper(oper, node):
        case (
            ComparisonExpr(operators=[lhs_oper], operands=[a, b]),
            ComparisonExpr(operators=[rhs_oper], operands=[c, d]),
        ) if (
            lhs_oper == rhs_oper == cmp_oper and (indices := get_common_expr_positions(a, b, c, d))
        ):
            return a, indices

    return None  # pragma: no cover


class ReadCountVisitor(TraverserVisitor):
    name: NameExpr
    read_count: int

    def __init__(self, name: NameExpr) -> None:
        self.name = name
        self.read_count = 0

    def visit_name_expr(self, node: NameExpr) -> None:
        if node.fullname == self.name.fullname:
            self.read_count += 1

    @property
    def was_read(self) -> int:
        return self.read_count > 0


def is_name_unused_in_contexts(name: NameExpr, contexts: list[Node]) -> bool:
    for ctx in contexts:
        visitor = ReadCountVisitor(name)
        visitor.accept(ctx)

        if visitor.was_read:
            return False

    return True


def normalize_os_path(module: str | None) -> str:
    """
    Mypy turns "os.path" module names into their respective platform, such
    as "ntpath" for windows, "posixpath" if they are POSIX only, or
    "genericpath" if they apply to both (I assume). To make life easier
    for us though, we turn those module names into their original form.
    """

    # Used for compatibility with older versions of Mypy.
    if not module:
        return ""

    segments = module.split(".")

    if segments[0].startswith(("genericpath", "ntpath", "posixpath")):
        return ".".join(["os", "path"] + segments[1:])

    return module


def is_type_none_call(node: Expression) -> bool:
    match node:
        case CallExpr(
            callee=NameExpr(fullname="builtins.type"),
            args=[NameExpr(fullname="builtins.None")],
        ):
            return True

    return False


def stringify(node: Node) -> str:
    try:
        return _stringify(node)

    except ValueError:  # pragma: no cover
        return "x"


def _stringify(node: Node) -> str:
    match node:
        case MemberExpr(expr=expr, name=name):
            return f"{_stringify(expr)}.{name}"

        case NameExpr(name=name):
            return unmangle_name(name)

        case BytesExpr(value=value):
            # TODO: use same formatting as source line
            value = value.replace('"', r"\"")

            return f'b"{value}"'

        case IntExpr(value=value):
            # TODO: use same formatting as source line
            return str(value)

        case ComplexExpr(value=value):
            # TODO: use same formatting as source line
            return str(value)

        case FloatExpr(value=value):
            return str(value)

        case StrExpr(value=value):
            value = value.replace('"', r"\"")

            return f'"{value}"'

        case DictExpr(items=items):
            parts: list[str] = []

            for k, v in items:
                if k:
                    parts.append(f"{stringify(k)}: {stringify(v)}")

                else:
                    parts.append(f"**{stringify(v)}")

            return f"{{{', '.join(parts)}}}"

        case TupleExpr(items=items):
            inner = ", ".join(stringify(x) for x in items)

            if len(items) == 1:
                # single element tuples need a trailing comma
                inner += ","

            return f"({inner})"

        case CallExpr(arg_names=arg_names, arg_kinds=arg_kinds, args=args):
            call_args: list[str] = []

            for arg_name, kind, arg in zip(arg_names, arg_kinds, args):
                if kind == ArgKind.ARG_NAMED:
                    call_args.append(f"{arg_name}={_stringify(arg)}")

                elif kind == ArgKind.ARG_STAR:
                    call_args.append(f"*{_stringify(arg)}")

                elif kind == ArgKind.ARG_STAR2:
                    call_args.append(f"**{_stringify(arg)}")

                else:
                    call_args.append(_stringify(arg))

            return f"{_stringify(node.callee)}({', '.join(call_args)})"

        case IndexExpr(base=base, index=index):
            return f"{stringify(base)}[{stringify(index)}]"

        case SliceExpr(begin_index=begin_index, end_index=end_index, stride=stride):
            begin = stringify(begin_index) if begin_index else ""
            end = stringify(end_index) if end_index else ""
            stride = f":{stringify(stride)}" if stride else ""  # type: ignore[assignment]

            return f"{begin}:{end}{stride}"

        case OpExpr(left=left, op=op, right=right):
            return f"{_stringify(left)} {op} {_stringify(right)}"

        case ComparisonExpr():
            parts = []

            for op, operand in zip(node.operators, node.operands):
                parts.extend((_stringify(operand), op))

            parts.append(_stringify(node.operands[-1]))

            return " ".join(parts)

        case UnaryExpr(op=op, expr=expr):
            if op not in "+-~":
                op += " "

            return f"{op}{_stringify(expr)}"

        case LambdaExpr(
            arg_names=arg_names,
            arg_kinds=arg_kinds,
            body=Block(body=[ReturnStmt(expr=Expression() as expr)]),
        ) if (all(kind == ArgKind.ARG_POS for kind in arg_kinds) and all(arg_names)):
            if arg_names:
                args = " "  # type: ignore
                args += ", ".join(arg_names)  # type: ignore
            else:
                args = ""  # type: ignore

            body = _stringify(expr)

            return f"lambda{args}: {body}"

        case ListExpr(items=items):
            inner = ", ".join(stringify(x) for x in items)

            return f"[{inner}]"

        case SetExpr(items=items):
            inner = ", ".join(stringify(x) for x in items)

            return f"{{{inner}}}"

        # TODO: support multiple lvalues
        case AssignmentStmt(lvalues=[lhs], rvalue=rhs):
            return f"{stringify(lhs)} = {stringify(rhs)}"

    raise ValueError


def slice_expr_to_slice_call(expr: SliceExpr) -> str:
    args = [
        stringify(expr.begin_index) if expr.begin_index else "None",
        stringify(expr.end_index) if expr.end_index else "None",
    ]

    if expr.stride:
        args.append(stringify(expr.stride))

    return f"slice({', '.join(args)})"
