"""Checks against the fictional account seeded in moto (same seed as the demo)."""

import boto3
import pytest
from moto import mock_aws

from evidence_kit import checks
from evidence_kit.checks import Context, Recorder
from evidence_kit.demo import REGION, seed
from evidence_kit.guard import guard_session


@pytest.fixture(scope="module")
def account():
    with mock_aws(config={"iam": {"load_aws_managed_policies": True}}):
        seed(boto3.Session(region_name=REGION))
        yield guard_session(boto3.Session(region_name=REGION))


def run(fn, session):
    rec = Recorder()
    facts = fn(session, Context(region=REGION), rec)
    return facts, rec


def test_network_counts_azs_subnet_kinds_and_nat(account):
    facts, rec = run(checks.check_network, account)
    assert rec.errors == []
    vpc = next(v for v in facts["vpcs"] if v["name"] == "fieldwork-prod")
    assert vpc["azs"] == ["us-east-1a", "us-east-1b", "us-east-1c"]
    kinds = sorted(s["kind"] for s in vpc["subnets"])
    assert kinds == ["private-nat"] * 3 + ["public"] * 3
    assert vpc["nat_gateway_azs"] == ["us-east-1a"]


def test_containers_reports_public_endpoint_logs_and_nodegroup_azs(account):
    facts, rec = run(checks.check_containers, account)
    assert rec.errors == []
    (c,) = facts["eks_clusters"]
    assert c["endpoint_public_access"] is True
    assert c["public_access_cidrs"] == ["0.0.0.0/0"]
    assert c["endpoint_private_access"] is False
    assert "audit" in c["control_plane_logs"]
    assert c["nodegroups"][0]["azs"] == ["us-east-1a", "us-east-1b", "us-east-1c"]
    assert facts["ecs_clusters"] == []


def test_databases_reports_multi_az_and_retention(account):
    facts, _ = run(checks.check_databases, account)
    (db,) = facts["db_instances"]
    assert db["multi_az"] is False
    assert db["backup_retention_days"] == 7
    assert db["storage_encrypted"] is True
    assert db["publicly_accessible"] is False
    assert db["deletion_protection"] is True


def test_iam_finds_direct_and_group_admins_and_oidc_roles(account):
    facts, rec = run(checks.check_iam, account)
    assert rec.errors == []
    users = {u["name"]: u for u in facts["users"]}
    assert users["vendor-deploy"]["admin_via"] == ["attached:AdministratorAccess"]
    assert users["admin-temp"]["admin_via"] == ["group:admins:AdministratorAccess"]
    assert users["admin-temp"]["console_password"] is True
    assert users["admin-temp"]["mfa_devices"] == 0
    assert users["readonly-auditor"]["admin"] is False
    key = users["vendor-deploy"]["access_keys"][0]
    assert len(key["key_id_suffix"]) == 4  # full key ids never stored
    roles = {r["role"] for r in facts["oidc_federated_roles"]}
    assert roles == {"github-deploy-staging", "fieldwork-api-irsa"}


def test_cloudtrail_status(account):
    facts, rec = run(checks.check_cloudtrail, account)
    assert rec.errors == []
    (t,) = facts["trails"]
    assert t["is_logging"] and t["multi_region"] and t["log_file_validation"]


def test_waf_lists_public_alb_without_web_acl(account):
    facts, rec = run(checks.check_waf, account)
    assert rec.errors == []
    assert facts["regional_web_acls"] == []
    assert facts["public_albs"][0]["name"] == "fieldwork-public"
    assert facts["public_albs"][0]["web_acl"] is None


def test_tagging_lists_missing_keys(account):
    facts, rec = run(checks.check_tagging, account)
    assert rec.errors == []
    missing = {(r["type"], r["id"]): r["missing"] for r in facts["missing_any"]}
    assert missing[("rds:db", "fieldwork-prod-db")] == ["cost-center"]
    assert missing[("s3:bucket", "acme-fieldwork-cloudtrail-logs")] == ["env", "owner", "cost-center"]
    assert ("eks:cluster", "fieldwork-prod") not in missing


def test_backups_plan_rules(account):
    facts, _ = run(checks.check_backups, account)
    (plan,) = facts["backup_plans"]
    assert plan["rules"][0]["delete_after_days"] == 35
