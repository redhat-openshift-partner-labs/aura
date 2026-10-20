from __future__ import annotations

import pytest

from aura.proposal_store import (
    JsonProposalStore,
    ProposalNotFound,
)
from test_proposals import make_proposal


def test_json_proposal_store_saves_and_loads(tmp_path) -> None:
    store = JsonProposalStore(root=tmp_path / "proposals")
    proposal = make_proposal()

    path = store.save(proposal)

    assert path == tmp_path / "proposals" / f"{proposal.proposal_id}.json"
    assert store.load(proposal.proposal_id) == proposal


def test_json_proposal_store_rejects_missing(tmp_path) -> None:
    with pytest.raises(ProposalNotFound):
        JsonProposalStore(root=tmp_path).load(make_proposal().proposal_id)


def test_json_proposal_store_rejects_tampered_record(tmp_path) -> None:
    store = JsonProposalStore(root=tmp_path)
    proposal = make_proposal()
    path = store.save(proposal)
    path.chmod(0o600)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            proposal.summary, "Tampered summary."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="proposal digest does not match"):
        store.load(proposal.proposal_id)
