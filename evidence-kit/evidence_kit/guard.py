"""Read-only guard for boto3 sessions.

Every API call made through a guarded session is checked *before the request
is built*. Only operations whose names start with a read verb are allowed, and
a short list of read calls that return customer data or secrets is blocked too.
A blocked call raises ``ReadOnlyViolation`` and nothing is sent to AWS.

The guard is a second line of defence. The first line is the IAM role the
client creates for the reviewer (SecurityAudit / ReadOnlyAccess plus a deny on
data reads, see ``evidence-kit/iam/``).
"""

from __future__ import annotations

import boto3

READ_PREFIXES = ("Describe", "List", "Get", "Lookup", "Search")

# Read calls that can return customer data, secrets or decrypted values.
# The evidence kit never needs them, so they are blocked even though they are "reads".
BLOCKED_READS = frozenset(
    {
        "GetObject",
        "GetObjectAttributes",
        "GetObjectTorrent",
        "GetSecretValue",
        "BatchGetSecretValue",
        "GetParameter",
        "GetParameters",
        "GetParametersByPath",
        "GetItem",
        "GetRecords",
        "GetLogEvents",
        "GetQueryResults",
        "GetPasswordData",
        "GetConsoleOutput",
        "GetConsoleScreenshot",
        "GetSessionToken",
        "GetFederationToken",
    }
)


class ReadOnlyViolation(RuntimeError):
    """Raised when code tries to call an AWS operation that is not a plain read."""


def is_allowed(operation_name: str) -> bool:
    if operation_name in BLOCKED_READS:
        return False
    return operation_name.startswith(READ_PREFIXES)


def _check(model=None, **_kwargs):
    name = getattr(model, "name", None) or "<unknown>"
    if not is_allowed(name):
        service = getattr(getattr(model, "service_model", None), "service_name", "?")
        raise ReadOnlyViolation(
            f"Blocked {service}:{name}. The evidence kit only performs read operations."
        )


def guard_session(session: boto3.Session) -> boto3.Session:
    """Attach the guard to a boto3 session. Clients created afterwards inherit it."""
    session.events.register("before-parameter-build.*.*", _check, unique_id="evidence-kit-read-only-guard")
    return session


def guard_client(client):
    """Attach the guard to an already created client (used in tests with Stubber)."""
    client.meta.events.register("before-parameter-build.*.*", _check, unique_id="evidence-kit-read-only-guard")
    return client


def guarded_session(profile: str | None = None, region: str | None = None) -> boto3.Session:
    return guard_session(boto3.Session(profile_name=profile, region_name=region))
