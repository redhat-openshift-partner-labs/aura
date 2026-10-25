from __future__ import annotations

import json
from uuid import UUID

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


@pytest.mark.parametrize("digest", ["", None, "missing"])
def test_json_proposal_store_requires_the_stored_digest(
    tmp_path,
    digest: str | None,
) -> None:
    store = JsonProposalStore(root=tmp_path)
    proposal = make_proposal()
    path = store.save(proposal)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["summary"] = "Tampered summary."
    if digest == "missing":
        del record["proposal_digest"]
    else:
        record["proposal_digest"] = digest
    path.chmod(0o600)
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="has no stored proposal_digest"):
        store.load(proposal.proposal_id)


def test_json_proposal_store_rejects_a_record_under_another_name(
    tmp_path,
) -> None:
    store = JsonProposalStore(root=tmp_path)
    proposal = make_proposal()
    path = store.save(proposal)
    other = UUID(int=999)
    path.rename(path.with_name(f"{other}.json"))

    with pytest.raises(
        ValueError,
        match=f"{other}.json holds proposal {proposal.proposal_id}",
    ):
        store.load(other)


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
