"""Tiny verbose-aware logger. Set parlai.log.VERBOSE = True to enable warn() output."""

from __future__ import annotations

import sys

VERBOSE: bool = False


def warn(msg: str) -> None:
    """Print a warning to stderr only when VERBOSE is on."""
    if VERBOSE:
        sys.stderr.write(f"[parlai] {msg}\n")
        sys.stderr.flush()
