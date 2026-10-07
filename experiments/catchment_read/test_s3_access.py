"""Authenticated requests preserve the existing strict range contract."""

import httpx
import pytest
from botocore.credentials import Credentials

from http_access import Transport
from s3_access import S3Auth, object_root


def test_signed_pinned_range() -> None:
    seen: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.query == b""
        assert (
            request.headers["X-Amz-Content-Sha256"]
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        assert "Credential=test/" in request.headers["Authorization"]
        assert "/fsn1/s3/aws4_request" in request.headers["Authorization"]
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": "8", "ETag": '"v1"'})
        assert request.headers["If-Match"] == '"v1"'
        assert request.headers["Range"] == "bytes=2-4"
        assert "if-match" in request.headers["Authorization"]
        assert "range" in request.headers["Authorization"]
        return httpx.Response(
            206,
            content=b"cde",
            headers={
                "Content-Length": "3",
                "Content-Range": "bytes 2-4/8",
                "ETag": '"v1"',
            },
        )

    client = httpx.Client(
        auth=S3Auth(Credentials("test", "secret", "dummy-session-token")),
        transport=httpx.MockTransport(serve),
    )
    transport = Transport(object_root("s3://bucket/prefix/"), client=client)
    try:
        assert transport.file("data.arrow").fetch((2, 5)) == (2, b"cde")
        assert transport.io_counts() == {"data.arrow": {"reads": 2, "bytes": 3}}
        assert len(seen) == 2
        assert "secret" not in str(transport.events)
        assert "dummy-session-token" not in str(transport.events)
        assert "Signature=" not in str(transport.events)
    finally:
        transport.close()


@pytest.mark.parametrize(
    "root",
    [
        "https://example.com/data",
        "s3://bucket/path?secret=x",
        "s3://bucket/path#x",
        "s3://user:pass@bucket/path",
    ],
)
def test_reject_noncanonical_roots(root: str) -> None:
    with pytest.raises(ValueError):
        object_root(root)


@pytest.mark.parametrize("identity", [(9, '"v1"'), (8, '"other"')])
def test_receipt_mismatch_stops_before_get(identity: tuple[int, str]) -> None:
    seen: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, headers={"Content-Length": "8", "ETag": '"v1"'})

    transport = Transport(
        "https://example.test/prefix",
        client=httpx.Client(transport=httpx.MockTransport(serve)),
        identities={"data.arrow": identity},
    )
    try:
        with pytest.raises(ValueError, match="receipt identity"):
            transport.file("data.arrow")
        assert seen == ["HEAD"]
    finally:
        transport.close()


def test_failed_attempt_logged_without_exception_secrets() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("do-not-log-this-secret", request=request)

    transport = Transport(
        "https://example.test/prefix",
        client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    try:
        with pytest.raises(httpx.ReadTimeout):
            transport.request("data.arrow", "HEAD")
        assert len(transport.events) == 1
        assert transport.events[0]["error"] == "ReadTimeout"
        assert "do-not-log-this-secret" not in str(transport.events)
    finally:
        transport.close()


@pytest.mark.parametrize(
    "suffix", ["../data", "x/../data", "x//data", "x%2Fdata", "x\\data"]
)
def test_reject_ambiguous_paths(suffix: str) -> None:
    with pytest.raises(ValueError):
        object_root("s3://bucket/" + suffix)


def test_path_style_root() -> None:
    assert (
        object_root("s3://bucket/prefix/")
        == "https://fsn1.your-objectstorage.com/bucket/prefix"
    )


def test_receipt_binds_original_sha_and_requires_full_verification() -> None:
    from s3_access import (
        ENDPOINT,
        InheritedProvenance,
        Receipt,
        receipt_identities,
        validate_receipt,
    )

    receipt: Receipt = {
        "status": "passed",
        "endpoint": ENDPOINT,
        "bucket": "bucket",
        "baseline": [
            {
                "key": "source/manifest.json",
                "size_bytes": 8,
                "etag": '"v1"',
                "sha256": "source-sha",
                "full_remote_sha256_verified": True,
            }
        ],
        "candidates": [
            {
                "root": "s3://bucket/candidates/candidate-" + kind,
                "objects": [
                    {
                        "key": "candidates/candidate-" + kind + "/manifest.json",
                        "size_bytes": 9,
                        "etag": '"v2"',
                        "sha256": "candidate-sha",
                        "full_remote_sha256_verified": True,
                    }
                ],
            }
            for kind in ("a-index", "b-arrow")
        ],
    }
    inherited: InheritedProvenance = {
        "source_artifacts": {
            "manifest.json": {"size_bytes": 8, "sha256": "source-sha"}
        },
        "candidate_manifests": {
            kind: {"size_bytes": 9, "sha256": "candidate-sha"}
            for kind in ("a-index", "b-arrow")
        },
        "candidate_consumed_artifacts": {"a-index": {}, "b-arrow": {}},
    }
    validate_receipt(receipt, "s3://bucket/source", "s3://bucket/candidates", inherited)
    assert receipt_identities(receipt, "s3://bucket/source") == {
        "manifest.json": (8, '"v1"')
    }
    receipt["baseline"][0]["sha256"] = "wrong-sha"
    with pytest.raises(ValueError, match="original input provenance"):
        validate_receipt(
            receipt, "s3://bucket/source", "s3://bucket/candidates", inherited
        )
    receipt["baseline"][0]["full_remote_sha256_verified"] = False
    with pytest.raises(ValueError, match="fully verified"):
        receipt_identities(receipt, "s3://bucket/source")
    with pytest.raises(ValueError, match="fully verified"):
        receipt_identities(receipt, "s3://wrong-bucket/source")
