"""Run the checks and write an evidence bundle.

Bundle layout (one folder per run):

    E-00-identity.json ... E-10-well_architected.json   facts + errors per check
    manifest.json                                         run metadata + SHA-256 of every file
    summary.md                                            facts table with an empty "Reviewer notes" column
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from . import __version__
from .checks import CHECKS, Context, Recorder
from .summary import render_summary


def _json_default(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=_json_default)


def _kit_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def run_checks(session, ctx: Context, only: list[str] | None = None) -> list[dict]:
    results = []
    for check in CHECKS:
        if only and check.key not in only and check.evidence_id not in only:
            continue
        rec = Recorder()
        started = dt.datetime.now(dt.timezone.utc)
        facts = check.fn(session, ctx, rec)
        if not rec.errors:
            status = "complete"
        elif len(rec.errors) >= rec.calls:
            status = "not_collected"
        else:
            status = "partial"
        results.append(
            {
                "evidence_id": check.evidence_id,
                "check": check.key,
                "title": check.title,
                "collected_at": started,
                "region": ctx.region,
                "collection_status": status,
                "errors": rec.errors,
                "facts": facts,
            }
        )
    return results


def write_bundle(results: list[dict], out_dir: Path, meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for r in results:
        name = f"{r['evidence_id']}-{r['check']}.json"
        text = dump(r)
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
        files[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    summary = render_summary(results, meta)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8", newline="\n")
    files["summary.md"] = hashlib.sha256(summary.encode("utf-8")).hexdigest()

    identity = next((r["facts"] for r in results if r["check"] == "identity"), {})
    manifest = {
        **meta,
        "kit_version": __version__,
        "kit_commit": _kit_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "account": identity.get("account"),
        "caller_arn": identity.get("caller_arn"),
        "finished_at": dt.datetime.now(dt.timezone.utc),
        "read_only_guard": True,
        "files_sha256": files,
    }
    (out_dir / "manifest.json").write_text(dump(manifest), encoding="utf-8", newline="\n")
    return out_dir
