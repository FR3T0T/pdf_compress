"""net_guard — hard offline enforcement for the embedded web UI.

The PDF Toolkit makes no network requests by design.  This module turns
that design choice into a guarantee the code *cannot* violate: it installs
a ``QWebEngineUrlRequestInterceptor`` that blocks every request whose URL
scheme is not local.  Any tracker, beacon, telemetry call, externally
hosted font/script, or accidentally introduced ``fetch()`` is dropped at
the engine level before a single byte leaves the machine.

Allowed schemes are local-only:
    file   — the bundled HTML/CSS/JS frontend
    qrc    — Qt's built-in resources (qwebchannel.js)
    data   — inline data: URIs (icons, generated thumbnails)
    blob   — in-memory blobs created by the page
    about  — about:blank and similar internal pages

Everything else (http, https, ws, wss, ftp, …) is blocked and logged.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: Schemes that never leave the local machine.
ALLOWED_SCHEMES = frozenset({"file", "qrc", "data", "blob", "about"})


def is_local_request(scheme: str) -> bool:
    """Return True if a URL scheme is purely local (safe to allow).

    Pure function — unit-testable without a running Qt application.
    """
    return (scheme or "").strip().lower() in ALLOWED_SCHEMES


def install_offline_guard(profile) -> "OfflineRequestInterceptor":
    """Attach an offline interceptor to a ``QWebEngineProfile``.

    Returns the interceptor instance.  **Keep a reference to it** for the
    lifetime of the window — Qt does not take ownership and it will be
    garbage-collected (and silently stop working) otherwise.
    """
    interceptor = OfflineRequestInterceptor()
    profile.setUrlRequestInterceptor(interceptor)
    log.info("Offline network guard installed (non-local requests blocked)")
    return interceptor


# The Qt class is defined lazily so this module can be imported (and the
# pure logic tested) even where PySide6-WebEngine is unavailable.
try:
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

    class OfflineRequestInterceptor(QWebEngineUrlRequestInterceptor):
        """Blocks every request that is not a local scheme."""

        def interceptRequest(self, info):  # noqa: N802 (Qt signature)
            try:
                scheme = info.requestUrl().scheme()
            except Exception:
                # Fail closed: if we can't read it, block it.
                info.block(True)
                return
            if not is_local_request(scheme):
                log.warning("Blocked non-local request: %s",
                            info.requestUrl().toString())
                info.block(True)

except Exception:  # pragma: no cover - only when WebEngine is missing
    class OfflineRequestInterceptor:  # type: ignore[no-redef]
        """Stub used only when PySide6-WebEngine is not installed."""
        def interceptRequest(self, info):  # noqa: N802
            pass
