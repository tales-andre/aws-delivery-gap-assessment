import json
import shutil
from pathlib import Path

import pytest

from evidence_kit.drift import UnsafeTerraformCommand, assert_safe, parse_plan_json, run_drift_check

FIXTURE = Path(__file__).parent / "fixtures" / "plan-refresh-only.json"


@pytest.mark.parametrize(
    "args",
    [
        ["terraform", "apply", "-auto-approve"],
        ["terraform", "destroy"],
        ["terraform", "import", "aws_s3_bucket.b", "b"],
        ["terraform", "state", "rm", "x"],
        ["terraform", "force-unlock", "id"],
        ["terraform", "refresh"],
        ["terraform", "plan", "-input=false"],  # missing -lock=false
        ["terraform", "plan", "-lock=false", "-destroy"],
    ],
)
def test_unsafe_commands_are_refused(args):
    with pytest.raises(UnsafeTerraformCommand):
        assert_safe(args)


def test_safe_commands_pass():
    assert_safe(["terraform", "init", "-input=false"])
    assert_safe(["terraform", "-chdir=env/prod", "plan", "-lock=false", "-refresh-only"])
    assert_safe(["terraform", "show", "-json", "x.tfplan"])


def test_parse_lists_attribute_names_never_values():
    parsed = parse_plan_json(json.loads(FIXTURE.read_text()))
    db = parsed["drift"][0]
    assert db["address"] == "module.db.aws_db_instance.this[0]"
    assert db["changed_attributes"] == ["backup_retention_period", "multi_az"]
    assert "redacted-in-state" not in json.dumps(parsed)
    assert parsed["drift"][1]["actions"] == ["delete"]
    assert [c["address"] for c in parsed["planned_changes"]] == ["aws_wafv2_web_acl_association.alb"]


class FakeTerraform:
    """Records commands and replays exit codes; returns the fixture for `show -json`."""

    def __init__(self, plan_codes):
        self.plan_codes = list(plan_codes)
        self.calls = []

    def __call__(self, args, cwd):
        assert_safe(args)
        self.calls.append(args)
        sub = args[1]
        if sub == "init":
            return 0, "", ""
        if sub == "plan":
            code = self.plan_codes.pop(0)
            return code, "", "Error: AccessDenied reading state" if code == 1 else ""
        if sub == "show":
            return 0, FIXTURE.read_text(), ""
        raise AssertionError(args)


def test_run_drift_check_uses_refresh_only_and_lock_false(tmp_path):
    fake = FakeTerraform([2, 2])
    report = run_drift_check(tmp_path, runner=fake)
    plans = [c for c in fake.calls if c[1] == "plan"]
    assert "-refresh-only" in plans[0] and "-refresh-only" not in plans[1]
    assert all("-lock=false" in c and "-detailed-exitcode" in c for c in plans)
    assert report["refresh_only"]["meaning"] == "changes found"
    assert len(report["refresh_only"]["drift"]) == 2


def test_plan_error_is_reported_not_hidden(tmp_path):
    report = run_drift_check(tmp_path, runner=FakeTerraform([1, 0]))
    assert report["refresh_only"]["meaning"] == "error"
    assert "AccessDenied" in report["refresh_only"]["error"]
    assert "drift" not in report["refresh_only"]


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_real_terraform_plan_json_is_parsed(tmp_path):
    """Offline integration: terraform_data is built in, so no provider download is needed."""
    (tmp_path / "main.tf").write_text('resource "terraform_data" "marker" {\n  input = "sow-item-7"\n}\n')
    report = run_drift_check(tmp_path)
    assert report["refresh_only"]["exit_code"] == 0
    assert report["refresh_only"]["drift"] == []
    code_vs_live = report["code_vs_live"]
    assert code_vs_live["exit_code"] == 2
    assert code_vs_live["planned_changes"][0]["address"] == "terraform_data.marker"
    assert code_vs_live["planned_changes"][0]["actions"] == ["create"]
