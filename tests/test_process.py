from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from aura.process import ProcessError, ProcessResult, run_process


def python(code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


def test_child_reads_no_stdin_without_input() -> None:
    result = run_process(
        python("import sys; sys.stdout.write(repr(sys.stdin.read()))"),
        env=None,
        timeout=10,
    )

    assert result == ProcessResult(returncode=0, stdout=b"''", stderr=b"")


def test_input_is_written_to_stdin() -> None:
    result = run_process(
        python("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        env=None,
        timeout=10,
        input=b"patch\r\n",
    )

    assert result.stdout == b"patch\r\n"


def test_output_is_returned_as_unmodified_bytes() -> None:
    result = run_process(
        python(
            "import sys; sys.stdout.buffer.write(b'a\\r\\n\\xff'); "
            "sys.stderr.buffer.write(b'err\\xfe'); sys.exit(3)"
        ),
        env=None,
        timeout=10,
    )

    assert result == ProcessResult(
        returncode=3,
        stdout=b"a\r\n\xff",
        stderr=b"err\xfe",
    )


def test_text_decodes_utf8_with_replacement() -> None:
    result = ProcessResult(returncode=0, stdout=b"ok\xff", stderr=b"\xfeno")

    assert result.stdout_text() == "ok\ufffd"
    assert result.stderr_text() == "\ufffdno"


def test_env_and_cwd_are_passed(tmp_path: Path) -> None:
    result = run_process(
        python(
            "import os; "
            "print(os.environ['AURA_PROCESS_TEST'], os.getcwd(), sep='|')"
        ),
        env={"AURA_PROCESS_TEST": "set"},
        timeout=10,
        cwd=tmp_path,
    )

    assert result.stdout_text().strip() == f"set|{tmp_path.resolve()}"


@pytest.mark.parametrize("missing", ["env", "timeout"])
def test_env_and_timeout_are_required(missing: str) -> None:
    options = {"env": None, "timeout": 10}
    del options[missing]

    with pytest.raises(TypeError):
        run_process(python("pass"), **options)


def test_timeout_raises_process_error() -> None:
    started = time.monotonic()

    with pytest.raises(ProcessError, match="timed out after 0.5 seconds") as error:
        run_process(python("import time; time.sleep(30)"), env=None, timeout=0.5)

    assert error.value.timed_out
    assert time.monotonic() - started < 10


def test_missing_program_raises_process_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing-program"

    with pytest.raises(ProcessError, match="cannot run") as error:
        run_process([str(missing)], env=None, timeout=10)

    assert not error.value.timed_out


def test_missing_working_directory_is_reported_as_such(tmp_path: Path) -> None:
    missing = tmp_path / "missing-directory"

    with pytest.raises(ProcessError) as error:
        run_process(python("pass"), env=None, timeout=10, cwd=missing)

    assert str(error.value) == (
        f"cannot use working directory {missing} for {sys.executable}: "
        "No such file or directory"
    )
    assert not error.value.timed_out
