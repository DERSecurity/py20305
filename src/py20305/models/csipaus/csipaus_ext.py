from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from xsdata_pydantic.fields import field

from py20305.models.sep.sep import (
    ActivePower,
    Link,
    Resource,
)

__NAMESPACE__ = "https://csipaus.org/ns"


class DoecontrolType(BaseModel):
    """
    Bitmap indicating the DOE controls implemented by the device.

    Bit positions SHALL be defined as follows: 0 - opModExpLimW (Maximum
    Imported Active Power) 1 - opModImpLimW (Maximum Exported Active Power)
    2 - opModGenLimW (Maximum Discharge Rate) 3 - opModLoadLimW (Maximum
    Charge Rate) All other values reserved.
    """

    class Meta:
        name = "DOEControlType"

    model_config = ConfigDict(defer_build=True)
    value: bytes = field(
        default=b"",
        metadata={
            "required": True,
            "max_length": 1,
            "format": "base16",
        },
    )


class ConnectionPointLink(Link):
    class Meta:
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class ConnectionPointType(Resource):
    """
    Contains identification information related to the network location at
    which the EndDevice is installed.

    :ivar connection_point_id: The identifier referring to the
        connection point. Typically the NMI.
    """

    model_config = ConfigDict(defer_build=True)
    connection_point_id: str = field(
        metadata={
            "name": "connectionPointId",
            "type": "Element",
            "namespace": "https://csipaus.org/ns",
            "required": True,
            "max_length": 32,
        }
    )


class DoeModesEnabled(DoecontrolType):
    class Meta:
        name = "doeModesEnabled"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class DoeModesSupported(DoecontrolType):
    class Meta:
        name = "doeModesSupported"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class OpModExpLimW(ActivePower):
    class Meta:
        name = "opModExpLimW"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class OpModGenLimW(ActivePower):
    class Meta:
        name = "opModGenLimW"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class OpModImpLimW(ActivePower):
    class Meta:
        name = "opModImpLimW"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class OpModLoadLimW(ActivePower):
    class Meta:
        name = "opModLoadLimW"
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)


class ConnectionPoint(ConnectionPointType):
    class Meta:
        namespace = "https://csipaus.org/ns"

    model_config = ConfigDict(defer_build=True)
