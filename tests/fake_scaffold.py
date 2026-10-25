"""A miniature fleet-cluster-scaffold, committed to a temporary Git tree.

`overlay_files` is the single description of what the fake scaffold
writes. The fake's `cli.py` embeds its source, because the scaffold runs
in an isolated interpreter that cannot import test modules.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from test_git_inventory_collector import commit_repository


def fake_infra_prefix(name: str) -> str:
    prefix = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9-]", "-", name))
    return prefix[:21].rstrip("-")


def overlay_files(arguments: list[str]) -> dict[str, str]:
    """Workspace-relative files the fake scaffold writes for arguments."""

    values = dict(zip(arguments[1::2], arguments[2::2], strict=True))
    name = values["--name"]
    topology = values.get("--topology", "standard")
    metadata = "platform: aws\naws-credentials-mode: scoped\n"
    if "--htpasswd-provider-name" in values:
        metadata += (
            f"htpasswd-provider-name: {values['--htpasswd-provider-name']}\n"
        )
    templates = f"../../../cluster-templates/aws/{topology}"
    files = {
        "kustomization.yaml": "resources:\n- crossplane/\n- hive/\n",
        "metadata.yaml": metadata,
        "hive/kustomization.yaml": f"resources:\n- {templates}/hive\n",
        "hive/patches/clusterdeployment.yaml": (
            "- op: replace\n  path: /metadata/name\n"
            f"  value: {name}\n"
            "- op: replace\n  path: /spec/platform/aws/region\n"
            f"  value: {values['--region']}\n"
            "- op: replace\n  path: /spec/provisioning/imageSetRef/name\n"
            f"  value: {values.get('--image-set', 'img4.21.13-x86-64-appsub')}\n"
        ),
        "hive/patches/managedcluster.yaml": (
            "- op: add\n  path: /metadata/labels/tier\n"
            f"  value: {values['--tier']}\n"
            "- op: add\n  path: /metadata/labels/environment\n"
            f"  value: {values.get('--environment', 'development')}\n"
        ),
        "crossplane/kustomization.yaml": (
            f"resources:\n- {templates}/crossplane-scoped\n"
        ),
        "crossplane/patches/machine-api/policy.yaml": (
            "- op: replace\n  path: /spec/forProvider/policy\n"
            "  value: '{\"Resource\": \"arn:aws:iam::*:instance-profile/"
            f"{fake_infra_prefix(name)}-?????-worker-profile\"}}'\n"
        ),
    }
    if topology == "standard":
        files["hive/patches/machinepool-worker.yaml"] = (
            "- op: replace\n  path: /spec/replicas\n  value: 3\n"
        )
    return {f"provision/{name}/{path}": text for path, text in files.items()}


MAIN = '''

def main(args):
    import json
    import os
    import sys
    import time

    values = dict(zip(args[1::2], args[2::2]))
    behavior = values.get("--environment", "development")
    if behavior == "fail":
        print("bad --environment value", file=sys.stderr)
        return 1
    if behavior == "hang":
        time.sleep(60)
    if behavior == "env":
        print(json.dumps(sorted(os.environ)))
    if behavior == "stdin":
        print(sys.stdin.read())
    if behavior == "badstdout":
        sys.stdout.flush()
        sys.stdout.buffer.write(b"\\xff\\n")
        sys.stdout.buffer.flush()
    files = overlay_files(args)
    if behavior == "template":
        marker = os.path.join(values["--cluster-templates-dir"], "marker.txt")
        with open(marker, encoding="utf-8") as stream:
            files[f"provision/{values['--name']}/marker.yaml"] = (
                f"marker: {stream.read().strip()}\\n"
            )
    if behavior == "ignored":
        ignored = os.path.join(values["--cluster-templates-dir"], "ignored.txt")
        files[f"provision/{values['--name']}/ignored.yaml"] = (
            f"present: {os.path.exists(ignored)}\\n"
        )
    for path, text in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
    cluster = os.path.join(values["--base-path"], values["--name"])
    if behavior == "symlink":
        os.symlink("metadata.yaml", os.path.join(cluster, "link.yaml"))
    if behavior == "exec":
        script = os.path.join(cluster, "run.sh")
        with open(script, "w", encoding="utf-8") as stream:
            stream.write("#!/bin/sh\\n")
        os.chmod(script, 0o755)
    if behavior == "modify":
        marker = os.path.join(values["--cluster-templates-dir"], "marker.txt")
        with open(marker, "w", encoding="utf-8") as stream:
            stream.write("changed\\n")
    if behavior == "delete":
        os.remove(os.path.join("provision", "existing", "metadata.yaml"))
    if behavior == "fifo":
        os.mkfifo(os.path.join(cluster, "pipe"))
    if behavior == "binary":
        with open(os.path.join(cluster, "blob.yaml"), "wb") as stream:
            stream.write(b"\\xff\\xfe")
    print(f"Cluster '{values['--name']}' scaffolded successfully", file=sys.stderr)
    return 0
'''


def fake_scaffold(root: Path) -> Path:
    """Commit a fake scaffold under root and return the checkout path."""

    package = root / "src" / "scaffold"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "import re\n\n\n"
        + inspect.getsource(fake_infra_prefix)
        + "\n\n"
        + inspect.getsource(overlay_files)
        + MAIN
    )
    commit_repository(root)
    return root


def fleet_with_templates(root: Path, marker: str = "base") -> Path:
    """Commit a fleet-clusters tree with one cluster and a template marker."""

    from test_git_inventory_collector import write_cluster

    write_cluster(root, cluster_name="existing")
    templates = root / "cluster-templates"
    templates.mkdir(exist_ok=True)
    (templates / "marker.txt").write_text(f"{marker}\n")
    commit_repository(root)
    return root


__all__ = [
    "fake_infra_prefix",
    "fake_scaffold",
    "fleet_with_templates",
    "overlay_files",
]
