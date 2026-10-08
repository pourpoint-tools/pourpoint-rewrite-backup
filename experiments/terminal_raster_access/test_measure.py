import logging

from measure import Traffic


def test_counts_wire_requests_not_http2_diagnostics_and_includes_failures() -> None:
    traffic = Traffic()
    for message in [
        "CURL_INFO_TEXT: [HTTP/2] [1] [:method: HEAD]",
        "CURL_INFO_HEADER_OUT: HEAD /flow_dir.tif HTTP/2\r\nHost: public.example",
        "CURL_INFO_HEADER_IN: HTTP/2 200 ",
        "CURL_INFO_HEADER_OUT: GET /flow_dir.tif HTTP/2\r\nRange: bytes=0-9",
        "CURL_INFO_HEADER_IN: HTTP/2 503 ",
        "CURL_INFO_HEADER_OUT: GET /flow_dir.tif HTTP/2\r\nRange: bytes=0-9",
        "CURL_INFO_HEADER_IN: HTTP/2 206 ",
    ]:
        traffic.observe(message)
    assert traffic.snapshot() == {
        "requests": 3,
        "responses": 3,
        "failed_responses": 1,
        "response_bytes": "not measured",
    }


def test_root_handler_sees_open_and_read_channels() -> None:
    traffic = Traffic()
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(traffic)
    try:
        traffic.phase = "open"
        logging.getLogger("rasterio._env").debug("CURL_INFO_HEADER_OUT: HEAD /a HTTP/2")
        traffic.phase = "read"
        logging.getLogger("rasterio._err").debug("CURL_INFO_HEADER_OUT: GET /a HTTP/2")
        logging.getLogger("rasterio._err").debug("CURL_INFO_HEADER_IN: HTTP/2 503")
    finally:
        root.removeHandler(traffic)
        root.setLevel(old_level)
    assert traffic.requests == 2
    assert traffic.failed == 1
    assert [event["phase"] for event in traffic.events] == ["open", "read", "read"]
