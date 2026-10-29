import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from evidence_kit.guard import ReadOnlyViolation, guard_client, guard_session, is_allowed


@pytest.mark.parametrize("op", ["DescribeDBInstances", "ListUsers", "GetTrailStatus", "LookupEvents", "GetBucketTagging"])
def test_read_operations_are_allowed(op):
    assert is_allowed(op)


@pytest.mark.parametrize(
    "op",
    ["CreateBucket", "PutObject", "DeleteDBInstance", "AttachUserPolicy", "UpdateClusterConfig", "StopLogging", "GenerateCredentialReport"],
)
def test_write_operations_are_blocked(op):
    assert not is_allowed(op)


@pytest.mark.parametrize("op", ["GetObject", "GetSecretValue", "GetParameter", "GetItem", "GetLogEvents"])
def test_data_reads_are_blocked(op):
    assert not is_allowed(op)


def test_guarded_session_never_sends_a_write():
    with mock_aws():
        session = guard_session(boto3.Session(region_name="us-east-1"))
        s3 = session.client("s3")
        with pytest.raises(ReadOnlyViolation):
            s3.create_bucket(Bucket="should-not-exist")
        # The call was stopped before the request: the bucket does not exist.
        assert boto3.client("s3", region_name="us-east-1").list_buckets()["Buckets"] == []


def test_guard_fires_before_a_stubbed_response():
    client = guard_client(boto3.client("rds", region_name="us-east-1"))
    with Stubber(client) as stub:
        stub.add_response("delete_db_instance", {"DBInstance": {"DBInstanceIdentifier": "x"}}, {"DBInstanceIdentifier": "x"})
        with pytest.raises(ReadOnlyViolation):
            client.delete_db_instance(DBInstanceIdentifier="x")


def test_reads_pass_through_the_guard():
    with mock_aws():
        session = guard_session(boto3.Session(region_name="us-east-1"))
        assert session.client("sts").get_caller_identity()["Account"]
