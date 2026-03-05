"""Unit tests for pybox.daemon.protocol."""

from __future__ import annotations

import pytest

from pybox.daemon.protocol import (
    DaemonRequest,
    DaemonResponse,
    Method,
    decode_request,
    decode_response,
    encode,
)


class TestMethod:
    def test_all_methods_are_strings(self) -> None:
        for method in Method:
            assert isinstance(method.value, str)

    def test_expected_methods_exist(self) -> None:
        values = {m.value for m in Method}
        for expected in ("run", "ps", "inspect", "stop", "rm", "exec", "logs"):
            assert expected in values


class TestDaemonRequest:
    def test_valid_request(self) -> None:
        req = DaemonRequest(id=1, method=Method.PS, params={})
        assert req.id == 1
        assert req.method == Method.PS

    def test_default_params_is_empty_dict(self) -> None:
        req = DaemonRequest(id=2, method=Method.INSPECT)
        assert req.params == {}

    def test_invalid_method_raises(self) -> None:
        with pytest.raises(Exception):
            DaemonRequest(id=1, method="not_a_valid_method")  # type: ignore


class TestDaemonResponse:
    def test_ok_response(self) -> None:
        resp = DaemonResponse(id=1, result={"containers": []})
        assert resp.ok
        assert resp.error is None

    def test_error_response(self) -> None:
        resp = DaemonResponse(id=1, error="container not found")
        assert not resp.ok
        assert resp.error == "container not found"

    def test_result_can_be_none(self) -> None:
        resp = DaemonResponse(id=1, result=None)
        assert resp.ok


class TestEncodeDecodeRoundtrip:
    def test_request_encode_decode(self) -> None:
        req = DaemonRequest(id=42, method=Method.STOP, params={"container_id": "abc"})
        wire = encode(req)

        # First 4 bytes are length prefix
        assert len(wire) > 4
        import struct
        length = struct.unpack(">I", wire[:4])[0]
        assert length == len(wire) - 4

        decoded = decode_request(wire[4:])
        assert decoded.id == req.id
        assert decoded.method == req.method
        assert decoded.params == req.params

    def test_response_encode_decode(self) -> None:
        resp = DaemonResponse(id=7, result={"status": "ok", "count": 3})
        wire = encode(resp)
        decoded = decode_response(wire[4:])
        assert decoded.id == resp.id
        assert decoded.result == resp.result
        assert decoded.ok

    def test_error_response_roundtrip(self) -> None:
        resp = DaemonResponse(id=99, error="something went wrong")
        wire = encode(resp)
        decoded = decode_response(wire[4:])
        assert not decoded.ok
        assert decoded.error == "something went wrong"

    def test_various_result_types(self) -> None:
        for result in ([1, 2, 3], "hello", 42, True, None, {"a": "b"}):
            resp = DaemonResponse(id=1, result=result)
            wire = encode(resp)
            decoded = decode_response(wire[4:])
            assert decoded.result == result
