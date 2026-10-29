"""Offline demo: a fictional account simulated in memory with moto.

"Acme Fieldwork, Inc." is fictional. The seeded environment mirrors the
sample report in sample-report/, so the evidence summary produced by the
demo lines up with the findings there. No real AWS endpoint is contacted.
"""

from __future__ import annotations

import json
import os

REGION = "us-east-1"
ACCOUNT_TAGS = {"env": "prod", "owner": "platform"}


def _fake_credentials() -> None:
    # moto intercepts every call; these values only satisfy botocore's signer.
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        os.environ[k] = v
    for k in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        os.environ.pop(k, None)


def _tags(d: dict) -> list[dict]:
    return [{"Key": k, "Value": v} for k, v in d.items()]


def seed(session) -> None:
    """Create the fictional 'as delivered' environment (writes go to moto only)."""
    ec2 = session.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16", TagSpecifications=[{"ResourceType": "vpc", "Tags": _tags({"Name": "fieldwork-prod"})}])["Vpc"]["VpcId"]
    igw = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
    public_rt = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=public_rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)

    public, private = {}, {}
    for i, az in enumerate(("a", "b", "c")):
        zone = f"{REGION}{az}"
        public[az] = ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.20.{i}.0/24", AvailabilityZone=zone)["Subnet"]["SubnetId"]
        private[az] = ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.20.{10 + i}.0/24", AvailabilityZone=zone)["Subnet"]["SubnetId"]
        ec2.associate_route_table(RouteTableId=public_rt, SubnetId=public[az])

    # Delivered: one NAT gateway for all three private subnets (SOW asked for one per AZ).
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    nat = ec2.create_nat_gateway(SubnetId=public["a"], AllocationId=eip)["NatGateway"]["NatGatewayId"]
    private_rt = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=private_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat)
    for sid in private.values():
        ec2.associate_route_table(RouteTableId=private_rt, SubnetId=sid)

    # EKS: running, logs on, but API endpoint open to the internet.
    eks = session.client("eks", region_name=REGION)
    eks.create_cluster(
        name="fieldwork-prod",
        version="1.30",
        roleArn="arn:aws:iam::123456789012:role/fieldwork-eks-cluster",
        resourcesVpcConfig={
            "subnetIds": list(private.values()),
            "endpointPublicAccess": True,
            "endpointPrivateAccess": False,
            "publicAccessCidrs": ["0.0.0.0/0"],
        },
        logging={"clusterLogging": [{"types": ["api", "audit", "authenticator"], "enabled": True}]},
        tags={**ACCOUNT_TAGS, "cost-center": "cc-210"},
    )
    eks.create_nodegroup(
        clusterName="fieldwork-prod",
        nodegroupName="general",
        subnets=list(private.values()),
        nodeRole="arn:aws:iam::123456789012:role/fieldwork-eks-node",
        scalingConfig={"minSize": 3, "maxSize": 6, "desiredSize": 3},
        instanceTypes=["m6i.large"],
    )

    # RDS: encrypted, private, deletion protection, but single-AZ and 7-day retention.
    rds = session.client("rds", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="fieldwork-prod-db",
        DBInstanceClass="db.m6g.large",
        Engine="postgres",
        MasterUsername="app_admin",
        MasterUserPassword="not-a-real-password",
        AllocatedStorage=100,
        MultiAZ=False,
        BackupRetentionPeriod=7,
        StorageEncrypted=True,
        PubliclyAccessible=False,
        DeletionProtection=True,
        Tags=_tags(ACCOUNT_TAGS),
    )

    backup = session.client("backup", region_name=REGION)
    backup.create_backup_plan(
        BackupPlan={
            "BackupPlanName": "fieldwork-daily",
            "Rules": [
                {
                    "RuleName": "daily-35d",
                    "TargetBackupVaultName": "Default",
                    "ScheduleExpression": "cron(0 5 * * ? *)",
                    "Lifecycle": {"DeleteAfterDays": 35},
                }
            ],
        }
    )

    # IAM: GitHub OIDC role for CI and an IRSA role (good), two admin users (gap).
    iam = session.client("iam", region_name=REGION)
    iam.create_open_id_connect_provider(Url="https://token.actions.githubusercontent.com", ClientIDList=["sts.amazonaws.com"], ThumbprintList=["0" * 40])
    issuer = eks.describe_cluster(name="fieldwork-prod")["cluster"]["identity"]["oidc"]["issuer"].replace("https://", "")
    iam.create_open_id_connect_provider(Url=f"https://{issuer}", ClientIDList=["sts.amazonaws.com"], ThumbprintList=["1" * 40])
    for role, provider in (("github-deploy-staging", "token.actions.githubusercontent.com"), ("fieldwork-api-irsa", issuer)):
        iam.create_role(
            RoleName=role,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": f"arn:aws:iam::123456789012:oidc-provider/{provider}"},
                            "Action": "sts:AssumeRoleWithWebIdentity",
                        }
                    ],
                }
            ),
        )
    admin = "arn:aws:iam::aws:policy/AdministratorAccess"
    iam.create_user(UserName="vendor-deploy")
    iam.attach_user_policy(UserName="vendor-deploy", PolicyArn=admin)
    iam.create_access_key(UserName="vendor-deploy")
    iam.create_group(GroupName="admins")
    iam.attach_group_policy(GroupName="admins", PolicyArn=admin)
    iam.create_user(UserName="admin-temp")
    iam.add_user_to_group(GroupName="admins", UserName="admin-temp")
    iam.create_login_profile(UserName="admin-temp", Password="Not-a-real-password-1!")
    iam.create_user(UserName="readonly-auditor")
    iam.attach_user_policy(UserName="readonly-auditor", PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess")

    # CloudTrail: multi-region, validation on, logging.
    s3 = session.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="acme-fieldwork-cloudtrail-logs")
    ct = session.client("cloudtrail", region_name=REGION)
    ct.create_trail(Name="acme-account-trail", S3BucketName="acme-fieldwork-cloudtrail-logs", IsMultiRegionTrail=True, EnableLogFileValidation=True)
    ct.start_logging(Name="acme-account-trail")

    # Public ALB with no web ACL (WAF was in the SOW).
    elb = session.client("elbv2", region_name=REGION)
    elb.create_load_balancer(Name="fieldwork-public", Subnets=list(public.values()), Scheme="internet-facing", Type="application", Tags=_tags(ACCOUNT_TAGS))


def run_demo(out_dir, only=None):
    _fake_credentials()
    import boto3
    from moto import mock_aws

    from .checks import Context
    from .guard import guard_session
    from .runner import run_checks, write_bundle
    import datetime as dt

    with mock_aws(config={"iam": {"load_aws_managed_policies": True}}):
        seed(boto3.Session(region_name=REGION))
        session = guard_session(boto3.Session(region_name=REGION))
        started = dt.datetime.now(dt.timezone.utc)
        results = run_checks(session, Context(region=REGION), only=only)
        meta = {
            "mode": "demo (moto, fictional account)",
            "region": REGION,
            "started_at": started,
            "profile": None,
            "notes": [
                "Demo note: moto does not simulate CloudTrail LookupEvents, AWS Backup selections or the "
                "Well-Architected Tool, so those calls show as NOT COLLECTED here. They are covered by "
                "botocore Stubber tests in tests/test_checks_stubber.py.",
            ],
        }
        return write_bundle(results, out_dir, meta), results
