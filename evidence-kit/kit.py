"""Evidence kit command line.

    python evidence-kit/kit.py demo  --out out/demo
    python evidence-kit/kit.py aws   --profile reviewer --region us-east-1 --out evidence/aws
    python evidence-kit/kit.py drift --workdir path/to/terraform/env --out evidence/drift.json
    python evidence-kit/kit.py repo  --path path/to/clone --since 2026-03-01 --until 2026-09-30 --out evidence/repo.json
    python evidence-kit/kit.py score sample-report/scoring.csv

Every subcommand only reads. See evidence-kit/README.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_kit import checks, scoring  # noqa: E402
from evidence_kit.runner import dump  # noqa: E402


def cmd_demo(a) -> int:
    from evidence_kit.demo import run_demo

    out, results = run_demo(Path(a.out), only=a.only)
    _report(out, results)
    return 0


def cmd_aws(a) -> int:
    from evidence_kit.guard import guarded_session
    from evidence_kit.runner import run_checks, write_bundle

    session = guarded_session(profile=a.profile, region=a.region)
    ctx = checks.Context(
        region=a.region,
        required_tags=tuple(a.required_tags.split(",")),
        lookback_days=a.lookback_days,
    )
    started = dt.datetime.now(dt.timezone.utc)
    results = run_checks(session, ctx, only=a.only)
    out = write_bundle(results, Path(a.out), {"mode": "live (read-only)", "region": a.region, "profile": a.profile, "started_at": started})
    _report(out, results)
    return 0


def cmd_drift(a) -> int:
    from evidence_kit.drift import run_drift_check

    report = run_drift_check(Path(a.workdir), terraform=a.terraform, var_files=a.var_file or [], skip_init=a.skip_init)
    _write(a.out, report)
    for key in ("refresh_only", "code_vs_live"):
        r = report.get(key)
        if r:
            print(f"{key}: exit {r['exit_code']} ({r['meaning']}), drifted {len(r.get('drift', []))}, planned changes {len(r.get('planned_changes', []))}")
    if "error" in report:
        print(report["error"], file=sys.stderr)
        return 1
    return 0


def cmd_repo(a) -> int:
    from evidence_kit.repo import collect_repo_evidence

    report = collect_repo_evidence(Path(a.path), branch=a.branch, since=a.since, until=a.until)
    _write(a.out, report)
    print(f"{report['repo']}: {report['commit_count']} commits in window, {len(report['ci_files'])} CI files, "
          f"{len(report['iac_dirs'])} IaC folders, {len(report['unmerged_branches'])} unmerged branches")
    return 0


def cmd_score(a) -> int:
    tasks = scoring.load(Path(a.csv))
    print(scoring.markdown_table(tasks))
    return 0


def _write(out: str | None, obj) -> None:
    text = dump(obj)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out}")
    else:
        print(text)


def _report(out: Path, results: list[dict]) -> None:
    for r in results:
        errs = f" ({len(r['errors'])} calls not collected)" if r["errors"] else ""
        print(f"{r['evidence_id']} {r['title']}: {r['collection_status']}{errs}")
    print(f"\nEvidence bundle: {out}  (summary.md, manifest.json with SHA-256 of every file)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only evidence kit for AWS delivery gap assessments")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run all checks against a fictional account simulated with moto (offline)")
    d.add_argument("--out", default="out/demo")
    d.add_argument("--only", nargs="*", help="check keys or evidence ids, e.g. iam E-03")
    d.set_defaults(fn=cmd_demo)

    w = sub.add_parser("aws", help="collect evidence from a real account with read-only credentials")
    w.add_argument("--profile", help="AWS profile that assumes the reviewer role")
    w.add_argument("--region", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--required-tags", default=",".join(checks.DEFAULT_REQUIRED_TAGS))
    w.add_argument("--lookback-days", type=int, default=90)
    w.add_argument("--only", nargs="*")
    w.set_defaults(fn=cmd_aws)

    t = sub.add_parser("drift", help="terraform state vs live account (plan -refresh-only and plan, read-only)")
    t.add_argument("--workdir", required=True)
    t.add_argument("--terraform", default="terraform")
    t.add_argument("--var-file", action="append")
    t.add_argument("--skip-init", action="store_true")
    t.add_argument("--out")
    t.set_defaults(fn=cmd_drift)

    r = sub.add_parser("repo", help="delivery record from a local git clone")
    r.add_argument("--path", required=True)
    r.add_argument("--branch", default="HEAD")
    r.add_argument("--since")
    r.add_argument("--until")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_repo)

    s = sub.add_parser("score", help="compute task and project completion from a reviewer's scoring CSV")
    s.add_argument("csv")
    s.set_defaults(fn=cmd_score)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
