"""Result data structures returned by the Excel extraction engine."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CellMatch:
    row: int
    col: int
    value: str
    is_merged: bool


@dataclass
class RowMatch:
    row: int
    cells: list[CellMatch]
    node_id: str | None


@dataclass
class BlockMatch:
    start: tuple[int, int]
    end: tuple[int, int]
    rows: list[RowMatch]
    block_id: str | None


@dataclass
class MatchOptions:
    """Options that control the behaviour of match_template().
    """
    return_mode: int = 0       # 0 for all, positive for specified number of matches
    # near_miss_threshold:     float | None = None
    # search_range:            tuple[int, int, int, int] | None = None
    # consume_matched_regions: bool = False
    # warn_fuzzy:              bool = True
