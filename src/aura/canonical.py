from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_digest(payload: dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = ["canonical_digest", "canonical_json"]
