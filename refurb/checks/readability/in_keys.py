from dataclasses import dataclass

from mypy.nodes import CallExpr, ComparisonExpr, MemberExpr, NameExpr, Var

from refurb.checks.common import stringify
from refurb.error import Error


@dataclass
class ErrorInfo(Error):
    """
    If you only want to check if a key exists in a dictionary, you don't need
    to call `.keys()` first, just use `in` on the dictionary itself:

    Bad:

    ```
    d = {"key": "value"}

    if "key" in d.keys():
        ...
    ```

    Good:

    ```
    d = {"key": "value"}

    if "key" in d:
        ...
    ```
    """

    name = "no-in-dict-keys"
    code = 130
    categories = ("dict", "readability")


def check(node: ComparisonExpr, errors: list[Error]) -> None:
    match node:
        case ComparisonExpr(
            operators=["in" | "not in" as oper],
            operands=[
                _,
                CallExpr(
                    callee=MemberExpr(
                        expr=NameExpr(node=Var(type=ty)) as obj,
                        name="keys",
                    ),
                ) as expr,
            ],
        ) if str(ty).startswith("builtins.dict"):
            obj_name = stringify(obj)

            msg = f"Replace `{oper} {obj_name}.keys()` with `{oper} {obj_name}`"

            errors.append(ErrorInfo.from_node(expr, msg))
