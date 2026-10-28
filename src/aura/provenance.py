"""Load a proposal or diagnosis with the records it references.

Each store verifies a record's own digest on load. This module also
verifies the references between records: IDs and digests that one record
holds for another must match the stored target (I9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid5

from aura.audit_store import AuditEventNotFound, JsonAuditEventStore
from aura.contracts import AuditEvent, EvidenceSnapshot
from aura.cwa import CWASnapshot, CWATrace
from aura.cwa_store import (
    CWASnapshotNotFound,
    CWATraceNotFound,
    JsonCWASnapshotStore,
    JsonCWATraceStore,
)
from aura.diagnosis import DiagnosisRecord
from aura.diagnosis_store import DiagnosisNotFound, JsonDiagnosisStore
from aura.evidence_store import (
    EvidenceSnapshotNotFound,
    JsonEvidenceSnapshotStore,
)
from aura.proposal_store import JsonProposalStore, ProposalNotFound
from aura.proposals import Proposal


T = TypeVar("T")
NOT_FOUND = (
    AuditEventNotFound,
    CWASnapshotNotFound,
    CWATraceNotFound,
    DiagnosisNotFound,
    EvidenceSnapshotNotFound,
    ProposalNotFound,
)


class ProvenanceError(ValueError):
    """Raised when a record or a reference between records is invalid."""


@dataclass(frozen=True)
class TraceProvenance:
    trace: CWATrace
    cwa_snapshot: CWASnapshot
    evidence_snapshot: EvidenceSnapshot
    audit_event: AuditEvent


@dataclass(frozen=True)
class ProposalProvenance(TraceProvenance):
    proposal: Proposal


@dataclass(frozen=True)
class DiagnosisProvenance(TraceProvenance):
    diagnosis: DiagnosisRecord


def load_proposal_provenance(
    proposal_id: UUID,
    *,
    proposal_dir: Path,
    cwa_dir: Path,
    snapshot_dir: Path,
    audit_dir: Path,
) -> ProposalProvenance:
    proposal = _load(lambda: JsonProposalStore(proposal_dir).load(proposal_id))
    trace = _load_trace(
        proposal.cwa_trace_id,
        command_id=proposal.command_id,
        cwa_snapshot_id=proposal.cwa_snapshot_id,
        response_digest=proposal.proposal_digest,
        cwa_dir=cwa_dir,
        snapshot_dir=snapshot_dir,
        audit_dir=audit_dir,
    )
    _require(
        "evidence_snapshot_id",
        proposal.evidence_snapshot_id,
        trace.evidence_snapshot.snapshot_id,
    )
    _require(
        "evidence_snapshot_digest",
        proposal.evidence_snapshot_digest,
        trace.evidence_snapshot.snapshot_digest,
    )
    return ProposalProvenance(**vars(trace), proposal=proposal)


def load_diagnosis_provenance(
    diagnosis_id: UUID,
    *,
    diagnosis_dir: Path,
    cwa_dir: Path,
    snapshot_dir: Path,
    audit_dir: Path,
) -> DiagnosisProvenance:
    diagnosis = _load(
        lambda: JsonDiagnosisStore(diagnosis_dir).load(diagnosis_id)
    )
    # The CWA service derives a trace's ID from its command and snapshot.
    trace_id = uuid5(
        diagnosis.command_id,
        f"cwa-trace:{diagnosis.cwa_snapshot_id}",
    )
    trace = _load_trace(
        trace_id,
        command_id=diagnosis.command_id,
        cwa_snapshot_id=diagnosis.cwa_snapshot_id,
        response_digest=diagnosis.response_digest,
        cwa_dir=cwa_dir,
        snapshot_dir=snapshot_dir,
        audit_dir=audit_dir,
    )
    _require(
        "cwa_snapshot_digest",
        diagnosis.cwa_snapshot_digest,
        trace.cwa_snapshot.snapshot_digest,
    )
    return DiagnosisProvenance(**vars(trace), diagnosis=diagnosis)


def _load_trace(
    trace_id: UUID,
    *,
    command_id: UUID,
    cwa_snapshot_id: UUID,
    response_digest: str,
    cwa_dir: Path,
    snapshot_dir: Path,
    audit_dir: Path,
) -> TraceProvenance:
    trace = _load(lambda: JsonCWATraceStore(cwa_dir / "traces").load(trace_id))
    _require("command_id", command_id, trace.command_id)
    _require("response_digest", response_digest, trace.response_digest)
    _require("cwa_snapshot_id", cwa_snapshot_id, trace.cwa_snapshot_id)
    cwa_snapshot = _load(
        lambda: JsonCWASnapshotStore(cwa_dir / "snapshots").load(
            cwa_snapshot_id
        )
    )
    _require(
        "cwa_snapshot_digest",
        cwa_snapshot.snapshot_digest,
        trace.cwa_snapshot_digest,
    )
    _require(
        "evidence_snapshot_id",
        cwa_snapshot.evidence_snapshot_id,
        trace.evidence_snapshot_id,
    )
    _require(
        "evidence_snapshot_digest",
        cwa_snapshot.evidence_snapshot_digest,
        trace.evidence_snapshot_digest,
    )
    evidence_snapshot = _load(
        lambda: JsonEvidenceSnapshotStore(snapshot_dir).load(
            trace.evidence_snapshot_id
        )
    )
    _require(
        "evidence_snapshot_digest",
        evidence_snapshot.snapshot_digest,
        trace.evidence_snapshot_digest,
    )
    audit_event = _load(
        lambda: JsonAuditEventStore(audit_dir).load(trace.audit_event_id)
    )
    _require("audit event command_id", trace.command_id, audit_event.command_id)
    return TraceProvenance(trace, cwa_snapshot, evidence_snapshot, audit_event)


def _load(load: Callable[[], T]) -> T:
    """Report a missing or invalid record as a provenance error.

    An OSError is left to the caller, which reports filesystem errors.
    """

    try:
        return load()
    except (*NOT_FOUND, ValueError) as error:
        raise ProvenanceError(str(error)) from error


def _require(name: str, expected: object, actual: object) -> None:
    if expected != actual:
        raise ProvenanceError(
            f"{name} does not match: expected {expected}, found {actual}"
        )


__all__ = [
    "DiagnosisProvenance",
    "ProposalProvenance",
    "ProvenanceError",
    "TraceProvenance",
    "load_diagnosis_provenance",
    "load_proposal_provenance",
]
