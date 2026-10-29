"""Read-only evidence checks.

Each check returns *facts* (what the AWS APIs say right now). Checks never
decide whether a contracted item is complete: that judgment belongs to the
reviewer, using the rubric in method.md.

If a call fails (missing permission, service not enabled, API not available),
the error is recorded next to the facts and the check is marked "partial" or
"not_collected". A failed call is never turned into a pass or a fail.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .guard import ReadOnlyViolation

DEFAULT_REQUIRED_TAGS = ("env", "owner", "cost-center")

# CloudTrail event names that usually prove (or disprove) delivery claims.
DEFAULT_CHANGE_EVENTS = (
    "CreateCluster",
    "UpdateClusterConfig",
    "CreateNodegroup",
    "CreateDBInstance",
    "ModifyDBInstance",
    "RestoreDBInstanceFromDBSnapshot",
    "RestoreDBInstanceToPointInTime",
    "StartRestoreJob",
    "CreateWebACL",
    "AssociateWebACL",
    "CreateTrail",
    "CreateUser",
    "AttachUserPolicy",
    "CreateAccessKey",
)


@dataclass
class Context:
    region: str
    required_tags: tuple[str, ...] = DEFAULT_REQUIRED_TAGS
    lookback_days: int = 90
    change_events: tuple[str, ...] = DEFAULT_CHANGE_EVENTS
    now: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class Recorder:
    """Runs one API call and records its error instead of crashing the check."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.calls = 0

    def call(self, label: str, fn: Callable[[], Any], default: Any = None) -> Any:
        self.calls += 1
        try:
            return fn()
        except ReadOnlyViolation:
            raise
        except (ClientError, BotoCoreError, NotImplementedError) as exc:
            self.errors.append({"call": label, "error": f"{type(exc).__name__}: {exc}"})
            return default


def paginate(client, operation: str, key: str, **kwargs) -> list:
    if client.can_paginate(operation):
        items: list = []
        for page in client.get_paginator(operation).paginate(**kwargs):
            items.extend(page.get(key, []))
        return items
    return getattr(client, operation)(**kwargs).get(key, [])


def _tags_to_dict(tags) -> dict[str, str]:
    if not tags:
        return {}
    if isinstance(tags, dict):
        return dict(tags)
    return {t.get("Key", t.get("key")): t.get("Value", t.get("value")) for t in tags}


# --------------------------------------------------------------------------- E-00
def check_identity(session, ctx: Context, rec: Recorder) -> dict:
    sts = session.client("sts", region_name=ctx.region)
    ident = rec.call("sts:GetCallerIdentity", sts.get_caller_identity, {}) or {}
    return {"account": ident.get("Account"), "caller_arn": ident.get("Arn"), "region": ctx.region}


# --------------------------------------------------------------------------- E-01
def check_network(session, ctx: Context, rec: Recorder) -> dict:
    ec2 = session.client("ec2", region_name=ctx.region)
    vpcs = rec.call("ec2:DescribeVpcs", lambda: paginate(ec2, "describe_vpcs", "Vpcs"), [])
    subnets = rec.call("ec2:DescribeSubnets", lambda: paginate(ec2, "describe_subnets", "Subnets"), [])
    tables = rec.call("ec2:DescribeRouteTables", lambda: paginate(ec2, "describe_route_tables", "RouteTables"), [])
    nats = rec.call("ec2:DescribeNatGateways", lambda: paginate(ec2, "describe_nat_gateways", "NatGateways"), [])

    # Which route table applies to each subnet (explicit association, else the VPC main table).
    explicit: dict[str, dict] = {}
    main: dict[str, dict] = {}
    for table in tables:
        for assoc in table.get("Associations", []):
            if assoc.get("Main"):
                main[table["VpcId"]] = table
            elif assoc.get("SubnetId"):
                explicit[assoc["SubnetId"]] = table

    def default_route(table: dict | None) -> str | None:
        for route in (table or {}).get("Routes", []):
            if route.get("DestinationCidrBlock") == "0.0.0.0/0":
                return route.get("GatewayId") or route.get("NatGatewayId") or route.get("TransitGatewayId")
        return None

    subnet_az = {s["SubnetId"]: s["AvailabilityZone"] for s in subnets}
    out = []
    for vpc in vpcs:
        vid = vpc["VpcId"]
        rows = []
        for s in (s for s in subnets if s["VpcId"] == vid):
            target = default_route(explicit.get(s["SubnetId"]) or main.get(vid))
            kind = "public" if (target or "").startswith("igw-") else ("private-nat" if (target or "").startswith("nat-") else "isolated")
            rows.append(
                {
                    "subnet_id": s["SubnetId"],
                    "az": s["AvailabilityZone"],
                    "cidr": s["CidrBlock"],
                    "default_route": target,
                    "kind": kind,
                    "name": _tags_to_dict(s.get("Tags")).get("Name"),
                }
            )
        vpc_nats = [n for n in nats if n.get("VpcId") == vid and n.get("State") not in ("deleted", "failed")]
        out.append(
            {
                "vpc_id": vid,
                "cidr": vpc.get("CidrBlock"),
                "is_default": vpc.get("IsDefault", False),
                "name": _tags_to_dict(vpc.get("Tags")).get("Name"),
                "azs": sorted({r["az"] for r in rows}),
                "subnets": sorted(rows, key=lambda r: (r["kind"], r["az"])),
                "nat_gateways": [
                    {"id": n["NatGatewayId"], "subnet_id": n.get("SubnetId"), "az": subnet_az.get(n.get("SubnetId")), "state": n.get("State")}
                    for n in vpc_nats
                ],
                "nat_gateway_azs": sorted({subnet_az.get(n.get("SubnetId")) for n in vpc_nats if subnet_az.get(n.get("SubnetId"))}),
            }
        )
    return {"vpcs": out}


# --------------------------------------------------------------------------- E-02
def check_containers(session, ctx: Context, rec: Recorder) -> dict:
    eks = session.client("eks", region_name=ctx.region)
    ec2 = session.client("ec2", region_name=ctx.region)
    ecs = session.client("ecs", region_name=ctx.region)

    subnets = rec.call("ec2:DescribeSubnets", lambda: paginate(ec2, "describe_subnets", "Subnets"), [])
    subnet_az = {s["SubnetId"]: s["AvailabilityZone"] for s in subnets}

    clusters = []
    for name in rec.call("eks:ListClusters", lambda: paginate(eks, "list_clusters", "clusters"), []):
        c = rec.call(f"eks:DescribeCluster {name}", lambda n=name: eks.describe_cluster(name=n)["cluster"], {}) or {}
        vpc_cfg = c.get("resourcesVpcConfig", {})
        enabled_logs = sorted(
            t for entry in c.get("logging", {}).get("clusterLogging", []) if entry.get("enabled") for t in entry.get("types", [])
        )
        nodegroups = []
        for ng_name in rec.call(f"eks:ListNodegroups {name}", lambda n=name: paginate(eks, "list_nodegroups", "nodegroups", clusterName=n), []):
            ng = rec.call(
                f"eks:DescribeNodegroup {ng_name}",
                lambda n=name, g=ng_name: eks.describe_nodegroup(clusterName=n, nodegroupName=g)["nodegroup"],
                {},
            ) or {}
            nodegroups.append(
                {
                    "name": ng_name,
                    "azs": sorted({subnet_az.get(s, "unknown") for s in ng.get("subnets", [])}),
                    "scaling": ng.get("scalingConfig"),
                    "instance_types": ng.get("instanceTypes"),
                }
            )
        clusters.append(
            {
                "name": name,
                "version": c.get("version"),
                "status": c.get("status"),
                "endpoint_public_access": vpc_cfg.get("endpointPublicAccess"),
                "endpoint_private_access": vpc_cfg.get("endpointPrivateAccess"),
                "public_access_cidrs": vpc_cfg.get("publicAccessCidrs"),
                "control_plane_logs": enabled_logs,
                "oidc_issuer": c.get("identity", {}).get("oidc", {}).get("issuer"),
                "created_at": c.get("createdAt"),
                "tags": c.get("tags", {}),
                "nodegroups": nodegroups,
            }
        )

    ecs_out = []
    arns = rec.call("ecs:ListClusters", lambda: paginate(ecs, "list_clusters", "clusterArns"), [])
    for i in range(0, len(arns), 100):
        described = rec.call("ecs:DescribeClusters", lambda chunk=arns[i : i + 100]: ecs.describe_clusters(clusters=chunk)["clusters"], [])
        for c in described:
            ecs_out.append(
                {
                    "name": c.get("clusterName"),
                    "status": c.get("status"),
                    "active_services": c.get("activeServicesCount"),
                    "running_tasks": c.get("runningTasksCount"),
                    "capacity_providers": c.get("capacityProviders", []),
                }
            )
    return {"eks_clusters": clusters, "ecs_clusters": ecs_out}


# --------------------------------------------------------------------------- E-03
def check_databases(session, ctx: Context, rec: Recorder) -> dict:
    rds = session.client("rds", region_name=ctx.region)
    instances = []
    for db in rec.call("rds:DescribeDBInstances", lambda: paginate(rds, "describe_db_instances", "DBInstances"), []):
        instances.append(
            {
                "id": db["DBInstanceIdentifier"],
                "engine": db.get("Engine"),
                "engine_version": db.get("EngineVersion"),
                "class": db.get("DBInstanceClass"),
                "status": db.get("DBInstanceStatus"),
                "multi_az": db.get("MultiAZ"),
                "backup_retention_days": db.get("BackupRetentionPeriod"),
                "storage_encrypted": db.get("StorageEncrypted"),
                "publicly_accessible": db.get("PubliclyAccessible"),
                "deletion_protection": db.get("DeletionProtection"),
                "cluster": db.get("DBClusterIdentifier"),
                "created_at": db.get("InstanceCreateTime"),
                "tags": _tags_to_dict(db.get("TagList")),
            }
        )
    clusters = []
    for c in rec.call("rds:DescribeDBClusters", lambda: paginate(rds, "describe_db_clusters", "DBClusters"), []):
        clusters.append(
            {
                "id": c["DBClusterIdentifier"],
                "engine": c.get("Engine"),
                "multi_az": c.get("MultiAZ"),
                "azs": c.get("AvailabilityZones", []),
                "members": len(c.get("DBClusterMembers", [])),
                "backup_retention_days": c.get("BackupRetentionPeriod"),
                "storage_encrypted": c.get("StorageEncrypted"),
                "deletion_protection": c.get("DeletionProtection"),
            }
        )
    return {"db_instances": instances, "db_clusters": clusters}


# --------------------------------------------------------------------------- E-04
def check_backups(session, ctx: Context, rec: Recorder) -> dict:
    backup = session.client("backup", region_name=ctx.region)
    plans = []
    for p in rec.call("backup:ListBackupPlans", lambda: paginate(backup, "list_backup_plans", "BackupPlansList"), []):
        pid = p["BackupPlanId"]
        detail = rec.call(f"backup:GetBackupPlan {pid}", lambda i=pid: backup.get_backup_plan(BackupPlanId=i)["BackupPlan"], {}) or {}
        selections = rec.call(
            f"backup:ListBackupSelections {pid}",
            lambda i=pid: paginate(backup, "list_backup_selections", "BackupSelectionsList", BackupPlanId=i),
            None,
        )
        plans.append(
            {
                "name": p.get("BackupPlanName"),
                "id": pid,
                "rules": [
                    {
                        "name": r.get("RuleName"),
                        "schedule": r.get("ScheduleExpression"),
                        "vault": r.get("TargetBackupVaultName"),
                        "delete_after_days": r.get("Lifecycle", {}).get("DeleteAfterDays"),
                    }
                    for r in detail.get("Rules", [])
                ],
                "selections": None if selections is None else [s.get("SelectionName") for s in selections],
            }
        )
    return {"backup_plans": plans}


# --------------------------------------------------------------------------- E-05
def _is_admin_document(doc) -> bool:
    statements = (doc or {}).get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for st in statements:
        if st.get("Effect") != "Allow":
            continue
        actions = st.get("Action", [])
        resources = st.get("Resource", [])
        actions = [actions] if isinstance(actions, str) else actions
        resources = [resources] if isinstance(resources, str) else resources
        if "*" in actions and "*" in resources:
            return True
    return False


def check_iam(session, ctx: Context, rec: Recorder) -> dict:
    iam = session.client("iam", region_name=ctx.region)
    admin_cache: dict[str, bool] = {}

    def policy_is_admin(arn: str) -> bool:
        if arn not in admin_cache:
            if arn.endswith(":policy/AdministratorAccess"):
                admin_cache[arn] = True
            else:
                pol = rec.call(f"iam:GetPolicy {arn}", lambda: iam.get_policy(PolicyArn=arn)["Policy"], {}) or {}
                ver = rec.call(
                    f"iam:GetPolicyVersion {arn}",
                    lambda: iam.get_policy_version(PolicyArn=arn, VersionId=pol["DefaultVersionId"])["PolicyVersion"],
                    {},
                ) if pol else {}
                admin_cache[arn] = _is_admin_document((ver or {}).get("Document"))
        return admin_cache[arn]

    users = []
    for u in rec.call("iam:ListUsers", lambda: paginate(iam, "list_users", "Users"), []):
        name = u["UserName"]
        admin_via: list[str] = []
        for p in rec.call(f"iam:ListAttachedUserPolicies {name}", lambda n=name: paginate(iam, "list_attached_user_policies", "AttachedPolicies", UserName=n), []):
            if policy_is_admin(p["PolicyArn"]):
                admin_via.append(f"attached:{p['PolicyName']}")
        for pname in rec.call(f"iam:ListUserPolicies {name}", lambda n=name: paginate(iam, "list_user_policies", "PolicyNames", UserName=n), []):
            doc = rec.call(f"iam:GetUserPolicy {name}/{pname}", lambda n=name, p=pname: iam.get_user_policy(UserName=n, PolicyName=p)["PolicyDocument"], {})
            if _is_admin_document(doc):
                admin_via.append(f"inline:{pname}")
        for g in rec.call(f"iam:ListGroupsForUser {name}", lambda n=name: paginate(iam, "list_groups_for_user", "Groups", UserName=n), []):
            gname = g["GroupName"]
            for p in rec.call(f"iam:ListAttachedGroupPolicies {gname}", lambda x=gname: paginate(iam, "list_attached_group_policies", "AttachedPolicies", GroupName=x), []):
                if policy_is_admin(p["PolicyArn"]):
                    admin_via.append(f"group:{gname}:{p['PolicyName']}")

        console = True
        try:
            rec.calls += 1
            iam.get_login_profile(UserName=name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchEntity":
                console = False
            else:
                rec.errors.append({"call": f"iam:GetLoginProfile {name}", "error": str(exc)})
                console = None

        mfa = rec.call(f"iam:ListMFADevices {name}", lambda n=name: paginate(iam, "list_mfa_devices", "MFADevices", UserName=n), None)
        keys = []
        for k in rec.call(f"iam:ListAccessKeys {name}", lambda n=name: paginate(iam, "list_access_keys", "AccessKeyMetadata", UserName=n), []):
            last = rec.call(
                "iam:GetAccessKeyLastUsed",
                lambda kid=k["AccessKeyId"]: iam.get_access_key_last_used(AccessKeyId=kid)["AccessKeyLastUsed"],
                {},
            ) or {}
            created = k.get("CreateDate")
            keys.append(
                {
                    "key_id_suffix": k["AccessKeyId"][-4:],  # never store full key ids in evidence
                    "status": k.get("Status"),
                    "created": created,
                    "age_days": (ctx.now - created).days if isinstance(created, dt.datetime) else None,
                    "last_used": last.get("LastUsedDate"),
                    "last_used_service": last.get("ServiceName"),
                }
            )
        users.append(
            {
                "name": name,
                "created": u.get("CreateDate"),
                "admin": bool(admin_via),
                "admin_via": admin_via,
                "console_password": console,
                "mfa_devices": None if mfa is None else len(mfa),
                "access_keys": keys,
            }
        )

    providers = [
        p["Arn"].split("oidc-provider/", 1)[-1]
        for p in rec.call("iam:ListOpenIDConnectProviders", lambda: iam.list_open_id_connect_providers()["OpenIDConnectProviderList"], [])
    ]
    federated_roles = []
    for r in rec.call("iam:ListRoles", lambda: paginate(iam, "list_roles", "Roles"), []):
        doc = r.get("AssumeRolePolicyDocument") or {}
        statements = doc.get("Statement", [])
        statements = [statements] if isinstance(statements, dict) else statements
        for st in statements:
            fed = (st.get("Principal") or {}).get("Federated")
            if fed and "oidc-provider/" in str(fed):
                federated_roles.append({"role": r["RoleName"], "oidc_provider": str(fed).split("oidc-provider/", 1)[-1]})
    return {"users": users, "oidc_providers": providers, "oidc_federated_roles": federated_roles}


# --------------------------------------------------------------------------- E-06
def check_cloudtrail(session, ctx: Context, rec: Recorder) -> dict:
    ct = session.client("cloudtrail", region_name=ctx.region)
    trails = []
    for t in rec.call("cloudtrail:DescribeTrails", lambda: ct.describe_trails(includeShadowTrails=True)["trailList"], []):
        status = rec.call(f"cloudtrail:GetTrailStatus {t['Name']}", lambda a=t["TrailARN"]: ct.get_trail_status(Name=a), {}) or {}
        trails.append(
            {
                "name": t["Name"],
                "home_region": t.get("HomeRegion"),
                "multi_region": t.get("IsMultiRegionTrail"),
                "organization_trail": t.get("IsOrganizationTrail"),
                "log_file_validation": t.get("LogFileValidationEnabled"),
                "s3_bucket": t.get("S3BucketName"),
                "kms_key_set": bool(t.get("KmsKeyId")),
                "is_logging": status.get("IsLogging"),
                "latest_delivery": status.get("LatestDeliveryTime"),
                "latest_delivery_error": status.get("LatestDeliveryError"),
            }
        )
    return {"trails": trails}


# --------------------------------------------------------------------------- E-07
def check_waf(session, ctx: Context, rec: Recorder) -> dict:
    waf = session.client("wafv2", region_name=ctx.region)
    elb = session.client("elbv2", region_name=ctx.region)
    acls = []
    for a in rec.call("wafv2:ListWebACLs", lambda: waf.list_web_acls(Scope="REGIONAL").get("WebACLs", []), []):
        acl = rec.call(f"wafv2:GetWebACL {a['Name']}", lambda x=a: waf.get_web_acl(Name=x["Name"], Scope="REGIONAL", Id=x["Id"])["WebACL"], {}) or {}
        rules = []
        for r in acl.get("Rules", []):
            managed = r.get("Statement", {}).get("ManagedRuleGroupStatement")
            rules.append({"name": r.get("Name"), "managed_group": managed.get("Name") if managed else None})
        acls.append({"name": a["Name"], "arn": a.get("ARN"), "rules": rules})

    public_albs = []
    for lb in rec.call("elbv2:DescribeLoadBalancers", lambda: paginate(elb, "describe_load_balancers", "LoadBalancers"), []):
        if lb.get("Scheme") != "internet-facing" or lb.get("Type") != "application":
            continue
        assoc = rec.call(
            f"wafv2:GetWebACLForResource {lb['LoadBalancerName']}",
            lambda arn=lb["LoadBalancerArn"]: waf.get_web_acl_for_resource(ResourceArn=arn).get("WebACL"),
            "error",
        )
        public_albs.append(
            {"name": lb["LoadBalancerName"], "dns": lb.get("DNSName"), "web_acl": None if assoc in (None, "error") else assoc.get("Name"), "lookup_failed": assoc == "error"}
        )
    return {"regional_web_acls": acls, "public_albs": public_albs}


# --------------------------------------------------------------------------- E-08
def check_tagging(session, ctx: Context, rec: Recorder) -> dict:
    required = list(ctx.required_tags)
    resources: list[dict] = []

    def add(kind: str, rid: str, tags: dict | None) -> None:
        if tags is None:
            resources.append({"type": kind, "id": rid, "missing": None, "note": "tags not readable"})
        else:
            resources.append({"type": kind, "id": rid, "missing": [k for k in required if k not in tags]})

    ec2 = session.client("ec2", region_name=ctx.region)
    for res in rec.call("ec2:DescribeInstances", lambda: paginate(ec2, "describe_instances", "Reservations"), []):
        for i in res.get("Instances", []):
            if i.get("State", {}).get("Name") != "terminated":
                add("ec2:instance", i["InstanceId"], _tags_to_dict(i.get("Tags")))
    for v in rec.call("ec2:DescribeVolumes", lambda: paginate(ec2, "describe_volumes", "Volumes"), []):
        add("ec2:volume", v["VolumeId"], _tags_to_dict(v.get("Tags")))

    rds = session.client("rds", region_name=ctx.region)
    for db in rec.call("rds:DescribeDBInstances", lambda: paginate(rds, "describe_db_instances", "DBInstances"), []):
        add("rds:db", db["DBInstanceIdentifier"], _tags_to_dict(db.get("TagList")))

    eks = session.client("eks", region_name=ctx.region)
    for name in rec.call("eks:ListClusters", lambda: paginate(eks, "list_clusters", "clusters"), []):
        c = rec.call(f"eks:DescribeCluster {name}", lambda n=name: eks.describe_cluster(name=n)["cluster"], None)
        add("eks:cluster", name, None if c is None else c.get("tags", {}))

    elb = session.client("elbv2", region_name=ctx.region)
    lbs = rec.call("elbv2:DescribeLoadBalancers", lambda: paginate(elb, "describe_load_balancers", "LoadBalancers"), [])
    for i in range(0, len(lbs), 20):
        chunk = lbs[i : i + 20]
        descs = rec.call("elbv2:DescribeTags", lambda c=chunk: elb.describe_tags(ResourceArns=[x["LoadBalancerArn"] for x in c])["TagDescriptions"], [])
        by_arn = {d["ResourceArn"]: _tags_to_dict(d.get("Tags")) for d in descs}
        for lb in chunk:
            add("elbv2:load-balancer", lb["LoadBalancerName"], by_arn.get(lb["LoadBalancerArn"]))

    s3 = session.client("s3", region_name=ctx.region)
    for b in rec.call("s3:ListBuckets", lambda: s3.list_buckets().get("Buckets", []), []):
        try:
            rec.calls += 1
            tags = _tags_to_dict(s3.get_bucket_tagging(Bucket=b["Name"]).get("TagSet"))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchTagSet":
                tags = {}
            else:
                rec.errors.append({"call": f"s3:GetBucketTagging {b['Name']}", "error": str(exc)})
                tags = None
        add("s3:bucket", b["Name"], tags)

    return {
        "required_tags": required,
        "resources": resources,
        "missing_any": [r for r in resources if r["missing"]],
        "unreadable": [r for r in resources if r["missing"] is None],
    }


# --------------------------------------------------------------------------- E-09
def check_change_events(session, ctx: Context, rec: Recorder) -> dict:
    """Who changed what, from CloudTrail Event history (management events, this region, up to 90 days)."""
    ct = session.client("cloudtrail", region_name=ctx.region)
    start = ctx.now - dt.timedelta(days=min(ctx.lookback_days, 90))
    events = []
    for name in ctx.change_events:
        found = rec.call(
            f"cloudtrail:LookupEvents {name}",
            lambda n=name: paginate(
                ct,
                "lookup_events",
                "Events",
                LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": n}],
                StartTime=start,
                EndTime=ctx.now,
            ),
            [],
        )
        for e in found:
            events.append(
                {
                    "time": e.get("EventTime"),
                    "event": e.get("EventName"),
                    "source": e.get("EventSource"),
                    "user": e.get("Username"),
                    "resources": [r.get("ResourceName") for r in e.get("Resources", [])],
                }
            )
    events.sort(key=lambda e: str(e["time"]))
    return {"window_start": start, "window_end": ctx.now, "event_names": list(ctx.change_events), "events": events}


# --------------------------------------------------------------------------- E-10
def check_well_architected(session, ctx: Context, rec: Recorder) -> dict:
    wa = session.client("wellarchitected", region_name=ctx.region)
    workloads = []
    for w in rec.call("wellarchitected:ListWorkloads", lambda: paginate(wa, "list_workloads", "WorkloadSummaries"), []):
        milestones = rec.call(
            f"wellarchitected:ListMilestones {w['WorkloadName']}",
            lambda i=w["WorkloadId"]: paginate(wa, "list_milestones", "MilestoneSummaries", WorkloadId=i),
            [],
        )
        workloads.append(
            {
                "name": w.get("WorkloadName"),
                "updated_at": w.get("UpdatedAt"),
                "lenses": w.get("Lenses", []),
                "risk_counts": w.get("RiskCounts", {}),
                "milestones": [{"name": m.get("MilestoneName"), "recorded_at": m.get("RecordedAt")} for m in milestones],
            }
        )
    return {"workloads": workloads}


@dataclass(frozen=True)
class Check:
    evidence_id: str
    key: str
    title: str
    fn: Callable


CHECKS: tuple[Check, ...] = (
    Check("E-00", "identity", "Account and caller identity", check_identity),
    Check("E-01", "network", "VPCs, subnets, routes and NAT gateways", check_network),
    Check("E-02", "containers", "EKS clusters, node groups and ECS clusters", check_containers),
    Check("E-03", "databases", "RDS instances and clusters", check_databases),
    Check("E-04", "backups", "AWS Backup plans", check_backups),
    Check("E-05", "iam", "IAM users, admin rights, MFA, keys and OIDC roles", check_iam),
    Check("E-06", "cloudtrail", "CloudTrail trails and logging status", check_cloudtrail),
    Check("E-07", "waf", "WAF web ACLs and public load balancers", check_waf),
    Check("E-08", "tagging", "Required tags on in-scope resources", check_tagging),
    Check("E-09", "change_events", "CloudTrail change events (who created or changed what)", check_change_events),
    Check("E-10", "well_architected", "Well-Architected Tool workloads and milestones", check_well_architected),
)
