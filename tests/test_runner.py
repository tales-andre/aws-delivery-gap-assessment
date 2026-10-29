import hashlib
import json

import kit
from evidence_kit.demo import run_demo


def test_demo_bundle_has_manifest_with_matching_hashes(tmp_path):
    out, results = run_demo(tmp_path / "bundle")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["read_only_guard"] is True
    assert manifest["mode"].startswith("demo")
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
    assert {r["evidence_id"] for r in results} == {f"E-{i:02d}" for i in range(11)}


def test_summary_never_reports_uncollected_checks_as_empty(tmp_path):
    out, results = run_demo(tmp_path / "bundle")
    summary = (out / "summary.md").read_text(encoding="utf-8")
    statuses = {r["evidence_id"]: r["collection_status"] for r in results}
    assert statuses["E-09"] == "not_collected"  # moto does not implement LookupEvents
    assert "Events found" not in summary
    assert "NOT COLLECTED: cloudtrail:LookupEvents CreateCluster and 13 similar calls" in summary
    assert "| E-03 | RDS fieldwork-prod-db (postgres) | Multi-AZ no; backup retention 7 d" in summary
    assert "Reviewer notes" in summary


def test_cli_demo_and_score(tmp_path, capsys):
    assert kit.main(["demo", "--out", str(tmp_path / "cli"), "--only", "databases", "E-05"]) == 0
    printed = capsys.readouterr().out
    assert "E-03 RDS instances and clusters: complete" in printed
    assert "E-05" in printed and "E-01" not in printed
