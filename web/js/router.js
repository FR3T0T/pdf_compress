/* ==========================================================================
   PDF Toolkit - Hash-based SPA Router
   Pages register themselves and the router manages lifecycle (mount,
   activate, deactivate).  Navigation is driven by window.location.hash.
   ========================================================================== */

"use strict";

const Router = {
    /** @private  path -> { create: Function, instance: Object|null } */
    _routes: {},

    /** @private  Currently active route path */
    _current: null,

    /** @private  DOM element that holds the active page (#page-content) */
    _container: null,

    /* ------------------------------------------------------------------
       Public API
       ------------------------------------------------------------------ */

    /**
     * Register a page.
     * @param {string}   path      Route key (e.g. "home", "compress")
     * @param {Function} createFn  Factory that returns a page instance with
     *                             onMount(el), and optionally onActivated(),
     *                             onDeactivated(), isBusy().
     */
    register(path, createFn) {
        this._routes[path] = { create: createFn, instance: null };
    },

    /**
     * Boot the router.  Should be called once after the DOM is ready and all
     * pages have been registered.
     */
    init() {
        this._container = document.getElementById('page-content');
        window.addEventListener('hashchange', () => this._onHashChange());
        // Navigate to whatever is in the address bar (or fall back to home)
        this._onHashChange();
    },

    /**
     * Programmatic navigation.
     * @param {string} path  Route key
     */
    navigate(path) {
        window.location.hash = '#/' + path;
    },

    /**
     * Return the instance of the currently active page (or null).
     * @returns {Object|null}
     */
    getCurrentPage() {
        return this._routes[this._current]?.instance ?? null;
    },

    /* ------------------------------------------------------------------
       Internal
       ------------------------------------------------------------------ */

    /** @private */
    _onHashChange() {
        const raw = (window.location.hash.slice(2) || '').split('?')[0];
        const path = raw || 'home';

        // Already on this page -- nothing to do
        if (path === this._current) return;

        // If the current page is busy (e.g. mid-operation) block navigation
        const currentRoute = this._routes[this._current];
        if (currentRoute?.instance?.isBusy?.()) {
            // Restore the hash so the URL stays consistent
            window.location.hash = '#/' + this._current;
            Toast.show('Operation in progress -- please wait or cancel first.', 'warning');
            return;
        }

        // Deactivate the outgoing page
        if (currentRoute?.instance?.onDeactivated) {
            currentRoute.instance.onDeactivated();
        }

        // Look up the target route
        const route = this._routes[path];
        if (!route) {
            // Unknown route -- bounce to home
            this.navigate('home');
            return;
        }

        // Lazily create the page instance on first visit
        if (!route.instance) {
            route.instance = route.create();
        }

        // Mount the page into the container
        this._container.innerHTML = '';
        const pageEl = document.createElement('div');
        pageEl.className = 'page animate-fadeIn';
        this._container.appendChild(pageEl);
        route.instance.onMount(pageEl);

        // Notify the page that it is now visible
        if (route.instance.onActivated) {
            route.instance.onActivated();
        }

        this._current = path;

        // Keep the sidebar highlight in sync
        if (typeof _updateSidebarActive === 'function') {
            _updateSidebarActive(path);
        } else if (typeof updateSidebarActive === 'function') {
            updateSidebarActive(path);
        }
    },
};
