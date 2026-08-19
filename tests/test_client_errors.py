"""Tests for ``py20305.client.errors`` helpers."""

from __future__ import annotations

import pytest

from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2Error,
    Sep2PayloadError,
    Sep2ProtocolError,
    compat_hint_suffix,
    is_2018_schema_validation_error,
)
from py20305.client.http import _parse_body
from py20305.models.sep.sep import DefaultDercontrol, DercontrolList


class TestIs2018SchemaValidationError:
    @pytest.mark.parametrize(
        "message",
        [
            "PUT /der returned 400: Invalid xml: The 'subscribable' attribute is not declared.",
            "POST /mup returned 400: 'schemaVer' attribute is not declared at line 1, column 42",
            "PUT /ders returned 400: invalid child element 'connectStatus' under DERStatus",
        ],
    )
    def test_recognizes_2018_signature(self, message: str) -> None:
        exc = Sep2ProtocolError(message, 400)
        assert is_2018_schema_validation_error(exc) is True

    def test_400_without_signature_is_not_2018(self) -> None:
        exc = Sep2ProtocolError("Bad Request: missing field", 400)
        assert is_2018_schema_validation_error(exc) is False

    def test_non_400_status_is_not_2018(self) -> None:
        # Even a "schemaVer" message at 500 is not the 2018 signature -- the
        # predicate is conservative on purpose so unrelated failures don't
        # carry the hint.
        exc = Sep2ProtocolError("'schemaVer' attribute is not declared", 500)
        assert is_2018_schema_validation_error(exc) is False

    def test_non_protocol_error_is_not_2018(self) -> None:
        exc = Sep2ConnectionError("network down")
        assert is_2018_schema_validation_error(exc) is False

    def test_arbitrary_exception_is_not_2018(self) -> None:
        assert is_2018_schema_validation_error(RuntimeError("boom")) is False


class TestCompatHintSuffix:
    def test_returns_hint_when_signature_matches_and_compat_off(self) -> None:
        exc = Sep2ProtocolError("'schemaVer' attribute is not declared", 400)
        suffix = compat_hint_suffix(exc, server_2018_compat=False)
        assert suffix.startswith(" -- ")
        assert "server_2018_compat=true" in suffix

    def test_empty_when_compat_already_on(self) -> None:
        """No point telling the operator to flip a flag they already flipped."""
        exc = Sep2ProtocolError("'schemaVer' attribute is not declared", 400)
        assert compat_hint_suffix(exc, server_2018_compat=True) == ""

    def test_empty_for_non_2018_error(self) -> None:
        exc = Sep2ProtocolError("Bad Request: missing field", 400)
        assert compat_hint_suffix(exc, server_2018_compat=False) == ""

    def test_empty_for_connection_error(self) -> None:
        exc = Sep2ConnectionError("network down")
        assert compat_hint_suffix(exc, server_2018_compat=False) == ""


class TestParseBodyPayloadErrors:
    """`_parse_body` is the boundary between aiohttp responses and the
    pydantic models. Any time the upstream server returns HTTP 200 with a
    body that can't be deserialized -- empty, malformed, wrong root --
    callers should see :class:`Sep2PayloadError` (a typed :class:`Sep2Error`
    subclass) instead of an opaque xsdata traceback. Modeled on the
    operator's report: a DERC/DDERC GET returned 200 with an empty payload
    and the aggregator logged a backtrace instead of a clean warning."""

    def test_empty_body_yields_payload_error_for_derc_list(self) -> None:
        """The headline case: server replied 200 to GET /derc with zero
        bytes. The aggregator must not propagate xsdata's ParserError."""
        with pytest.raises(Sep2PayloadError) as excinfo:
            _parse_body(b"", DercontrolList, "/edev/1/fsa/1/derp/1/derc")
        err = excinfo.value
        assert err.path == "/edev/1/fsa/1/derp/1/derc"
        assert err.body_length == 0
        # Path appears in the message so the operator's log line is
        # self-contained -- no need to correlate with neighbouring lines.
        assert "/edev/1/fsa/1/derp/1/derc" in str(err)
        assert "empty body" in str(err)

    def test_empty_body_yields_payload_error_for_dderc(self) -> None:
        """Same symptom on the DefaultDERControl singleton endpoint."""
        with pytest.raises(Sep2PayloadError) as excinfo:
            _parse_body(b"", DefaultDercontrol, "/edev/1/fsa/1/derp/1/dderc")
        assert "/edev/1/fsa/1/derp/1/dderc" in str(excinfo.value)

    def test_payload_error_is_a_sep2_error(self) -> None:
        """Polling code keys off the Sep2Error hierarchy; a typed parse
        error must fit that hierarchy or the warning catch in
        ``_poll_with_404_recovery`` won't fire."""
        with pytest.raises(Sep2Error):
            _parse_body(b"", DercontrolList, "/x")

    def test_malformed_xml_yields_payload_error(self) -> None:
        """Truncated / non-XML bodies also land here."""
        with pytest.raises(Sep2PayloadError) as excinfo:
            _parse_body(b"<oops", DercontrolList, "/x")
        err = excinfo.value
        assert err.body_length == len(b"<oops")
        # The original xsdata exception is preserved on the cause chain
        # so callers that want the underlying detail can still reach it,
        # without it being printed by default.
        assert excinfo.value.__cause__ is not None
