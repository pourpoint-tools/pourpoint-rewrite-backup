from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from range_server import RangeServer


def test_range_and_head(tmp_path: Path) -> None:
    (tmp_path / "data").write_bytes(b"0123456789")
    with RangeServer({"fixture": tmp_path}) as server:
        with urlopen(
            Request(
                server.url + "/fixture/data",
                headers={"Range": "bytes=2-5", "X-Request-ID": "slice"},
            )
        ) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"
        with urlopen(Request(server.url + "/fixture/data", method="HEAD")) as response:
            assert response.headers["Content-Length"] == "10"
            assert response.read() == b""
    events = server.events
    assert [event["payload_bytes"] for event in events] == [4, 0]
    assert events[0]["request_id"] == "slice"
    assert events[0]["range"] == "bytes=2-5"
    assert events[0]["status"] == 206
    assert events[0]["start_ns"] < events[0]["end_ns"]


def test_concurrent_delay_and_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import sleep
    from urllib.parse import urlsplit

    entered = Barrier(2, timeout=5)

    def synchronized_delay(seconds: float) -> None:
        entered.wait()  # Both handlers must be inside their measured interval.
        sleep(seconds)

    monkeypatch.setattr("range_server.sleep", synchronized_delay)

    path = tmp_path / "data"
    path.write_bytes(b"0123456789")
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        RangeServer({"file": path}, delay_seconds=0.1) as server,
    ):
        assert urlsplit(server.url).hostname == "127.0.0.1"

        def fetch(request_id: str) -> bytes:
            with urlopen(
                Request(server.url + "/file", headers={"X-Request-ID": request_id})
            ) as response:
                return response.read()

        futures = [pool.submit(fetch, str(i)) for i in range(2)]
        assert [future.result() for future in futures] == [b"0123456789"] * 2
    events = server.events
    assert len(events) == 2
    assert max(event["start_ns"] for event in events) < min(
        event["end_ns"] for event in events
    )
    assert all(event["end_ns"] - event["start_ns"] >= 100_000_000 for event in events)
    assert sum(event["payload_bytes"] for event in events) == 20


def test_security_and_invalid_ranges(tmp_path: Path) -> None:
    from urllib.error import HTTPError

    import pytest

    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_bytes(b"secret")
    (root / "escape").symlink_to(tmp_path / "secret")
    (root / "data").write_bytes(b"0123456789")
    with RangeServer({"fixture": root}) as server:
        for target in [
            "/",
            "/fixture/",
            "/fixture/../secret",
            "/fixture/%2e%2e/secret",
            "/fixture/escape",
            "/unknown/data",
        ]:
            with pytest.raises(HTTPError) as error:
                urlopen(server.url + target)
            assert error.value.code == 404
            error.value.close()
        for range_value in ["bytes=20-", "bytes=3-2", "bytes=0-1,3-4", "bytes=-0"]:
            with pytest.raises(HTTPError) as error:
                urlopen(
                    Request(
                        server.url + "/fixture/data", headers={"Range": range_value}
                    )
                )
            assert error.value.code == 416
            assert error.value.headers["Content-Range"] == "bytes */10"
            error.value.close()
        for range_value, expected in [
            ("bytes=-3", b"789"),
            ("bytes=8-", b"89"),
            ("bytes=8-99", b"89"),
        ]:
            with urlopen(
                Request(server.url + "/fixture/data", headers={"Range": range_value})
            ) as response:
                assert response.read() == expected
    assert all(
        event["payload_bytes"] == 0 for event in server.events if event["status"] >= 400
    )


def test_etag_and_if_match(tmp_path: Path) -> None:
    import os

    import httpx

    path = tmp_path / "data"
    path.write_bytes(b"0123456789")
    with RangeServer({"file": path}) as server, httpx.Client(timeout=2) as client:
        url = server.url + "/file"
        head = client.head(url)
        etag = head.headers["ETag"]
        assert etag.startswith('"') and etag.endswith('"')
        assert client.get(url).headers["ETag"] == etag
        for match in [etag, '"other", ' + etag, "*"]:
            response = client.get(
                url, headers={"If-Match": match, "Range": "bytes=2-5"}
            )
            assert response.status_code == 206
            assert response.content == b"2345"
            assert response.headers["ETag"] == etag
        for method in ["HEAD", "GET"]:
            for match in ['"stale"', "W/" + etag]:
                response = client.request(method, url, headers={"If-Match": match})
                assert response.status_code == 412
                assert response.headers["Content-Length"] == "0"
                assert response.content == b""
        before = path.stat()
        path.write_bytes(b"abcdefghij")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        assert client.head(url).headers["ETag"] != etag
        response = client.get(url, headers={"If-Match": etag, "Range": "bytes=99-"})
        assert response.status_code == 412  # Preconditions precede range evaluation.
        assert response.content == b""
    assert all(
        event["payload_bytes"] == 0 for event in server.events if event["status"] == 412
    )


def test_http11_reuses_connection_after_empty_errors(tmp_path: Path) -> None:
    import socket
    from http.server import BaseHTTPRequestHandler

    import httpx

    no_delay: list[int] = []

    class ObservedServer(RangeServer):
        def _handle(self, handler: BaseHTTPRequestHandler) -> None:
            no_delay.append(
                handler.connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            )
            super()._handle(handler)

    path = tmp_path / "data"
    path.write_bytes(b"0123456789")
    with ObservedServer({"file": path}) as server, httpx.Client(timeout=2) as client:
        url = server.url + "/file"
        first = client.get(url)
        assert first.http_version == "HTTP/1.1"
        connection = first.extensions["network_stream"]
        for target, headers, status in [
            (url + "/missing", {}, 404),
            (url, {"Range": "bytes=99-"}, 416),
            (url, {"If-Match": '"stale"'}, 412),
            (url, {}, 200),
        ]:
            response = client.get(target, headers=headers)
            assert response.status_code == status
            assert response.extensions["network_stream"] is connection
            assert response.content == (b"0123456789" if status == 200 else b"")
    assert len(no_delay) == 5
    assert all(no_delay)  # Enabled is nonzero; macOS can return 4 rather than 1.
