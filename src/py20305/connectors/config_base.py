"""The fields every device configuration carries, whatever its connector.

Separate from :mod:`py20305.connectors.config` so a connector package can
define its own configuration model without importing the union that lists it.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: An IEEE 2030.5 LFDI is a 160-bit value, written here as 40 hex characters.
_LFDI_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class _DeviceConfigBase(BaseModel):
    """Fields every device configuration carries, whatever its connector.

    Unknown keys are rejected rather than ignored: a misspelled setting that
    validates and then does nothing is the hardest kind of configuration
    mistake to find, because nothing reports it.
    """

    model_config = ConfigDict(extra="forbid")

    lfdi: str = Field(description="Device LFDI (40 hex characters)")
    description: str | None = None
    pin: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Expected IEEE 2030.5 registration PIN for this device. When set, "
            "the client verifies it against the EndDevice's Registration "
            "resource on the server during discovery and logs a warning on "
            "mismatch. Omit to skip verification."
        ),
    )
    role: Literal["der", "site_meter", "der_meter"] = Field(
        default="der",
        description=(
            "What the device measures. 'der' is a DER: its readings are mirrored "
            "with roleFlags 0x49 and it takes controls. 'site_meter' is the meter at "
            "the premises point of delivery, mirrored with roleFlags 0x03. "
            "'der_meter' is a meter on one DER's circuit, mirrored with roleFlags "
            "0x49. A meter takes no controls and has no DER resources."
        ),
    )
    revenue_quality: bool = Field(
        default=False,
        description="Set isRevenueQuality (0x10) in the MirrorUsagePoint roleFlags.",
    )
    mirror_under: Literal["site", "own"] = Field(
        default="site",
        description=(
            "site_meter only. The LFDI the meter's MirrorUsagePoint carries in "
            "deviceLFDI: 'site' uses this client's own EndDevice LFDI, which a server "
            "that accepts mirrors only for registered EndDevices needs; 'own' uses the "
            "meter's lfdi, the literal reading of deviceLFDI."
        ),
    )

    #: Fields the client uses itself and does not pass to the connector.
    _CLIENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"type", "lfdi", "description", "pin", "role", "revenue_quality", "mirror_under"}
    )

    @model_validator(mode="after")
    def _mirror_under_needs_a_site_meter(self) -> Self:
        """Reject ``mirror_under`` on a device that is not a site meter."""
        if "mirror_under" in self.model_fields_set and self.role != "site_meter":
            raise ValueError(
                f"mirror_under applies to role site_meter only; this device's role is {self.role}"
            )
        return self

    def connector_kwargs(self) -> dict[str, Any]:
        """Return the keyword arguments this device's connector is constructed with."""
        return self.model_dump(exclude=set(self._CLIENT_FIELDS))

    @field_validator("lfdi")
    @classmethod
    def _validate_lfdi(cls, v: str) -> str:
        """Reject a malformed LFDI at config load rather than at first poll.

        Without this, a typo -- an extra character, a stray quote, a
        non-hex digit -- loads fine and only fails later inside
        ``bytes.fromhex(lfdi)``, so the operator sees a
        ``non-hexadecimal number found`` warning every poll cycle with
        nothing pointing at the cause. Failing at load with a
        length-aware message is the actionable shape.

        The value is lowercased on return so downstream lookups have one
        normalization to rely on; certificate tools differ on hex case.
        """
        if not _LFDI_RE.fullmatch(v):
            # Tease apart the common failure modes for a useful message:
            # too short, too long, contains non-hex.
            if len(v) != 40:
                detail = f"length is {len(v)}, must be 40"
            else:
                bad = next((c for c in v if c not in "0123456789abcdefABCDEF"), "")
                detail = f"contains non-hex character {bad!r}" if bad else "not 40 hex chars"
            raise ValueError(f"lfdi must be 40 hex characters ({detail}). Got {v[:8]}...")
        return v.lower()


__all__ = ["_DeviceConfigBase"]
