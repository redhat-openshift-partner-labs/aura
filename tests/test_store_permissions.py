from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aura.audit_store import JsonAuditEventStore
from aura.command_store import JsonCommandStore
from aura.cwa_store import JsonCWASnapshotStore, JsonCWATraceStore
from aura.diagnosis_store import JsonDiagnosisStore
from aura.evidence_store import JsonEvidenceSnapshotStore
from aura.proposal_store import JsonProposalStore
from test_audit_event_store import make_audit_event
from test_command_store import make_command
from test_cwa_store import make_snapshot as make_cwa_snapshot
from test_cwa_store import make_trace as make_cwa_trace
from test_diagnosis_store import make_record as make_diagnosis
from test_evidence_snapshot_store import make_snapshot
from test_proposals import make_proposal


@pytest.mark.parametrize(
    ("store_factory", "record"),
    [
        (JsonCommandStore, make_command("permission-test")),
        (JsonEvidenceSnapshotStore, make_snapshot()),
        (JsonAuditEventStore, make_audit_event()),
        (JsonCWASnapshotStore, make_cwa_snapshot()),
        (JsonCWATraceStore, make_cwa_trace()),
        (JsonDiagnosisStore, make_diagnosis()),
        (JsonProposalStore, make_proposal()),
    ],
)
def test_json_store_restricts_directory_and_record_permissions(
    tmp_path: Path,
    store_factory,
    record,
) -> None:
    root = tmp_path / "store"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)
    previous_umask = os.umask(0o000)
    try:
        path = store_factory(root).save(record)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


__all__ = [
    "test_json_store_restricts_directory_and_record_permissions",
]
