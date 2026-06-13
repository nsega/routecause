"""Read-only kubectl access, pinned to the lab context.

Quarantine rule (BRIEF §3 / DESIGN-DRAFT): diagnosis-side agents get **read-only**
tools only. This module exposes only read verbs and a *non-mutating* server-side
dry-run validator. Mutation (apply/patch) lives in ``routecause.actuator`` and is
imported only by the Actuator (stretch). Every call is pinned to
``kind-inference-lab`` so a company GKE context can never be touched.
"""

from __future__ import annotations

import json
import subprocess

from .config import KUBE_CONTEXT, NAMESPACE

_READONLY_VERBS = {
    "get",
    "describe",
    "top",
    "version",
    "api-resources",
    "explain",
    "logs",
}


class KubectlError(RuntimeError):
    pass


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _base(context: str, namespace: str | None) -> list[str]:
    cmd = ["kubectl", "--context", context]
    if namespace:
        cmd += ["-n", namespace]
    return cmd


def kubectl_ro(
    *args: str,
    context: str = KUBE_CONTEXT,
    namespace: str | None = NAMESPACE,
    timeout: int = 30,
) -> str:
    """Run a read-only kubectl command. Refuses any non-read verb."""
    verb = next((a for a in args if not a.startswith("-")), None)
    if verb not in _READONLY_VERBS:
        raise KubectlError(
            f"read-only kubectl refused verb {verb!r}; mutation is reserved for the Actuator"
        )
    cmd = _base(context, namespace) + list(args)
    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise KubectlError(f"{' '.join(cmd)} -> rc={proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def get_json(
    *args: str,
    context: str = KUBE_CONTEXT,
    namespace: str | None = NAMESPACE,
    timeout: int = 30,
) -> dict:
    """`kubectl get <args> -o json` parsed to a dict."""
    out = kubectl_ro("get", *args, "-o", "json", context=context, namespace=namespace, timeout=timeout)
    return json.loads(out)


def apply_dry_run_server(manifest_yaml: str, timeout: int = 45) -> tuple[bool, str]:
    """Validate a manifest with server-side dry-run (A4). Non-mutating: the API
    server validates admission/schema but persists nothing."""
    cmd = _base(KUBE_CONTEXT, NAMESPACE) + ["apply", "--dry-run=server", "-f", "-"]
    proc = _run(cmd, input_text=manifest_yaml, timeout=timeout)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
