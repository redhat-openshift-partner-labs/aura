"""Run Git commands that cannot be redirected or extended by the caller.

An inherited GIT_DIR, GIT_INDEX_FILE, or similar would point Git at another
repository or index, and hooks or an fsmonitor would run code. Aura only
reads repositories, so every Git call drops GIT_ variables, disables hooks
and fsmonitor, and turns off optional locks so commands such as
`git status` never rewrite an index.
"""

from __future__ import annotations

import os


GIT_ISOLATION = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def isolated_git_environment() -> dict[str, str]:
    """Return the process environment without any GIT_ variable."""

    return {
        **{
            name: value
            for name, value in os.environ.items()
            if not name.startswith("GIT_")
        },
        "GIT_OPTIONAL_LOCKS": "0",
    }


__all__ = ["GIT_ISOLATION", "isolated_git_environment"]
