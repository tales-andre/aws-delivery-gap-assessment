"""Checks that moto does not simulate, tested with botocore Stubber (canned responses, no network)."""

import datetime as dt

import boto3
import pytest
from botocore.stub import ANY, Stubber

from evidence_kit import checks
from evidence_kit.checks import Context, Recorder
from evidence_kit.guard import guard_client
from evidence_kit.runner import run_checks

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)


class StubSession:
    """Looks like a boto3 Session but hands out pre-built, stubbed, guarded clients."""

    def __init__(self, *services):
        self.clients = {s: guard_client(boto3.client(s, region_name="us-east-1")) for s in services}
        self.stubs = {s: Stubber(c) for s, c in self.clients.items()}

    def client(self, name, region_name=None):
        return self.clients[name]

    def __enter__(self):
        for s in self.stubs.values():
            s.activate()
        return self

    def __exit__(self, *exc):
        for s in self.stubs.values():
            s.assert_no_pending_responses()
            s.deactivate()


def test_change_events_records_who_did_what():
    ctx = Context(region="us-east-1", change_events=("CreateDBInstance", "ModifyDBInstance"), now=NOW)
    with StubSession("cloudtrail") as ss:
        ss.stubs["cloudtrail"].add_response(
            "lookup_events",
            {
                "Events": [
                    {
                        "EventId": "1",
                        "EventName": "CreateDBInstance",
                        "EventTime": dt.datetime(2026, 8, 3, 9, 15, tzinfo=dt.timezone.utc),
                        "EventSource": "rds.amazonaws.com",
                        "Username": "vendor-deploy",
                        "Resources": [{"ResourceType": "AWS::RDS::DBInstance", "ResourceName": "fieldwork-prod-db"}],
                    }
                ]
            },
            {
                "LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": "CreateDBInstance"}],
                "StartTime": NOW - dt.timedelta(days=90),
                "EndTime": NOW,
            },
        )
        ss.stubs["cloudtrail"].add_response("lookup_events", {"Events": []}, None)
        rec = Recorder()
        facts = checks.check_change_events(ss, ctx, rec)
    assert rec.errors == []
    (event,) = facts["events"]
    assert event["user"] == "vendor-deploy"
    assert event["resources"] == ["fieldwork-prod-db"]


def test_change_events_window_is_capped_at_90_days():
    ctx = Context(region="us-east-1", change_events=("CreateTrail",), lookback_days=400, now=NOW)
    with StubSession("cloudtrail") as ss:
        ss.stubs["cloudtrail"].add_response(
            "lookup_events",
            {"Events": []},
            {"LookupAttributes": ANY, "StartTime": NOW - dt.timedelta(days=90), "EndTime": NOW},
        )
        facts = checks.check_change_events(ss, ctx, Recorder())
    assert facts["window_start"] == NOW - dt.timedelta(days=90)


def test_well_architected_workloads_and_milestones():
    with StubSession("wellarchitected") as ss:
        ss.stubs["wellarchitected"].add_response(
            "list_workloads",
            {
                "WorkloadSummaries": [
                    {
                        "WorkloadId": "a" * 32,
                        "WorkloadName": "fieldwork-prod",
                        "UpdatedAt": NOW,
                        "Lenses": ["wellarchitected"],
                        "RiskCounts": {"HIGH": 4, "MEDIUM": 9},
                    }
                ]
            },
            None,
        )
        ss.stubs["wellarchitected"].add_response(
            "list_milestones",
            {"WorkloadId": "a" * 32, "MilestoneSummaries": [{"MilestoneNumber": 1, "MilestoneName": "handover", "RecordedAt": NOW}]},
            {"WorkloadId": "a" * 32},
        )
        facts = checks.check_well_architected(ss, Context(region="us-east-1"), Recorder())
    (w,) = facts["workloads"]
    assert w["risk_counts"] == {"HIGH": 4, "MEDIUM": 9}
    assert w["milestones"][0]["name"] == "handover"


def test_backup_selections_are_listed():
    with StubSession("backup") as ss:
        st = ss.stubs["backup"]
        st.add_response("list_backup_plans", {"BackupPlansList": [{"BackupPlanId": "p1", "BackupPlanName": "daily"}]}, None)
        st.add_response(
            "get_backup_plan",
            {"BackupPlan": {"BackupPlanName": "daily", "Rules": [{"RuleName": "r", "TargetBackupVaultName": "Default", "Lifecycle": {"DeleteAfterDays": 35}}]}},
            {"BackupPlanId": "p1"},
        )
        st.add_response(
            "list_backup_selections",
            {"BackupSelectionsList": [{"SelectionName": "rds-prod", "BackupPlanId": "p1"}]},
            {"BackupPlanId": "p1"},
        )
        facts = checks.check_backups(ss, Context(region="us-east-1"), Recorder())
    assert facts["backup_plans"][0]["selections"] == ["rds-prod"]


def test_waf_reports_managed_rule_groups_and_association():
    alb_arn = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/web/abc"
    acl_arn = "arn:aws:wafv2:us-east-1:111122223333:regional/webacl/edge/1"
    with StubSession("wafv2", "elbv2") as ss:
        ss.stubs["wafv2"].add_response(
            "list_web_acls", {"WebACLs": [{"Name": "edge", "Id": "1", "ARN": acl_arn, "LockToken": "t"}]}, {"Scope": "REGIONAL"}
        )
        ss.stubs["wafv2"].add_response(
            "get_web_acl",
            {
                "WebACL": {
                    "Name": "edge",
                    "Id": "1",
                    "ARN": acl_arn,
                    "DefaultAction": {"Allow": {}},
                    "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "edge"},
                    "Rules": [
                        {
                            "Name": "common",
                            "Priority": 0,
                            "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesCommonRuleSet"}},
                            "OverrideAction": {"None": {}},
                            "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "common"},
                        }
                    ],
                },
                "LockToken": "t",
            },
            {"Name": "edge", "Scope": "REGIONAL", "Id": "1"},
        )
        ss.stubs["elbv2"].add_response(
            "describe_load_balancers",
            {"LoadBalancers": [{"LoadBalancerArn": alb_arn, "LoadBalancerName": "web", "Scheme": "internet-facing", "Type": "application"}]},
            None,
        )
        ss.stubs["wafv2"].add_response(
            "get_web_acl_for_resource",
            {
                "WebACL": {
                    "Name": "edge",
                    "Id": "1",
                    "ARN": acl_arn,
                    "DefaultAction": {"Allow": {}},
                    "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "edge"},
                }
            },
            {"ResourceArn": alb_arn},
        )
        facts = checks.check_waf(ss, Context(region="us-east-1"), Recorder())
    assert facts["regional_web_acls"][0]["rules"] == [{"name": "common", "managed_group": "AWSManagedRulesCommonRuleSet"}]
    assert facts["public_albs"][0]["web_acl"] == "edge"


def test_access_denied_is_recorded_as_not_collected_never_as_a_finding():
    with StubSession("iam") as ss:
        for op in ("list_users", "list_open_id_connect_providers", "list_roles"):
            ss.stubs["iam"].add_client_error(op, service_error_code="AccessDenied", http_status_code=403)
        (result,) = run_checks(ss, Context(region="us-east-1"), only=["iam"])
    assert result["collection_status"] == "not_collected"
    assert len(result["errors"]) == 3
    assert all("AccessDenied" in e["error"] for e in result["errors"])


def test_admin_detection_from_customer_managed_policy_document():
    assert checks._is_admin_document({"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]})
    assert checks._is_admin_document({"Statement": {"Effect": "Allow", "Action": ["*"], "Resource": ["*"]}})
    assert not checks._is_admin_document({"Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]})
    assert not checks._is_admin_document({"Statement": [{"Effect": "Deny", "Action": "*", "Resource": "*"}]})
