from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass
class Error:
    code: ClassVar[int]
    line: int
    column: int
    msg: str
    filename: Optional[str] = None
    prefix: str = "FURB"

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column + 1} [{self.prefix}{self.code}]: {self.msg}"  # noqa: E501
