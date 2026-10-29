"""Terraform state vs. live account drift check (read-only).

Two questions, two plans, both run with the reviewer's read-only credentials:

1. "Was the live account changed outside Terraform since the last apply?"
   terraform plan -refresh-only -detailed-exitcode -lock=false
   -> the JSON plan's ``resource_drift`` lists resources whose live
      attributes differ from the state file.

2. "Does the code in the delivered repo match what is running?"
   terraform plan -detailed-exitcode -lock=false
   -> ``resource_changes`` with actions other than no-op are things the
      delivered code would create, change or destroy: code that was never
      applied, or live changes the code does not know about.

Exit codes of -detailed-exitcode: 0 = no changes, 2 = changes, 1 = error.

Safety:
- Only ``init``, ``plan``, ``show`` and ``version`` subcommands are allowed.
  ``apply``, ``destroy``, ``import``, ``state rm/mv/push``, ``refresh`` and
  ``force-unlock`` are refused before anything runs.
- ``-lock=false`` because a read-only role cannot (and must not) write a
  state lock. The plan file stays in a temporary folder and is deleted.
- The output lists resource addresses and *attribute names* only, never
  values: state and plans can contain secrets.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

ALLOWED_SUBCOMMANDS = {"init", "plan", "show", "version"}
REFUSED_FLAGS = {"-auto-approve", "-destroy"}

Runner = Callable[[list[str], Path], "tuple[int, str, str]"]


class UnsafeTerraformCommand(RuntimeError):
    pass


def assert_safe(args: list[str]) -> None:
    sub = next((a for a in args[1:] if not a.startswith("-")), None)
    if sub not in ALLOWED_SUBCOMMANDS:
        raise UnsafeTerraformCommand(f"terraform {sub!r} is not allowed in a read-only assessment")
    bad = REFUSED_FLAGS.intersection(args)
    if bad:
        raise UnsafeTerraformCommand(f"flags not allowed: {sorted(bad)}")
    if sub == "plan" and "-lock=false" not in args:
        raise UnsafeTerraformCommand("plan must run with -lock=false (the reviewer role cannot write locks)")


def subprocess_runner(args: list[str], cwd: Path) -> tuple[int, str, str]:
    assert_safe(args)
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1800)
    return proc.returncode, proc.stdout, proc.stderr


def changed_attribute_names(before, after) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return [] if before == after else ["<whole object>"]
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


def parse_plan_json(doc: dict) -> dict:
    drift = []
    for rc in doc.get("resource_drift", []) or []:
        change = rc.get("change", {})
        drift.append(
            {
                "address": rc.get("address"),
                "type": rc.get("type"),
                "actions": change.get("actions", []),
                "changed_attributes": changed_attribute_names(change.get("before"), change.get("after")),
            }
        )
    planned = []
    for rc in doc.get("resource_changes", []) or []:
        actions = rc.get("change", {}).get("actions", [])
        if actions and actions != ["no-op"] and actions != ["read"]:
            change = rc.get("change", {})
            planned.append(
                {
                    "address": rc.get("address"),
                    "type": rc.get("type"),
                    "actions": actions,
                    "changed_attributes": changed_attribute_names(change.get("before"), change.get("after")),
                }
            )
    return {"terraform_version": doc.get("terraform_version"), "drift": drift, "planned_changes": planned}


EXIT_MEANING = {0: "no changes", 2: "changes found", 1: "error"}


def _plan(terraform: str, workdir: Path, runner: Runner, refresh_only: bool, var_files: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="drift-") as tmp:
        plan_file = str(Path(tmp) / "review.tfplan")
        args = [terraform, "plan", "-input=false", "-lock=false", "-no-color", "-detailed-exitcode", f"-out={plan_file}"]
        if refresh_only:
            args.insert(2, "-refresh-only")
        args += [f"-var-file={v}" for v in var_files]
        assert_safe(args)
        code, _out, err = runner(args, workdir)
        result = {"mode": "refresh-only" if refresh_only else "code-vs-live", "exit_code": code, "meaning": EXIT_MEANING.get(code, "unknown")}
        if code == 1:
            result["error"] = err.strip()[-2000:]
            return result
        show = [terraform, "show", "-json", "-no-color", plan_file]
        assert_safe(show)
        scode, sout, serr = runner(show, workdir)
        if scode != 0:
            result["error"] = f"terraform show failed: {serr.strip()[-500:]}"
            return result
        result.update(parse_plan_json(json.loads(sout)))
        return result


def run_drift_check(
    workdir: Path,
    terraform: str = "terraform",
    runner: Runner = subprocess_runner,
    var_files: list[str] | None = None,
    skip_init: bool = False,
) -> dict:
    workdir = Path(workdir)
    var_files = var_files or []
    report: dict = {"workdir": str(workdir)}
    if not skip_init:
        init = [terraform, "init", "-input=false", "-no-color"]
        assert_safe(init)
        code, _o, err = runner(init, workdir)
        if code != 0:
            report["error"] = f"terraform init failed: {err.strip()[-1000:]}"
            return report
    report["refresh_only"] = _plan(terraform, workdir, runner, True, var_files)
    report["code_vs_live"] = _plan(terraform, workdir, runner, False, var_files)
    return report
