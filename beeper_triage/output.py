"""Output helpers: JSON for machines (non-TTY / --json), pretty text for humans."""
from __future__ import annotations

import json
import sys
from typing import Any, Optional


def is_json_mode(json_flag: Optional[bool]) -> bool:
    """Decide JSON vs human output.

    An explicit --json / --no-json flag (True/False) always wins. When the flag
    is None (unset), default to JSON whenever stdout is not a TTY (i.e. piped or
    invoked by an agent), and human output when attached to a terminal.
    """
    if json_flag is not None:
        return json_flag
    return not sys.stdout.isatty()


def emit(data: Any, *, json_flag: Optional[bool] = None, human: Optional[str] = None) -> None:
    """Print `data` as JSON in machine mode, or `human` text in human mode.

    In JSON mode the `human` argument is intentionally ignored — all data
    travels through `data` for machine consumers. Falls back to indented JSON
    for humans when no `human` string is supplied.
    """
    if is_json_mode(json_flag):
        print(json.dumps(data, default=str))
    else:
        print(human if human is not None else json.dumps(data, indent=2, default=str))
