"""
network_guard — the app's own process makes no network connection unless
you start a setup step (#120 d).

``install()`` runs first thing when the app and the command line start
(app.py, compress_pdf.py). From then on Python's socket module refuses every
connection to anything but this computer (loopback, ``localhost``):

  * ``socket.socket.connect`` and ``connect_ex``, whatever opened the socket;
  * ``socket.create_connection``, before it looks the name up;
  * ``socket.getaddrinfo`` for a name or address that is not local, which is
    where a lookup would go out (a DNS query says where you are going).

A proxy on this computer counts as leaving it: urllib connects to the proxy,
on loopback, and sends it CONNECT for the real host, which the guard never
sees. So a connection to the port of a proxy Python would use
(``urllib.request.getproxies()``: the environment, and on Windows the
system's Internet settings, read when the connection is made) is refused
too, and recorded as "a proxy on this computer at 127.0.0.1:8888". Other
loopback connections stay allowed (asyncio's socketpair on Windows makes
one). A forwarder on this computer that is not set as a proxy cannot be
told from any other program here.

A refused connection raises :class:`NetworkRefusedError`, an OSError (so
code that handles a failed connection handles this one), saying in plain
words that the app makes no network connection unless you start a setup
step. Its host is named; no file ever is.

The one way through is a window: ``with allow_network("translation setup"):``
around the setup steps the user starts in the app (pdf_translate.run_setup:
the translation engine's wheels, the sentence models and the language
packs). While a window is open the process may connect, through a proxy
too; windows nest, and one shuts when its block ends, however it ends. setup_translation.py and
setup_ocr.py are download tools run on purpose: they do not install the
guard at all.

What it covers, and what it does not, plainly (the README says the same):
it covers Python code in the app's own process. Other programs the app
starts (Ghostscript, pip in the developers' lock generator) are other
processes; native code inside this one (MuPDF and its built-in Tesseract,
Qt) does not go through Python's socket module; and a datagram sent without
connecting (``sendto``) or a lookup through ``gethostbyname`` is not wrapped.
Nothing in the app does either. The web view has its own guard
(ui/net_guard.py).

It records what happened, for the line a removal report carries (#100):
each window (what for, when it opened and shut) and each refused connection
(its host name, never a file name). ``mark()`` and ``activity_since()`` give
what happened during one run; ``in_words()`` says it.

Qt-free and stdlib-only: tests import it (see CLAUDE.md).
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
import threading
from datetime import datetime

log = logging.getLogger(__name__)

#: What a refused connection says, before naming its host.
REFUSED = ("The PDF Toolkit makes no network connection unless you start a setup step "
           "(translation setup, or fetching the OCR language file).")

#: How many refused connections are kept; a count of the rest is not lost.
_KEEP = 500


class NetworkRefusedError(ConnectionRefusedError):
    """A connection the guard refused: an OSError, as any failed connection
    is."""


_lock = threading.RLock()
_originals: dict = {}          # name -> (owner, attribute, original, was in owner.__dict__)
_seq = 0                       # one count for every event, so runs can be told apart
_windows: list[dict] = []      # every window opened, most recent last
_open = 0                      # windows open now
_refusals: list[tuple[int, str]] = []   # (event number, host)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _next() -> int:
    global _seq
    _seq += 1
    return _seq


# ── What counts as this computer ─────────────────────────────────────────

def _host_text(host) -> str:
    if isinstance(host, (bytes, bytearray)):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def is_local(host) -> bool:
    """True for this computer: no host (a passive lookup), ``localhost``, or a
    loopback address (127.0.0.0/8, ::1, ::ffff:127.0.0.1)."""
    if host is None:
        return True
    text = _host_text(host).strip().rstrip(".").lower()
    if text == "localhost":
        return True
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def window_open() -> bool:
    """Whether a setup step has the network open now."""
    with _lock:
        return _open > 0


#: A proxy's port when its address names none, by scheme.
_PROXY_PORTS = {"http": 80, "https": 443, "socks": 1080, "socks4": 1080, "socks4a": 1080,
                "socks5": 1080, "socks5h": 1080}


def _local_proxy_ports() -> set:
    """The ports of the proxies Python would use (urllib.request.getproxies:
    the environment, and on Windows the system's Internet settings) that
    are on this computer, read now: the settings can change while the app
    runs. A proxy on this computer carries a connection out — urllib
    connects to it and asks it for the real host — so a connection to one
    is not a connection that stays here. Loopback addresses are taken as one
    computer: a proxy named "localhost:8888" is the one at 127.0.0.1:8888.
    Settings that cannot be read count as none."""
    try:
        import urllib.parse
        import urllib.request
        proxies = urllib.request.getproxies()
    except Exception:
        return set()
    ports = set()
    for scheme, address in proxies.items():
        if scheme == "no" or not address:
            continue
        text = str(address) if "://" in str(address) else f"http://{address}"
        try:
            parts = urllib.parse.urlsplit(text)
            host, port = parts.hostname, parts.port
        except ValueError:
            continue
        if host and is_local(host):
            ports.add(port if port is not None else _PROXY_PORTS.get(parts.scheme.lower(), 80))
    return ports


def _refuse(name: str) -> None:
    name = name or "(no address)"
    with _lock:
        _refusals.append((_next(), name))
        del _refusals[:-_KEEP]
    log.info("A network connection was refused: the app makes none outside a setup step")
    log.debug("Refused a connection to %s", name)
    raise NetworkRefusedError(f"{REFUSED} A connection to {name} was refused.")


def _check(host, port=None) -> None:
    """Refuse a connection to *host* (and *port*, when there is one) unless
    a window is open: one that leaves this computer, or one to a proxy on
    this computer (_local_proxy_ports), which leaves it too."""
    if window_open():
        return
    if not is_local(host):
        _refuse(_host_text(host))
    if port is not None:
        try:
            number = int(port)
        except (TypeError, ValueError):
            return
        if number in _local_proxy_ports():
            text = _host_text(host) if host is not None else "localhost"
            where = f"[{text}]" if ":" in text else text
            _refuse(f"a proxy on this computer at {where}:{number}")


# ── The wrappers ─────────────────────────────────────────────────────────

_AF_UNIX = getattr(socket, "AF_UNIX", None)


def _address_of(sock, address):
    """(host, port) a socket's connect goes to; (None, None) for a local
    (Unix) socket."""
    family = getattr(sock, "family", None)
    if _AF_UNIX is not None and family == _AF_UNIX:
        return None, None
    if isinstance(address, tuple) and len(address) >= 2 and family in (socket.AF_INET,
                                                                        socket.AF_INET6):
        return address[0], address[1]
    return "an address of another kind", None


def _connect(self, address):
    _check(*_address_of(self, address))
    return _call("connect", self, address)


def _connect_ex(self, address):
    _check(*_address_of(self, address))
    return _call("connect_ex", self, address)


def _create_connection(address, *args, **kwargs):
    if isinstance(address, tuple) and len(address) >= 2:
        _check(address[0], address[1])
    else:
        _check(address)
    return _call("create_connection", address, *args, **kwargs)


def _getaddrinfo(host, *args, **kwargs):
    _check(host)
    return _call("getaddrinfo", host, *args, **kwargs)


def _call(name: str, *args, **kwargs):
    return _originals[name][2](*args, **kwargs)


_WRAPPERS = (
    ("connect", socket.socket, "connect", _connect),
    ("connect_ex", socket.socket, "connect_ex", _connect_ex),
    ("create_connection", socket, "create_connection", _create_connection),
    ("getaddrinfo", socket, "getaddrinfo", _getaddrinfo),
)


def install() -> None:
    """Refuse every connection that leaves this computer, from now on, in
    this process (twice is once)."""
    with _lock:
        if _originals:
            return
        for name, owner, attribute, wrapper in _WRAPPERS:
            own = attribute in vars(owner)
            _originals[name] = (owner, attribute, getattr(owner, attribute), own)
            setattr(owner, attribute, wrapper)
    log.info("Network guard on: this process connects to nothing outside this computer "
             "unless a setup step is running")


def uninstall() -> None:
    """Put the socket module back as it was (for tests)."""
    with _lock:
        for owner, attribute, original, own in _originals.values():
            if own:
                setattr(owner, attribute, original)
            else:
                with contextlib.suppress(AttributeError):
                    delattr(owner, attribute)
        _originals.clear()


def installed() -> bool:
    with _lock:
        return bool(_originals)


@contextlib.contextmanager
def allow_network(purpose: str):
    """Let this process connect for the length of the block: a setup step the
    user started. *purpose* says which, in words ("translation setup"); it is
    recorded with when the window opened and shut."""
    global _open
    with _lock:
        window = {"purpose": purpose, "opened": _now(), "closed": None,
                  "openedAt": _next(), "closedAt": None}
        _windows.append(window)
        del _windows[:-_KEEP]
        _open += 1
    log.info("Network allowed for %s", purpose)
    try:
        yield
    finally:
        with _lock:
            window["closed"], window["closedAt"] = _now(), _next()
            _open -= 1
        log.info("Network closed again after %s", purpose)


# ── What happened during a run ───────────────────────────────────────────

def mark() -> int:
    """Where the record stands now: hand it to activity_since() at the end
    of a run."""
    with _lock:
        return _seq


def activity_since(since: int) -> dict:
    """What happened from *since* (a mark()) to now: ``watched`` (the guard
    is on), the ``windows`` that were open at any time in between (purpose,
    opened, closed; closed is None for one still open), how many
    connections were ``refused`` and to which ``hosts``."""
    with _lock:
        windows = [{"purpose": w["purpose"], "opened": w["opened"], "closed": w["closed"]}
                   for w in _windows if w["closedAt"] is None or w["closedAt"] > since]
        refused = [host for at, host in _refusals if at > since]
        return {"watched": bool(_originals), "windows": windows, "refused": len(refused),
                "hosts": sorted(set(refused))}


def _clock(stamp) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(stamp)


def in_words(activity) -> str:
    """One line for a report: "Network: none used during this run.", the
    window that was open, or "not watched" when the guard was not on."""
    if not activity or not activity.get("watched"):
        return "Network: not watched (the network guard was not running)."
    windows = activity.get("windows") or []
    if windows:
        spans = "; ".join(
            f"{w['purpose']} during part of this run, from {_clock(w['opened'])} to "
            + (_clock(w["closed"]) if w.get("closed") else "the end of the run")
            for w in windows)
        line = f"Network: open for {spans}."
    else:
        line = "Network: none used during this run."
    n = int(activity.get("refused") or 0)
    if n:
        hosts = list(activity.get("hosts") or [])
        named = ", ".join(hosts[:5]) + (f" and {len(hosts) - 5} more" if len(hosts) > 5 else "")
        line += (f" {n} attempt{'' if n == 1 else 's'} to connect {'was' if n == 1 else 'were'} "
                 f"refused ({named}).")
    return line
