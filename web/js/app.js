/* ==========================================================================
   PDF Toolkit -- Main Application Bootstrap
   Initialises QWebChannel, connects Bridge signals, renders sidebar,
   applies theme, and boots the client-side router.
   ========================================================================== */

"use strict";

/* --------------------------------------------------------------------------
   Global application state
   -------------------------------------------------------------------------- */

const App = {
    /** @type {object|null}  QWebChannel proxy for the Python Bridge */
    bridge: null,

    /** @type {string|null}  Key of the currently active page/tool */
    currentPage: null,

    /** @type {Array<object>}  Tool definitions from the Python registry */
    tools: [],

    /** @type {object}  Category id -> display name */
    categories: {},

    /** @type {string}  Active theme name: "light" or "dark" */
    theme: "light",

    /** @type {boolean}  Whether the sidebar is collapsed */
    sidebarCollapsed: true,
};

/* --------------------------------------------------------------------------
   Lightweight event bus -- decouples Bridge signals from UI components
   -------------------------------------------------------------------------- */

const EventBus = {
    /** @private */
    _listeners: {},

    /**
     * Subscribe to an event.
     * @param {string}   event
     * @param {Function} callback
     */
    on(event, callback) {
        if (!this._listeners[event]) {
            this._listeners[event] = [];
        }
        this._listeners[event].push(callback);
    },

    /**
     * Unsubscribe from an event.
     * @param {string}   event
     * @param {Function} callback
     */
    off(event, callback) {
        const list = this._listeners[event];
        if (!list) return;
        const idx = list.indexOf(callback);
        if (idx !== -1) list.splice(idx, 1);
    },

    /**
     * Emit an event to all registered listeners.
     * @param {string} event
     * @param {*}      data
     */
    emit(event, data) {
        const list = this._listeners[event];
        if (!list) return;
        for (let i = 0; i < list.length; i++) {
            try {
                list[i](data);
            } catch (err) {
                console.error(`[EventBus] Error in "${event}" listener:`, err);
            }
        }
    },
};

/* --------------------------------------------------------------------------
   QWebChannel initialisation
   (Router is defined in router.js -- loaded before this file)
   -------------------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", function () {
    // qt.webChannelTransport is injected by QWebEngineView when a
    // QWebChannel is attached to the page.
    if (typeof qt === "undefined" || !qt.webChannelTransport) {
        console.error(
            "[app] qt.webChannelTransport is not available. " +
            "Ensure the page is loaded inside a QWebEngineView with a QWebChannel."
        );
        return;
    }

    new QWebChannel(qt.webChannelTransport, function (channel) {
        App.bridge = channel.objects.bridge;
        if (!App.bridge) {
            console.error('[app] "bridge" object not found on the QWebChannel.');
            return;
        }

        _connectSignals();
        _boot();
    });
});

/* --------------------------------------------------------------------------
   Bridge signal wiring
   -------------------------------------------------------------------------- */

/**
 * Connect all Python -> JS signals to the EventBus.
 * @private
 */
function _connectSignals() {
    const b = App.bridge;

    b.progressUpdate.connect(function (jsonStr) {
        try { EventBus.emit("progress", JSON.parse(jsonStr)); }
        catch (e) { console.error("[app] Bad progressUpdate payload", e); }
    });

    b.operationDone.connect(function (jsonStr) {
        try { EventBus.emit("done", JSON.parse(jsonStr)); }
        catch (e) { console.error("[app] Bad operationDone payload", e); }
    });

    b.filesDropped.connect(function (jsonStr) {
        try { EventBus.emit("files-dropped", JSON.parse(jsonStr)); }
        catch (e) { console.error("[app] Bad filesDropped payload", e); }
    });

    b.themeChanged.connect(function (jsonStr) {
        try { applyTheme(JSON.parse(jsonStr)); }
        catch (e) { console.error("[app] Bad themeChanged payload", e); }
    });
}

/* --------------------------------------------------------------------------
   Boot sequence
   -------------------------------------------------------------------------- */

/**
 * Fetch tool registry, restore saved state, render sidebar, and start
 * the router.
 * @private
 */
function _boot() {
    // getToolRegistry returns a JSON string via QWebChannel callback
    App.bridge.getToolRegistry(function (toolsJson) {
        try {
            var registry = JSON.parse(toolsJson);
            App.tools = registry.tools || [];
            // Convert categories array [{key, label}, ...] to {key: label}
            var catsArr = registry.categories || [];
            var catsObj = {};
            for (var i = 0; i < catsArr.length; i++) {
                catsObj[catsArr[i].key] = catsArr[i].label;
            }
            App.categories = catsObj;
        } catch (e) {
            console.error("[app] Failed to parse tool registry", e);
            App.tools = [];
            App.categories = {};
        }

        // Restore sidebar collapsed state
        App.bridge.loadSetting("sidebar_collapsed", function (rawValue) {
            try {
                var value = JSON.parse(rawValue);
                App.sidebarCollapsed = value !== "false";
            } catch (e) {
                App.sidebarCollapsed = true;
            }
            renderSidebar();
            Router.init();
        });
    });
}

/* --------------------------------------------------------------------------
   Theme application
   -------------------------------------------------------------------------- */

/**
 * Apply a theme by setting CSS custom properties on ``:root``.
 * @param {object} vars  Map of CSS property name -> value
 */
function applyTheme(vars) {
    var root = document.documentElement;
    var themeName = vars["--theme-name"] || "light";

    for (var key in vars) {
        if (!vars.hasOwnProperty(key)) continue;
        if (key === "--theme-name") continue;
        root.style.setProperty(key, vars[key]);
    }

    root.setAttribute("data-theme", themeName);
    App.theme = themeName;

    // Update the theme toggle icon in the sidebar
    var themeIcon = document.getElementById("sidebar-theme-icon");
    if (themeIcon) {
        themeIcon.innerHTML = _svgIcon(themeName === "light" ? "moon" : "sun");
    }
    var themeLabel = document.getElementById("sidebar-theme-label");
    if (themeLabel) {
        themeLabel.textContent = themeName === "light" ? "Dark mode" : "Light mode";
    }

    EventBus.emit("theme", themeName);
}

/* --------------------------------------------------------------------------
   Sidebar rendering
   -------------------------------------------------------------------------- */

/**
 * Build the sidebar HTML from ``App.tools`` and inject it into the DOM.
 * Class names match layout.css conventions: .sidebar, .sidebar.expanded,
 * .sidebar-header, .sidebar-nav, .sidebar-nav-item, .sidebar-footer, etc.
 */
function renderSidebar() {
    var container = document.getElementById("sidebar");
    if (!container) {
        console.warn("[app] #sidebar element not found in the DOM.");
        return;
    }

    var expanded = !App.sidebarCollapsed;

    // -- Header / Brand ---------------------------------------------------
    var html = "";
    html += '<div class="sidebar-header">';
    html +=   '<div class="sidebar-logo">P</div>';
    html +=   '<span class="sidebar-brand">PDF Toolkit</span>';
    html += "</div>";

    // -- Toggle button (positioned on sidebar edge via CSS) ---------------
    html += '<button class="sidebar-toggle" id="sidebar-toggle" title="Toggle sidebar">';
    html +=   '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="4,2 9,7 4,12"/></svg>';
    html += "</button>";

    // -- Navigation (scrollable) ------------------------------------------
    html += '<nav class="sidebar-nav scrollbar-thin">';

    // Home button
    html += _sidebarNavItem("home", "Home", _svgIcon("home"));

    // Group tools by category
    var catKeys = Object.keys(App.categories);
    for (var ci = 0; ci < catKeys.length; ci++) {
        var catKey = catKeys[ci];
        var catLabel = App.categories[catKey];
        var catTools = App.tools.filter(function (t) { return t.category === catKey; });
        if (catTools.length === 0) continue;

        // Category section label
        html += '<div class="sidebar-section-label">' + catLabel + "</div>";

        for (var ti = 0; ti < catTools.length; ti++) {
            var tool = catTools[ti];
            html += _sidebarNavItem(tool.key, tool.title, _svgIcon(tool.icon));
        }
    }

    html += "</nav>";

    // -- Footer (theme toggle) --------------------------------------------
    html += '<div class="sidebar-footer">';
    html += '<button class="sidebar-nav-item" id="sidebar-theme-btn" data-tooltip="Toggle theme">';
    html +=   '<span class="nav-icon" id="sidebar-theme-icon">' + _svgIcon(App.theme === "light" ? "moon" : "sun") + "</span>";
    html +=   '<span class="nav-label" id="sidebar-theme-label">' + (App.theme === "light" ? "Dark mode" : "Light mode") + "</span>";
    html += "</button>";
    html += "</div>";

    // Apply class and inject
    container.className = "sidebar" + (expanded ? " expanded" : "");
    container.innerHTML = html;

    // -- Event listeners --------------------------------------------------
    document.getElementById("sidebar-toggle").addEventListener("click", toggleSidebar);
    document.getElementById("sidebar-theme-btn").addEventListener("click", toggleTheme);

    var navItems = container.querySelectorAll(".sidebar-nav-item[data-route]");
    for (var i = 0; i < navItems.length; i++) {
        navItems[i].addEventListener("click", _onNavClick);
    }

    if (App.currentPage) {
        _updateSidebarActive(App.currentPage);
    }
}

/**
 * Generate HTML for a single sidebar navigation item.
 * @private
 */
function _sidebarNavItem(routeKey, label, iconHtml) {
    var active = (App.currentPage === routeKey) ? " active" : "";
    return '<button class="sidebar-nav-item' + active + '" data-route="' + routeKey + '" data-tooltip="' + label + '">' +
           '<span class="nav-icon">' + iconHtml + "</span>" +
           '<span class="nav-label">' + label + "</span>" +
           "</button>";
}

/**
 * Handle a click on a sidebar navigation item.
 * @private
 */
function _onNavClick(e) {
    var btn = e.currentTarget;
    var route = btn.getAttribute("data-route");
    if (route) {
        Router.navigate(route);
    }
}

/**
 * Update the ``.active`` class on sidebar nav items.
 * @private
 */
function _updateSidebarActive(routeKey) {
    var sidebar = document.getElementById("sidebar");
    if (!sidebar) return;
    var items = sidebar.querySelectorAll(".sidebar-nav-item[data-route]");
    for (var i = 0; i < items.length; i++) {
        if (items[i].getAttribute("data-route") === routeKey) {
            items[i].classList.add("active");
        } else {
            items[i].classList.remove("active");
        }
    }
}

/* --------------------------------------------------------------------------
   Sidebar collapse / expand
   -------------------------------------------------------------------------- */

/**
 * Toggle the sidebar between collapsed and expanded states.
 * CSS uses ``.sidebar.expanded`` for the wide state.
 */
function toggleSidebar() {
    App.sidebarCollapsed = !App.sidebarCollapsed;

    var sidebar = document.getElementById("sidebar");
    if (!sidebar) return;

    if (App.sidebarCollapsed) {
        sidebar.classList.remove("expanded");
    } else {
        sidebar.classList.add("expanded");
    }

    // Persist
    if (App.bridge) {
        App.bridge.saveSetting(
            "sidebar_collapsed",
            App.sidebarCollapsed ? "true" : "false"
        );
    }
}

/* --------------------------------------------------------------------------
   Theme toggle
   -------------------------------------------------------------------------- */

/**
 * Ask the Python backend to toggle the theme.
 */
function toggleTheme() {
    if (App.bridge) {
        App.bridge.requestThemeToggle();
    }
}

/* --------------------------------------------------------------------------
   Simple SVG icon helper
   -------------------------------------------------------------------------- */

/**
 * Return an inline SVG string for the given icon name.
 *
 * These are minimal, stroke-based icons matching the QPainter icons
 * defined in ``ui/icons.py``.  They are intentionally simple so the
 * sidebar stays lightweight.
 *
 * @param {string} name  Icon name (matches tool_registry icon field)
 * @returns {string}     SVG markup string
 */
function _svgIcon(name) {
    var s = 20;  // viewBox size
    var w = 1.4; // stroke width

    var icons = {
        home:
            '<path d="M10 3 L3 9 L3 17 L8 17 L8 12 L12 12 L12 17 L17 17 L17 9 Z"/>',
        compress:
            '<line x1="10" y1="3" x2="10" y2="14"/>' +
            '<polyline points="6,10 10,14 14,10"/>' +
            '<line x1="4" y1="17" x2="16" y2="17"/>',
        merge:
            '<rect x="2" y="2" width="6" height="8" rx="1"/>' +
            '<rect x="12" y="2" width="6" height="8" rx="1"/>' +
            '<line x1="10" y1="11" x2="10" y2="15"/>' +
            '<polyline points="8,13 10,15 12,13"/>' +
            '<rect x="6" y="15" width="8" height="3" rx="1"/>',
        split:
            '<rect x="6" y="1" width="8" height="5" rx="1"/>' +
            '<line x1="3" y1="8" x2="17" y2="8" stroke-dasharray="2,2"/>' +
            '<rect x="1" y="11" width="7" height="8" rx="1"/>' +
            '<rect x="12" y="11" width="7" height="8" rx="1"/>',
        lock:
            '<rect x="4" y="9" width="12" height="9" rx="2"/>' +
            '<path d="M7 9 V6 A3 3 0 0 1 13 6 V9" fill="none"/>' +
            '<circle cx="10" cy="13" r="1.2"/>',
        unlock:
            '<rect x="4" y="9" width="12" height="9" rx="2"/>' +
            '<path d="M7 9 V6 A3 3 0 0 1 13 6" fill="none"/>' +
            '<circle cx="10" cy="13" r="1.2"/>',
        image:
            '<rect x="2" y="2" width="16" height="16" rx="2" fill="none"/>' +
            '<circle cx="14" cy="6" r="1.5" fill="none"/>' +
            '<polyline points="2,15 7,8 11,11 14,7 18,15"/>',
        image_to_pdf:
            '<rect x="1" y="4" width="7" height="6" rx="1" fill="none"/>' +
            '<line x1="9" y1="7" x2="12" y2="7"/>' +
            '<polyline points="11,5 12,7 11,9"/>' +
            '<rect x="12" y="3" width="7" height="9" rx="1" fill="none"/>' +
            '<text x="15.5" y="16" text-anchor="middle" font-size="4" font-weight="bold" fill="currentColor" stroke="none">PDF</text>',
        word:
            '<rect x="3" y="3" width="14" height="14" rx="2" fill="none"/>' +
            '<text x="10" y="14" text-anchor="middle" font-size="8" font-weight="bold" fill="currentColor" stroke="none">W</text>',
        pages:
            '<rect x="5" y="2" width="12" height="12" rx="1" fill="none"/>' +
            '<rect x="3" y="5" width="12" height="12" rx="1" fill="none"/>' +
            '<line x1="5" y1="9" x2="13" y2="9"/>' +
            '<line x1="5" y1="12" x2="11" y2="12"/>',
        crop:
            '<line x1="6" y1="2" x2="6" y2="6"/><line x1="2" y1="6" x2="6" y2="6"/>' +
            '<line x1="14" y1="18" x2="14" y2="14"/><line x1="18" y1="14" x2="14" y2="14"/>' +
            '<rect x="6" y="6" width="8" height="8" fill="none" stroke-dasharray="2,2"/>',
        flatten:
            '<rect x="3" y="3" width="14" height="4" rx="1" fill="none"/>' +
            '<line x1="10" y1="7" x2="10" y2="12"/>' +
            '<polyline points="8,10 10,12 12,10"/>' +
            '<line x1="3" y1="15" x2="17" y2="15" stroke-width="2"/>',
        grid:
            '<rect x="2" y="2" width="7" height="7" rx="1" fill="none"/>' +
            '<rect x="11" y="2" width="7" height="7" rx="1" fill="none"/>' +
            '<rect x="2" y="11" width="7" height="7" rx="1" fill="none"/>' +
            '<rect x="11" y="11" width="7" height="7" rx="1" fill="none"/>',
        watermark:
            '<rect x="3" y="3" width="14" height="14" rx="2" fill="none"/>' +
            '<line x1="5" y1="14" x2="15" y2="6" opacity="0.5"/>' +
            '<line x1="5" y1="16" x2="13" y2="8" opacity="0.5"/>',
        numbers:
            '<rect x="3" y="3" width="14" height="14" rx="2" fill="none"/>' +
            '<text x="10" y="14" text-anchor="middle" font-size="8" font-weight="bold" fill="currentColor" stroke="none">#</text>',
        metadata:
            '<circle cx="10" cy="10" r="8" fill="none"/>' +
            '<text x="10" y="14" text-anchor="middle" font-size="9" font-weight="bold" font-style="italic" fill="currentColor" stroke="none">i</text>',
        extract_img:
            '<rect x="2" y="3" width="10" height="14" rx="1" fill="none"/>' +
            '<line x1="12" y1="10" x2="17" y2="10"/>' +
            '<polyline points="15,8 17,10 15,12"/>' +
            '<rect x="12" y="4" width="6" height="4" rx="1" fill="none"/>',
        extract_text:
            '<rect x="2" y="3" width="10" height="14" rx="1" fill="none"/>' +
            '<line x1="13" y1="7" x2="18" y2="7"/>' +
            '<line x1="13" y1="10" x2="17" y2="10"/>' +
            '<line x1="13" y1="13" x2="16" y2="13"/>',
        repair:
            '<circle cx="13" cy="6" r="3" fill="none"/>' +
            '<line x1="6" y1="14" x2="11" y2="8"/>',
        compare:
            '<rect x="1" y="3" width="7" height="14" rx="1" fill="none"/>' +
            '<rect x="12" y="3" width="7" height="14" rx="1" fill="none"/>' +
            '<line x1="9" y1="8" x2="11" y2="8"/>' +
            '<line x1="11" y1="12" x2="9" y2="12"/>',
        redact:
            '<rect x="3" y="3" width="14" height="14" rx="2" fill="none"/>' +
            '<rect x="5" y="6" width="10" height="2" rx="1" fill="currentColor" stroke="none"/>' +
            '<rect x="5" y="10" width="7" height="2" rx="1" fill="currentColor" stroke="none"/>' +
            '<rect x="5" y="14" width="9" height="2" rx="1" fill="currentColor" stroke="none"/>',
    };

    var inner = icons[name];
    if (!inner) {
        // Fallback: question mark
        inner = '<text x="10" y="15" text-anchor="middle" font-size="12" font-weight="bold" ' +
                'fill="currentColor" stroke="none">?</text>';
    }

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + s + " " + s + '" ' +
        'width="' + s + '" height="' + s + '" ' +
        'fill="none" stroke="currentColor" stroke-width="' + w + '" ' +
        'stroke-linecap="round" stroke-linejoin="round">' +
        inner +
        "</svg>"
    );
}
