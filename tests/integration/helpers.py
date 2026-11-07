"""
Helpers for integration tests.
"""

import time
from contextlib import contextmanager

import pytest

from dspace_api.exceptions import DSpaceError

UNSUPPORTED_STATUSES = (400, 404, 405, 422, 501)


def unsupported(reason):
    """Record the feature as not offered by this server (not a failure)."""
    pytest.skip(f"unsupported: {reason}")


@contextmanager
def unsupported_on(*statuses, what="endpoint"):
    """Turn the given HTTP statuses (default 400/404/405/422/501) into 'unsupported'."""
    statuses = statuses or UNSUPPORTED_STATUSES
    try:
        yield
    except DSpaceError as exc:
        if getattr(exc, "status_code", None) in statuses:
            unsupported(f"{what} answered HTTP {exc.status_code}")
        raise


def eventually(fn, timeout=120, interval=3, message="condition not met"):
    """Poll fn() until it returns something truthy (e.g. for Solr indexing)."""
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"{message} within {timeout}s")
        time.sleep(interval)


def values(dso, field):
    return [v.get("value") for v in (dso.get("metadata") or {}).get(field) or []]


