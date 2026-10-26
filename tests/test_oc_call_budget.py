from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from aura.collectors.oc_cli import OpenShiftCLI
from aura.contracts import AuditEventType, CommandState
from test_failure_matrix import (
    COMMAND_ID,
    check,
    execute,
    scenario_named,
)
from invariants import command_chain


class CountingRunner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, args: Sequence[str]) -> str:
        self.calls += 1
        return '{"items": []}'


class OcReadingCollector:
    """Makes a number of oc reads, then collects with a fake collector."""

    def __init__(self, root: Path, reads: int, runner: CountingRunner) -> None:
        self._root = root
        self._reads = reads
        self._runner = runner

    def collect(self, **options: object):
        cli = OpenShiftCLI(
            kubeconfig_path=self._root / "kubeconfig",
            command_runner=self._runner,
        )
        for index in range(self._reads):
            cli.resource(f"resource{index}").items()
        _, collector = scenario_named("inventory-audited").build(self._root)
        return collector.collect(**options)


def run_reads(root: Path, monkeypatch, reads: int):
    root.mkdir()
    runner = CountingRunner()
    run, before = execute(
        scenario_named("inventory-audited"),
        root,
        monkeypatch,
        collector=OcReadingCollector(root, reads, runner),
    )
    return run, before, runner


def test_ten_oc_calls_succeed(tmp_path, monkeypatch) -> None:
    run, before, runner = run_reads(tmp_path / "run", monkeypatch, 10)

    assert run.exit_code == 0, run.stderr
    assert runner.calls == 10
    assert check(tmp_path / "run", before).status is CommandState.SUCCEEDED


def test_eleventh_oc_call_fails_the_command(tmp_path, monkeypatch) -> None:
    root = tmp_path / "run"
    run, before, runner = run_reads(root, monkeypatch, 11)

    assert run.exit_code == 2
    assert run.stdout == ""
    assert run.stderr == "aura: oc call budget of 10 exceeded\n"
    assert runner.calls == 10
    assert check(root, before).status is CommandState.FAILED
    assert [
        event.event_type for event in command_chain(root / "audit", COMMAND_ID)
    ] == [AuditEventType.COMMAND_RECEIVED, AuditEventType.COMMAND_FAILED]


def test_budget_resets_between_commands(tmp_path, monkeypatch) -> None:
    for name in ("first", "second"):
        run, _, runner = run_reads(tmp_path / name, monkeypatch, 10)

        assert run.exit_code == 0, (name, run.stderr)
        assert runner.calls == 10


def test_cached_resource_reads_count_once(tmp_path) -> None:
    runner = CountingRunner()
    resource = OpenShiftCLI(
        kubeconfig_path=tmp_path / "kubeconfig",
        command_runner=runner,
    ).resource("clusterdeployments")

    resource.items()
    resource.resource_version()

    assert runner.calls == 1
