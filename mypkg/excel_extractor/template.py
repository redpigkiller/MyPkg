"""Template AST node definitions.

Users build a Block by composing Row/Col/EmptyRow/EmptyCol/Group nodes.
These are pure data-holder classes; no matching logic lives here.

Repeat spec
-----------
repeat = 1          → exactly once
repeat = "?"        → 0 or 1
repeat = "+"        → 1 or more (greedy)
repeat = "*"        → 0 or more (greedy)
repeat = (2, 4)     → between 2 and 4 times (inclusive)
repeat = (N, None)  → N or more
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

from mypkg.excel_extractor.types import CellCondition, Types, _literal

RepeatSpec = int | str | tuple[int, int | None]


def _parse_repeat(repeat: RepeatSpec) -> tuple[int, int | None]:
    """Normalise any repeat spec to (min, max).

    max = None means unbounded.
    """
    if isinstance(repeat, int):
        if repeat < 0:
            raise ValueError(f"repeat must be non-negative, got {repeat}")
        return (repeat, repeat)
    if isinstance(repeat, str):
        table = {"?": (0, 1), "+": (1, None), "*": (0, None)}
        if repeat not in table:
            raise ValueError(f"repeat string must be '?', '+' or '*', got {repeat!r}")
        return table[repeat]
    if isinstance(repeat, tuple):
        if len(repeat) != 2:
            raise ValueError("repeat tuple must be (min, max) where max may be None")
        lo, hi = repeat
        if not isinstance(lo, int) or lo < 0:
            raise ValueError(f"repeat min must be a non-negative int, got {lo!r}")
        if hi is not None and (not isinstance(hi, int) or hi < lo):
            raise ValueError(f"repeat max must be None or an int >= min, got {hi!r}")
        return (lo, hi)
    raise TypeError(f"Unsupported repeat spec: {repeat!r}")


def _normalise_pattern(pattern: list[Any]) -> list[CellCondition]:
    """Convert each element of a pattern list to a CellCondition.

    str  → literal match
    CellCondition → pass-through
    """
    result = []
    for item in pattern:
        if isinstance(item, CellCondition):
            result.append(item)
        elif isinstance(item, str):
            result.append(_literal(item))
        else:
            raise TypeError(f"Pattern elements must be str or CellCondition, got {type(item)}")
    return result


class TemplateNode:
    """Abstract base for all template AST nodes."""


@dataclass
class Row(TemplateNode):
    """Horizontal pattern: matches one or more Excel rows.

    Parameters
    ----------
    pattern : list of str (literal) or CellCondition
    repeat  : how many times this row pattern must appear
    node_id : optional label surfaced in NodeResult
    """
    pattern: list[CellCondition] = field(default_factory=list)
    repeat: RepeatSpec = 1
    node_id: str | None = None

    def __post_init__(self):
        self.pattern = _normalise_pattern(self.pattern)
        self._repeat_range = _parse_repeat(self.repeat)

    @property
    def repeat_min(self) -> int:
        return self._repeat_range[0]

    @property
    def repeat_max(self) -> int | None:
        return self._repeat_range[1]


@dataclass
class Col(TemplateNode):
    """Vertical pattern: matches one or more Excel columns.

    Parameters
    ----------
    pattern : list of str (literal) or CellCondition  (top-to-bottom)
    repeat  : how many times this column pattern must appear
    node_id : optional label surfaced in NodeResult
    """
    pattern: list[CellCondition] = field(default_factory=list)
    repeat: RepeatSpec = 1
    node_id: str | None = None

    def __post_init__(self):
        self.pattern = _normalise_pattern(self.pattern)
        self._repeat_range = _parse_repeat(self.repeat)

    @property
    def repeat_min(self) -> int:
        return self._repeat_range[0]

    @property
    def repeat_max(self) -> int | None:
        return self._repeat_range[1]


@dataclass
class EmptyRow(TemplateNode):
    """Matches one or more completely empty rows (syntactic sugar).

    Parameters
    ----------
    repeat          : repeat spec
    allow_whitespace: if True, cells that are empty strings also count as empty
    """
    repeat: RepeatSpec = 1
    allow_whitespace: bool = False
    node_id: str | None = None

    def __post_init__(self):
        self._repeat_range = _parse_repeat(self.repeat)
        cond = Types.EMPTY | Types.SPACE if self.allow_whitespace else Types.EMPTY
        # EmptyRow is stored as a Row with a single wildcard-width pattern
        self._condition = cond

    @property
    def repeat_min(self) -> int:
        return self._repeat_range[0]

    @property
    def repeat_max(self) -> int | None:
        return self._repeat_range[1]


@dataclass
class EmptyCol(TemplateNode):
    """Matches one or more completely empty columns (syntactic sugar)."""
    repeat: RepeatSpec = 1
    allow_whitespace: bool = False
    node_id: str | None = None

    def __post_init__(self):
        self._repeat_range = _parse_repeat(self.repeat)
        self._condition = Types.EMPTY | Types.SPACE if self.allow_whitespace else Types.EMPTY

    @property
    def repeat_min(self) -> int:
        return self._repeat_range[0]

    @property
    def repeat_max(self) -> int | None:
        return self._repeat_range[1]


@dataclass
class Group(TemplateNode):
    """Groups multiple Row/Col/EmptyRow/EmptyCol nodes for collective repetition.

    Parameters
    ----------
    children : sequence of TemplateNode (no nested Group for now)
    repeat   : how many times the whole group repeats
    """
    children: list[TemplateNode] = field(default_factory=list)
    repeat: RepeatSpec = 1

    def __init__(self, *children: TemplateNode, repeat: RepeatSpec = 1):
        self.children = list(children)
        self.repeat = repeat
        self._repeat_range = _parse_repeat(repeat)

    @property
    def repeat_min(self) -> int:
        return self._repeat_range[0]

    @property
    def repeat_max(self) -> int | None:
        return self._repeat_range[1]


@dataclass
class Block(TemplateNode):
    """Top-level template unit.

    A Block consists entirely of Row-family nodes (orientation='vertical')
    or entirely of Col-family nodes (orientation='horizontal').  Mixing is
    not allowed.

    Parameters
    ----------
    children    : Row, Col, EmptyRow, EmptyCol, or Group nodes
    block_id    : optional name for identification in MatchResult
    orientation : 'vertical'  → children are Row/EmptyRow/Group
                  'horizontal' → children are Col/EmptyCol/Group
    """
    children: list[TemplateNode] = field(default_factory=list)
    block_id: str | None = None
    orientation: Literal["vertical", "horizontal"] = "vertical"

    def __init__(
        self,
        *children: TemplateNode,
        block_id: str | None = None,
        orientation: Literal["vertical", "horizontal"] = "vertical",
    ):
        self.children = list(children)
        self.block_id = block_id
        self.orientation = orientation
        self._validate()

    def _validate(self):
        row_types = (Row, EmptyRow)
        col_types = (Col, EmptyCol)
        for child in self.children:
            if isinstance(child, Group):
                continue  # Group is direction-neutral at declaration time
            if self.orientation == "vertical" and isinstance(child, col_types):
                raise TypeError(
                    f"Block(orientation='vertical') cannot contain Col/EmptyCol nodes, "
                    f"got {type(child).__name__}"
                )
            if self.orientation == "horizontal" and isinstance(child, row_types):
                raise TypeError(
                    f"Block(orientation='horizontal') cannot contain Row/EmptyRow nodes, "
                    f"got {type(child).__name__}"
                )
