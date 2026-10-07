"""Profile-authenticated access for the fixed Hetzner experiment."""

from collections.abc import Generator
from typing import TypedDict
from urllib.parse import urlsplit

import httpx
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.session import Session

from http_access import Transport

ENDPOINT = "https://fsn1.your-objectstorage.com"


class S3Auth(httpx.Auth):
    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        signed = AWSRequest(
            method=request.method, url=str(request.url), headers=dict(request.headers)
        )
        S3SigV4Auth(self.credentials.get_frozen_credentials(), "s3", "fsn1").add_auth(
            signed
        )
        request.headers.update(dict(signed.headers))
        yield request


def object_root(root: str) -> str:
    parsed = urlsplit(root)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.username
        or parsed.port
        or parsed.query
        or parsed.fragment
        or "?" in root
        or "#" in root
        or any(part in (".", "..", "") for part in parsed.path.strip("/").split("/"))
        or "%" in root
        or "\\" in root
        or any(c.isspace() for c in root)
        or parsed.path.startswith("//")
    ):
        raise ValueError("canonical s3://bucket/prefix/ root required")
    return ENDPOINT + "/" + parsed.netloc + parsed.path.rstrip("/")


class ReceiptObject(TypedDict):
    key: str
    size_bytes: int
    etag: str
    sha256: str
    full_remote_sha256_verified: bool


class CandidateReceipt(TypedDict):
    root: str
    objects: list[ReceiptObject]


class Receipt(TypedDict):
    status: str
    endpoint: str
    bucket: str
    baseline: list[ReceiptObject]
    candidates: list[CandidateReceipt]


def receipt_identities(receipt: Receipt, root: str) -> dict[str, tuple[int, str]]:
    """Use uploaded full-verification evidence, not a new download or ETag hash."""
    url = object_root(root)
    if receipt["status"] != "passed" or receipt["endpoint"].rstrip("/") != ENDPOINT:
        raise ValueError("unverified or different receipt endpoint")
    prefix = url.removeprefix(ENDPOINT + "/" + receipt["bucket"] + "/") + "/"
    groups = [receipt["baseline"]] + [
        candidate["objects"] for candidate in receipt["candidates"]
    ]
    objects = [
        item for group in groups for item in group if item["key"].startswith(prefix)
    ]
    if not objects or any(not item["full_remote_sha256_verified"] for item in objects):
        raise ValueError("root absent from fully verified receipt")
    return {
        item["key"].removeprefix(prefix): (item["size_bytes"], item["etag"])
        for item in objects
    }


def cloud_transport(root: str, receipt: Receipt) -> Transport:
    """Only botocore credential resolution/signing; HTTPX owns every data request."""
    identities = receipt_identities(receipt, root)
    session = Session(profile="hetzner-pourpoint")
    credentials = session.get_credentials()
    if credentials is None:
        raise ValueError("profile credentials unavailable")
    client = httpx.Client(
        auth=S3Auth(credentials),
        transport=httpx.HTTPTransport(
            retries=0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        ),
        timeout=60,
        trust_env=False,
        follow_redirects=False,
    )
    return Transport(object_root(root), client=client, identities=identities)


class Artifact(TypedDict):
    size_bytes: int
    sha256: str


class InheritedProvenance(TypedDict):
    source_artifacts: dict[str, Artifact]
    candidate_manifests: dict[str, Artifact]
    candidate_consumed_artifacts: dict[str, dict[str, Artifact]]


def validate_receipt(
    receipt: Receipt, source: str, candidates: str, inherited: InheritedProvenance
) -> None:
    """Bind cloud receipt to the unchanged original independently checked inputs."""
    expected = {"0.3.0": inherited["source_artifacts"]}
    for kind in ("a-index", "b-arrow"):
        expected[kind] = dict(
            inherited["candidate_consumed_artifacts"][kind],
            **{"manifest.json": inherited["candidate_manifests"][kind]},
        )
    all_objects = receipt["baseline"] + [
        item for candidate in receipt["candidates"] for item in candidate["objects"]
    ]
    for kind, artifacts in expected.items():
        root = source if kind == "0.3.0" else candidates + "/candidate-" + kind
        receipt_identities(receipt, root)
        prefix = urlsplit(root).path.strip("/") + "/"
        actual = {
            item["key"].removeprefix(prefix): item
            for item in all_objects
            if item["key"].startswith(prefix)
        }
        for name, expected_artifact in artifacts.items():
            if name not in actual or any(
                actual[name][key] != expected_artifact[key]
                for key in ("size_bytes", "sha256")
            ):
                raise ValueError("receipt differs from original input provenance")
