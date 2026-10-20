"""Deprovision state derived from Git sentinels, definitions, and OpenShift."""

from __future__ import annotations

from aura.contracts import EvidenceItem


DEPROVISION_REQUESTED = frozenset({"pending", "archive-pending", "stranded"})


def deprovision_status(
    git_items: list[EvidenceItem],
    openshift_item: EvidenceItem | None,
    sentinels: list[EvidenceItem],
) -> str | None:
    """Return the deprovision state for one cluster, or None without a sentinel."""

    if not sentinels:
        return None
    if not git_items:
        return "orphaned"
    if all(item.data.get("status") == "archived" for item in git_items):
        return "leftover"
    if openshift_item is not None:
        return "pending"
    if any(item.data.get("archive_suffix") for item in sentinels):
        return "archive-pending"
    return "stranded"


__all__ = ["DEPROVISION_REQUESTED", "deprovision_status"]
