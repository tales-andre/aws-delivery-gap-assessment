import json
from pathlib import Path

from evidence_kit.guard import BLOCKED_READS

IAM = Path(__file__).resolve().parents[1] / "evidence-kit" / "iam"


def test_templates_are_valid_policy_json():
    for f in IAM.glob("*.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["Version"] == "2012-10-17", f.name
        assert doc["Statement"], f.name


def test_trust_policy_requires_external_id_and_mfa():
    (st,) = json.loads((IAM / "reviewer-trust-policy.json").read_text())["Statement"]
    assert "sts:ExternalId" in st["Condition"]["StringEquals"]
    assert st["Condition"]["Bool"]["aws:MultiFactorAuthPresent"] == "true"


def test_deny_policy_covers_the_data_reads_the_guard_blocks():
    denied = set()
    for st in json.loads((IAM / "deny-data-reads.json").read_text())["Statement"]:
        assert st["Effect"] == "Deny"
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        denied |= {a.split(":", 1)[1] for a in actions}
    for op in ("GetObject", "GetSecretValue", "GetParameter", "GetItem", "GetLogEvents", "GetPasswordData"):
        assert op in denied and op in BLOCKED_READS
