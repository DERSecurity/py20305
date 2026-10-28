"""The CSIP-AUS 1.3 TimeTariffInterval, which carries a RateComponentLink.

csipaus-core-v1.3.xsd extends TimeTariffInterval with a mandatory
``RateComponentLink`` in the IEEE 2030.5 namespace. The generated model has no
such field, and its ``xs:any`` wildcard admits only other namespaces, so a 1.3
TimeTariffIntervalList does not parse into it. These hand-written subclasses add
the link as optional, because plain IEEE 2030.5 servers do not send it; the
generated models stay as the schemas define them.
"""

from __future__ import annotations

from pydantic import ConfigDict
from xsdata_pydantic.fields import field

from py20305.models.sep.sep import (
    RateComponentLink,
    TimeTariffInterval1,
    TimeTariffIntervalList,
)

__NAMESPACE__ = "urn:ieee:std:2030.5:ns"


class CsipausTimeTariffInterval(TimeTariffInterval1):
    """A TimeTariffInterval with the CSIP-AUS 1.3 RateComponentLink, when sent."""

    class Meta:
        name = "TimeTariffInterval"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    rate_component_link: None | RateComponentLink = field(
        default=None,
        metadata={
            "name": "RateComponentLink",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )


class CsipausTimeTariffIntervalList(TimeTariffIntervalList):
    """A TimeTariffIntervalList whose intervals may carry a RateComponentLink."""

    class Meta:
        name = "TimeTariffIntervalList"
        namespace = "urn:ieee:std:2030.5:ns"

    model_config = ConfigDict(defer_build=True)
    time_tariff_interval: list[CsipausTimeTariffInterval] = field(  # type: ignore[assignment]
        default_factory=list,
        metadata={
            "name": "TimeTariffInterval",
            "type": "Element",
            "namespace": "urn:ieee:std:2030.5:ns",
        },
    )
