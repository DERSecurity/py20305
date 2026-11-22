"""A loopback IEEE 2030.5 server for tests that run several client processes as one.

Serves one client's view: a DeviceCapability, an EndDeviceList of several
EndDevices (the first is the client's own and carries the SubscriptionList), a
FunctionSetAssignments list and a DERProgram per EndDevice, and a DERControl
list per program. It records every request, every Response POSTed, and keeps a
SubscriptionList that takes POST, PUT and DELETE. The XML is written out by
hand, so the client's parser is checked against text it did not produce.
"""

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from aiohttp import web

NS = "urn:ieee:std:2030.5:ns"
SEP_XML = "application/sep+xml"
SUB_LIST = "/edev/0/sub"
NOTIFY = "https://host.example.com:10443/notify"


def lfdi_for(index: int) -> str:
    """Return the LFDI of EndDevice ``index``: 40 hex characters."""
    return f"{index + 1:02x}" * 20


@dataclass
class Control:
    """One DERControl the server serves."""

    mrid: str
    start: int
    duration: int = 3600
    current_status: int = 0


@dataclass
class FakeTreeServer:
    """The resource tree of one client with ``devices`` EndDevices.

    ``program_of`` maps an EndDevice index to the index of the DERProgram its
    FunctionSetAssignments name; by default each EndDevice has its own.
    """

    devices: int = 2
    program_of: dict[int, int] = field(default_factory=dict)
    controls: dict[int, list[Control]] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    responses: list[tuple[str, str, int]] = field(default_factory=list)
    subscriptions: dict[str, str] = field(default_factory=dict)
    sub_posts: list[str] = field(default_factory=list)
    sub_deletes: list[str] = field(default_factory=list)
    sub_puts: list[str] = field(default_factory=list)
    next_sub: int = 1
    #: GETs of this path wait for ``release`` after setting ``reached``.
    hold: str | None = None
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    def program(self, device: int) -> int:
        return self.program_of.get(device, device)

    def gets(self, path: str) -> int:
        """Return how many GETs of ``path`` the server answered."""
        return sum(1 for method, p in self.requests if method == "GET" and p == path)

    def responses_for(self, device: int) -> list[tuple[str, int]]:
        """Return ``(mrid, status)`` of each Response POSTed for EndDevice ``device``."""
        lfdi = lfdi_for(device)
        return [(mrid, status) for who, mrid, status in self.responses if who == lfdi]

    # -- XML ----------------------------------------------------------------

    def _dcap(self) -> str:
        return (
            f'<DeviceCapability xmlns="{NS}" href="/dcap" pollRate="900">'
            f'<EndDeviceListLink href="/edev" all="{self.devices}"/>'
            "</DeviceCapability>"
        )

    def _edev_list(self) -> str:
        entries = ""
        for i in range(self.devices):
            sub = f'<SubscriptionListLink href="{SUB_LIST}" all="0"/>' if i == 0 else ""
            entries += (
                f'<EndDevice href="/edev/{i}">'
                f'<FunctionSetAssignmentsListLink href="/edev/{i}/fsa" all="1"/>'
                f"<lFDI>{lfdi_for(i).upper()}</lFDI><sFDI>{100000 + i}</sFDI>"
                f"<changedTime>1</changedTime>{sub}</EndDevice>"
            )
        return (
            f'<EndDeviceList xmlns="{NS}" href="/edev" all="{self.devices}" '
            f'results="{self.devices}" subscribable="1">{entries}</EndDeviceList>'
        )

    def _fsa_list(self, i: int) -> str:
        return (
            f'<FunctionSetAssignmentsList xmlns="{NS}" href="/edev/{i}/fsa" all="1" '
            f'results="1" subscribable="1">'
            f'<FunctionSetAssignments href="/edev/{i}/fsa/1">'
            f'<DERProgramListLink href="/edev/{i}/fsa/1/derp" all="1"/>'
            f"<mRID>AA0000000000000000000000000000{i:02X}</mRID>"
            "</FunctionSetAssignments></FunctionSetAssignmentsList>"
        )

    def _derp_list(self, i: int) -> str:
        p = self.program(i)
        return (
            f'<DERProgramList xmlns="{NS}" href="/edev/{i}/fsa/1/derp" all="1" results="1">'
            f'<DERProgram href="/derp/{p}">'
            f'<DERControlListLink href="/derp/{p}/derc" all="1"/>'
            f"<mRID>BB0000000000000000000000000000{p:02X}</mRID>"
            "<primacy>1</primacy></DERProgram></DERProgramList>"
        )

    def _derc_list(self, p: int) -> str:
        controls = self.controls.get(p, [])
        entries = "".join(
            f'<DERControl href="/derp/{p}/derc/{n}" replyTo="/rsps" responseRequired="03">'
            f"<mRID>{c.mrid}</mRID><creationTime>1</creationTime>"
            f"<EventStatus><currentStatus>{c.current_status}</currentStatus>"
            "<dateTime>1</dateTime><potentiallySuperseded>false</potentiallySuperseded>"
            "</EventStatus>"
            f"<interval><duration>{c.duration}</duration><start>{c.start}</start></interval>"
            "<DERControlBase><opModMaxLimW>8000</opModMaxLimW></DERControlBase>"
            "</DERControl>"
            for n, c in enumerate(controls, start=1)
        )
        return (
            f'<DERControlList xmlns="{NS}" href="/derp/{p}/derc" all="{len(controls)}" '
            f'results="{len(controls)}" subscribable="1">{entries}</DERControlList>'
        )

    def _sub_list(self) -> str:
        entries = "".join(
            f'<Subscription href="{href}"><subscribedResource>{resource}</subscribedResource>'
            "<encoding>0</encoding><level>+S1</level><limit>1</limit>"
            f"<notificationURI>{NOTIFY}</notificationURI></Subscription>"
            for href, resource in self.subscriptions.items()
        )
        n = len(self.subscriptions)
        return (
            f'<SubscriptionList xmlns="{NS}" href="{SUB_LIST}" all="{n}" results="{n}">'
            f"{entries}</SubscriptionList>"
        )

    # -- handlers -----------------------------------------------------------

    @staticmethod
    def _xml(body: str) -> web.Response:
        return web.Response(body=body.encode(), content_type=SEP_XML)

    async def _get(self, request: web.Request) -> web.Response:
        path = request.path
        self.requests.append(("GET", path))
        if path == self.hold:
            self.reached.set()
            await self.release.wait()
        parts = path.strip("/").split("/")
        if path == "/dcap":
            return self._xml(self._dcap())
        if path == "/edev":
            return self._xml(self._edev_list())
        if path == SUB_LIST:
            return self._xml(self._sub_list())
        if parts[0] == "edev" and parts[2:] == ["fsa"]:
            return self._xml(self._fsa_list(int(parts[1])))
        if parts[0] == "edev" and parts[2:] == ["fsa", "1", "derp"]:
            return self._xml(self._derp_list(int(parts[1])))
        if parts[0] == "derp" and parts[2:] == ["derc"]:
            return self._xml(self._derc_list(int(parts[1])))
        return web.Response(status=404)

    async def _post(self, request: web.Request) -> web.Response:
        path = request.path
        self.requests.append(("POST", path))
        root = ET.fromstring(await request.read())
        if path == "/rsps":
            lfdi = (root.findtext(f"{{{NS}}}endDeviceLFDI") or "").lower()
            mrid = (root.findtext(f"{{{NS}}}subject") or "").upper()
            status = int(root.findtext(f"{{{NS}}}status") or "-1")
            self.responses.append((lfdi, mrid, status))
            return web.Response(status=201, headers={"Location": f"/rsps/{len(self.responses)}"})
        if path == SUB_LIST:
            resource = root.findtext(f"{{{NS}}}subscribedResource") or ""
            self.sub_posts.append(resource)
            href = f"{SUB_LIST}/{self.next_sub}"
            self.next_sub += 1
            self.subscriptions[href] = resource
            return web.Response(status=201, headers={"Location": href})
        return web.Response(status=404)

    async def _put(self, request: web.Request) -> web.Response:
        self.requests.append(("PUT", request.path))
        self.sub_puts.append(request.path)
        return web.Response(status=204)

    async def _delete(self, request: web.Request) -> web.Response:
        self.requests.append(("DELETE", request.path))
        self.sub_deletes.append(request.path)
        self.subscriptions.pop(request.path, None)
        return web.Response(status=204)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._get)
        app.router.add_post("/{tail:.*}", self._post)
        app.router.add_put("/{tail:.*}", self._put)
        app.router.add_delete("/{tail:.*}", self._delete)
        return app


def notification_xml(subscribed_resource: str, subscription_href: str, status: int = 0) -> bytes:
    """Return a Notification body for ``subscribed_resource``."""
    return (
        f'<Notification xmlns="{NS}">'
        f"<subscribedResource>{subscribed_resource}</subscribedResource>"
        f"<status>{status}</status>"
        f"<subscriptionURI>{subscription_href}</subscriptionURI>"
        "</Notification>"
    ).encode()


def now() -> int:
    return int(time.time())
