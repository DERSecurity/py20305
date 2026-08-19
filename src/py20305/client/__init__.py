"""The IEEE 2030.5 protocol client and its transport.

Names are resolved lazily (PEP 562). Importing them eagerly here would make
``import py20305.client.errors`` pull in :class:`CsipClient`, and
that class imports the subscription and event packages -- which import back
into this one for the transport and its error types. The cycle only bites on
a cold import that starts inside one of those packages, which is exactly what
a consumer reaching for a single module does.

Deferring the import to first attribute access breaks it without changing
what this package exposes: ``from py20305.client import CsipClient``
behaves as before.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Re-exported for type checkers and editors, which cannot follow the
    # runtime __getattr__ below. Written in the redundant-alias form so the
    # linter reads them as re-exports rather than unused imports -- __all__ is
    # built from _EXPORTS, so it has nothing else to go on.
    from py20305.client.connector import Address as Address
    from py20305.client.connector import SocketPair as SocketPair
    from py20305.client.csip_client import CsipClient as CsipClient
    from py20305.client.errors import Sep2ConnectionError as Sep2ConnectionError
    from py20305.client.errors import Sep2Error as Sep2Error
    from py20305.client.errors import Sep2ProtocolError as Sep2ProtocolError
    from py20305.client.errors import Sep2TlsError as Sep2TlsError
    from py20305.client.http import Sep2Client as Sep2Client
    from py20305.client.observer import ConnectionObserver as ConnectionObserver
    from py20305.client.polling import PollScheduler as PollScheduler
    from py20305.client.retry import RetryPolicy as RetryPolicy
    from py20305.client.retry import with_retry as with_retry
    from py20305.client.state import DiscoveredState as DiscoveredState
    from py20305.client.tls import TlsConfig as TlsConfig
    from py20305.client.tls import create_ssl_context as create_ssl_context

#: Exported name -> the submodule that defines it.
_EXPORTS: dict[str, str] = {
    "Address": "connector",
    "ConnectionObserver": "observer",
    "CsipClient": "csip_client",
    "DiscoveredState": "state",
    "SocketPair": "connector",
    "PollScheduler": "polling",
    "RetryPolicy": "retry",
    "Sep2Client": "http",
    "Sep2ConnectionError": "errors",
    "Sep2Error": "errors",
    "Sep2ProtocolError": "errors",
    "Sep2TlsError": "errors",
    "TlsConfig": "tls",
    "create_ssl_context": "tls",
    "with_retry": "retry",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import on first access: an exported name, or one of this package's submodules.

    Submodules are resolved here too, not only exports. Without that,
    ``import py20305.client`` followed by ``client.tls`` raises
    AttributeError -- but starts working the moment any unrelated code touches
    an export, because importing the submodule to resolve that export binds it
    here as a side effect. An attribute that exists depending on what ran
    first is worse than one that consistently does not.

    The submodule is resolved by attempting the import rather than by
    consulting a list, because a hand-maintained list drifts: this package
    gained ``discovery`` without one being updated, and it appeared to work
    only because something else had already imported it.
    """
    import importlib

    module_name = _EXPORTS.get(name)
    if module_name is not None:
        module = importlib.import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value  # cache, so this runs once per name
        return value

    if not name.startswith("_"):
        target = f"{__name__}.{name}"
        try:
            module = importlib.import_module(target)
        except ModuleNotFoundError as exc:
            # Only "there is no such submodule" becomes an AttributeError. A
            # submodule that exists but fails to import because one of *its*
            # dependencies is missing must keep raising: turning that into an
            # AttributeError would hide the real missing package behind a
            # message about this one.
            if exc.name != target:
                raise
        else:
            globals()[name] = module
            return module

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
