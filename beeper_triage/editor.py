"""Helpers for editing message drafts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Optional


class EditorError(RuntimeError):
    """Raised when the editor cannot be opened."""


def edit_text(initial_text: str, editor: str) -> str:
    """Open the user's editor with initial text and return the edited content."""

    if not editor:
        raise EditorError("EDITOR is not set.")

    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt") as handle:
        path = handle.name
        handle.write(initial_text)
        handle.flush()

    try:
        subprocess.run([editor, path], check=True)
    except FileNotFoundError as exc:
        raise EditorError(f"Editor not found: {editor}") from exc
    except subprocess.CalledProcessError as exc:
        raise EditorError("Editor returned a non-zero exit code.") from exc
    finally:
        pass

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
