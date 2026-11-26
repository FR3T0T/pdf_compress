"""The app's own process makes no network connection (#120 d).

network_guard, installed when the app and the command line start, refuses
every outbound connection Python's socket module would make to anything but
this computer (loopback): socket.socket.connect and connect_ex,
socket.create_connection, and socket.getaddrinfo for a name that is not
local, which is where a lookup would go out. The one way through is a
window the setup steps the user starts open around themselves
(allow_network): translation setup in the app. It records the windows and
the connections it refused, for the line a removal report carries.

No test here reaches the network. Where a connection would go out, the
socket calls underneath the guard are recorders that never connect: what
the guard lets through is seen arriving there, and what it refuses is seen
not to. Loopback is tried for real, on a listening socket of the test's own.

Qt-free (see CLAUDE.md): the bridge is read as text, never imported.
"""

import ast
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import network_guard
import pdf_translate
import translate_runtime

ROOT = Path(__file__).resolve().parent.parent
TEST_NET = "192.0.2.1"            # RFC 5737: documentation only, routes nowhere


@pytest.fixture
def underneath(monkeypatch):
    """Recorders in place of the real socket calls, put in before the guard
    so that it calls them: nothing can leave the computer."""
    calls = []

    def connect(sock, address):
        calls.append(("connect", address))

    def connect_ex(sock, address):
        calls.append(("connect_ex", address))
        return 0

    def getaddrinfo(host, port, *args, **kwargs):
        calls.append(("getaddrinfo", host))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (TEST_NET, port or 80))]

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return calls


@pytest.fixture
def guard():
    network_guard.install()
    try:
        yield network_guard
    finally:
        network_guard.uninstall()


def test_urllib_to_an_outside_address_is_refused_before_any_connection(underneath, guard):
    mark = guard.mark()
    with pytest.raises(urllib.error.URLError) as caught:
        urllib.request.urlopen(f"http://{TEST_NET}/", timeout=5)
    reason = caught.value.reason
    assert isinstance(reason, guard.NetworkRefusedError) and isinstance(reason, OSError)
    assert str(reason) == (
        "The PDF Toolkit makes no network connection unless you start a setup step "
        "(translation setup, or fetching the OCR language file). A connection to 192.0.2.1 "
        "was refused.")
    assert underneath == []                          # nothing was looked up or connected
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("http://updates.example.com/check", timeout=5)
    assert underneath == []                          # the name was not looked up
    seen = guard.activity_since(mark)
    assert (seen["watched"], seen["refused"], seen["hosts"], seen["windows"]) == (
        True, 2, ["192.0.2.1", "updates.example.com"], [])


@pytest.mark.parametrize("call", ["connect", "connect_ex", "create_connection", "getaddrinfo"])
def test_each_way_out_is_refused(underneath, guard, call):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(guard.NetworkRefusedError):
            if call == "connect":
                sock.connect((TEST_NET, 80))
            elif call == "connect_ex":
                sock.connect_ex((TEST_NET, 80))
            elif call == "create_connection":
                socket.create_connection((TEST_NET, 80), timeout=5)
            else:
                socket.getaddrinfo("updates.example.com", 443)
    finally:
        sock.close()
    assert underneath == []


@pytest.mark.parametrize("host", ["::ffff:192.0.2.1", "0.0.0.0", "", "localhost.example.com",
                                  "10.0.0.1", "[2001:db8::1]"])
def test_addresses_that_are_not_this_computer_are_refused(underneath, guard, host):
    with pytest.raises(guard.NetworkRefusedError):
        socket.getaddrinfo(host, 80)
    assert underneath == []


def test_loopback_is_allowed(guard):
    """For real: a listening socket on this computer, reached by address and
    by name, on IPv4 (and IPv6 where the computer has it)."""
    families = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_ipv6:
        families.append((socket.AF_INET6, "::1"))
    for family, address in families:
        try:
            server = socket.socket(family, socket.SOCK_STREAM)
            server.bind((address, 0))
        except OSError:
            continue                                  # no IPv6 loopback here
        server.listen(4)
        port = server.getsockname()[1]
        accepted: list = []

        def serve(s=server, into=accepted):
            for _ in range(2):
                into.append(s.accept()[0])

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            socket.create_connection((address, port), timeout=5).close()
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(5)
            raw.connect((address, port))
            raw.close()
        finally:
            worker.join(5)
            for conn in accepted:
                conn.close()
            server.close()
        assert len(accepted) == 2
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo(None, 80, flags=socket.AI_PASSIVE)


# ── A proxy on this computer ─────────────────────────────────────────────
#
# A proxy listening on loopback (a corporate client, an antivirus web
# shield, Fiddler) carries a connection out: urllib connects to it and sends
# CONNECT for the real host, which the guard never sees. Outside a window, a
# connection to a host:port urllib.request.getproxies() names is refused,
# loopback or not; inside one, the proxy works.

@pytest.fixture
def stand_in_proxy():
    """A listening loopback socket standing in for a proxy: it keeps what
    each connection sends it, answers 502, and closes. Nothing goes further."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    server.settimeout(0.2)
    received: list = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received.append(data)
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield server.getsockname()[1], received
    finally:
        stop.set()
        worker.join(5)
        server.close()


def _proxied(port: int) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler(
        {"https": f"http://127.0.0.1:{port}", "http": f"http://127.0.0.1:{port}"}))


@pytest.mark.parametrize("named", ["http://127.0.0.1:{port}", "socks5://localhost:{port}",
                                   "127.0.0.1:{port}", "http://someone:secret@127.0.0.1:{port}"])
def test_a_proxy_on_this_computer_is_refused_outside_a_window(guard, stand_in_proxy, monkeypatch,
                                                               named):
    """The proxy is read when the connection is made: it is named after the
    guard went on. A name or a scheme, or none, makes no difference; a
    password in the proxy's address is never recorded."""
    port, received = stand_in_proxy
    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: {"https": named.format(port=port), "no": "localhost"})
    mark = guard.mark()
    with pytest.raises(urllib.error.URLError) as caught:
        _proxied(port).open("https://updates.example.com/check", timeout=5)
    assert isinstance(caught.value.reason, guard.NetworkRefusedError)
    assert str(caught.value.reason).endswith(
        f"A connection to a proxy on this computer at 127.0.0.1:{port} was refused.")
    with pytest.raises(guard.NetworkRefusedError):
        socket.create_connection(("localhost", port), timeout=5)
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(guard.NetworkRefusedError):
        raw.connect(("127.0.0.1", port))
    raw.close()
    assert received == []                            # nothing reached the proxy
    seen = guard.activity_since(mark)
    assert seen["refused"] == 3
    assert seen["hosts"] == [f"a proxy on this computer at {h}:{port}"
                             for h in ("127.0.0.1", "localhost")]
    assert "secret" not in repr(seen)
    assert guard.in_words(seen) == (
        f"Network: none used during this run. 3 attempts to connect were refused (a proxy on "
        f"this computer at 127.0.0.1:{port}, a proxy on this computer at localhost:{port}).")


def test_a_proxy_works_inside_a_window(guard, stand_in_proxy, monkeypatch):
    """Setup behind a proxy still works: the CONNECT reaches it."""
    port, received = stand_in_proxy
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": f"http://127.0.0.1:{port}"})
    mark = guard.mark()
    with guard.allow_network("translation setup"):
        with pytest.raises(urllib.error.URLError) as caught:      # the stand-in answers 502
            _proxied(port).open("https://updates.example.com/check", timeout=5)
    assert "502" in str(caught.value.reason)
    assert received and received[0].startswith(b"CONNECT updates.example.com:443 HTTP/1.")
    assert guard.activity_since(mark)["refused"] == 0


def test_another_port_on_this_computer_is_still_allowed(guard, stand_in_proxy, monkeypatch):
    """Only the proxy's port: other loopback connections (asyncio's
    socketpair on Windows uses one) go through."""
    port, _received = stand_in_proxy
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": f"http://127.0.0.1:{port}"})
    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    other.bind(("127.0.0.1", 0))
    other.listen(1)
    try:
        socket.create_connection(("127.0.0.1", other.getsockname()[1]), timeout=5).close()
        a, b = socket.socketpair()
        a.close()
        b.close()
    finally:
        other.close()


def test_no_proxy_named_means_no_proxy_refused(guard, stand_in_proxy, monkeypatch):
    port, _received = stand_in_proxy
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    socket.create_connection(("127.0.0.1", port), timeout=5).close()


def test_the_window_lets_a_setup_through_and_closes_after_an_exception(underneath, guard):
    mark = guard.mark()
    assert guard.window_open() is False
    with pytest.raises(RuntimeError, match="the download failed"):
        with guard.allow_network("translation setup"):
            assert guard.window_open() is True
            socket.create_connection(("updates.example.com", 443), timeout=5).close()
            raise RuntimeError("the download failed")
    assert underneath == [("getaddrinfo", "updates.example.com"), ("connect", (TEST_NET, 443))]
    assert guard.window_open() is False
    with pytest.raises(guard.NetworkRefusedError):
        socket.create_connection(("updates.example.com", 443), timeout=5)
    seen = guard.activity_since(mark)
    [window] = seen["windows"]
    assert window["purpose"] == "translation setup"
    assert window["opened"] and window["closed"] and window["opened"] <= window["closed"]
    assert seen["refused"] == 1


def test_the_report_line(underneath, guard):
    mark = guard.mark()
    assert guard.in_words(guard.activity_since(mark)) == "Network: none used during this run."
    with pytest.raises(guard.NetworkRefusedError):
        socket.getaddrinfo("updates.example.com", 443)
    assert guard.in_words(guard.activity_since(mark)) == (
        "Network: none used during this run. 1 attempt to connect was refused "
        "(updates.example.com).")
    with guard.allow_network("translation setup"):
        line = guard.in_words(guard.activity_since(mark))
    assert line.startswith("Network: open for translation setup during part of this run, from ")
    assert guard.in_words(None) == "Network: not watched (the network guard was not running)."


def test_not_installed_means_not_watched(underneath):
    assert network_guard.installed() is False
    mark = network_guard.mark()
    assert network_guard.activity_since(mark)["watched"] is False
    assert network_guard.in_words(network_guard.activity_since(mark)) == (
        "Network: not watched (the network guard was not running).")


def test_uninstall_puts_everything_back(underneath):
    before = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
              socket.getaddrinfo)
    network_guard.install()
    network_guard.install()                          # twice is once
    assert socket.getaddrinfo is not before[3]
    network_guard.uninstall()
    assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
            socket.getaddrinfo) == before


# ── Who opens the window ─────────────────────────────────────────────────

def test_the_translation_setup_runs_inside_the_window(monkeypatch, guard):
    """What the app's setup button runs (pdf_translate.run_setup): the
    runtime's wheels and the language packs with their sentence models are
    fetched with the window open, and it is shut again after."""
    seen = []
    monkeypatch.setattr(translate_runtime, "runtime_status",
                        lambda: {"needed": True, "installed": False})
    monkeypatch.setattr(translate_runtime, "install_runtime",
                        lambda **kw: seen.append(("runtime", guard.window_open())))
    monkeypatch.setattr(pdf_translate, "install_languages",
                        lambda codes, **kw: seen.append(("languages", guard.window_open()))
                        or {"installed": len(codes)})
    monkeypatch.setattr(pdf_translate, "translation_status",
                        lambda: seen.append(("status", guard.window_open())) or {})
    result = pdf_translate.run_setup(["de"])
    assert seen == [("runtime", True), ("languages", True), ("status", False)]
    assert result == {"runtimeInstalled": True, "installed": 1, "status": {}}
    assert guard.window_open() is False


def test_the_window_shuts_when_the_setup_fails(monkeypatch, guard):
    monkeypatch.setattr(translate_runtime, "runtime_status",
                        lambda: {"needed": False, "installed": True})

    def fail(codes, **kw):
        raise pdf_translate.TranslationError("Could not reach the Argos package index")

    monkeypatch.setattr(pdf_translate, "install_languages", fail)
    with pytest.raises(pdf_translate.TranslationError):
        pdf_translate.run_setup(["de"])
    assert guard.window_open() is False


def _python_sources():
    skip = {"tests", "web-react", "web", "build", "dist", ".venv", "venv", ".git"}
    for path in ROOT.rglob("*.py"):
        if not skip & set(path.relative_to(ROOT).parts):
            yield path


def _calls(path: Path, name: str, owner: str | None = None) -> list:
    """The functions in *path* that call *name* (as name() or x.name(), or
    only owner.name() when *owner* is given), one entry per call, read from
    the source without importing it."""
    found = []

    def calls_it(func) -> bool:
        if owner is not None:
            return (isinstance(func, ast.Attribute) and func.attr == name
                    and isinstance(func.value, ast.Name) and func.value.id == owner)
        return getattr(func, "attr", None) == name or getattr(func, "id", None) == name

    def visit(node, inside):
        for child in ast.iter_child_nodes(node):
            here = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else inside
            if isinstance(child, ast.Call) and calls_it(child.func):
                found.append(inside)
            visit(child, here)

    visit(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def test_only_the_translation_setup_opens_the_window():
    """allow_network is called in one place in the app: the setup the
    bridge runs (pdf_translate.run_setup). The bridge's setup slot runs it,
    and nothing else in the bridge does."""
    calls = {str(p.relative_to(ROOT)): _calls(p, "allow_network") for p in _python_sources()}
    assert {name: where for name, where in calls.items() if where} == {
        "pdf_translate.py": ["run_setup"]}
    assert _calls(ROOT / "ui" / "bridge.py", "run_setup") == ["startSetupTranslation"]


def test_the_app_and_the_command_line_install_the_guard_and_the_setup_tools_do_not():
    calls = {str(p.relative_to(ROOT)): _calls(p, "install", "network_guard")
             for p in _python_sources()}
    assert calls["app.py"] == ["main"] and calls["compress_pdf.py"] == ["main"]
    for name in ("setup_translation.py", "setup_ocr.py", "translate_runtime.py",
                 "pdf_translate.py", "pdf_ocr.py"):
        assert calls[name] == [], name
    for name in ("app.py", "compress_pdf.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        body = text[text.index("def main("):]
        # The first thing main() does.
        first = [line.strip() for line in body.splitlines()[1:]
                 if line.strip() and not line.strip().startswith("#")][0]
        assert first == "network_guard.install()", name
