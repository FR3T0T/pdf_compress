/* ==========================================================================
   PDF Toolkit - Home Page
   Dashboard showing all available tools in a categorized grid.
   ========================================================================== */

"use strict";

class HomePage {

    constructor() {
        this._el = null;
        this._searchInput = null;
        this._toolCountEl = null;
        this._categorySections = [];  // { key, headerEl, gridEl, cards[] }
    }

    /* ------------------------------------------------------------------
       Lifecycle
       ------------------------------------------------------------------ */

    onMount(el) {
        this._el = el;
        this._build();
    }

    onActivated() {
        // Focus the search bar on activation
        if (this._searchInput) {
            this._searchInput.focus();
        }
    }

    onDeactivated() {
        // Nothing to tear down
    }

    isBusy() {
        return false;
    }

    /* ------------------------------------------------------------------
       DOM Construction
       ------------------------------------------------------------------ */

    _build() {
        const el = this._el;

        // -- Hero Section -------------------------------------------------
        const hero = document.createElement('div');
        hero.className = 'home-hero';

        const heroTitle = document.createElement('div');
        heroTitle.className = 'home-hero-title';
        heroTitle.textContent = 'PDF Toolkit';
        hero.appendChild(heroTitle);

        const heroSub = document.createElement('div');
        heroSub.className = 'home-hero-subtitle';
        heroSub.textContent = 'Everything you need to work with PDFs \u2014 fully offline, no account required';
        hero.appendChild(heroSub);

        // Search bar row inside hero actions area
        const heroActions = document.createElement('div');
        heroActions.className = 'home-hero-actions';

        const searchWrap = document.createElement('div');
        searchWrap.className = 'search-bar';
        searchWrap.style.flex = '1';
        searchWrap.style.maxWidth = '400px';

        // Search icon
        const searchIconEl = document.createElement('span');
        searchIconEl.className = 'search-bar-icon';
        searchIconEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.5"/><line x1="10.5" y1="10.5" x2="14" y2="14"/></svg>';
        searchWrap.appendChild(searchIconEl);

        // Search input
        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'input';
        searchInput.placeholder = 'Search tools...';
        searchInput.addEventListener('input', () => this._onSearch(searchInput.value));
        searchWrap.appendChild(searchInput);
        this._searchInput = searchInput;

        // Clear button
        const clearBtn = document.createElement('button');
        clearBtn.className = 'search-bar-clear';
        clearBtn.style.display = 'none';
        clearBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="3" x2="11" y2="11"/><line x1="11" y1="3" x2="3" y2="11"/></svg>';
        clearBtn.addEventListener('click', () => {
            searchInput.value = '';
            clearBtn.style.display = 'none';
            this._onSearch('');
            searchInput.focus();
        });
        searchWrap.appendChild(clearBtn);
        this._clearBtn = clearBtn;

        heroActions.appendChild(searchWrap);

        // Tool count badge
        const toolCount = document.createElement('span');
        toolCount.className = 'badge badge-accent';
        toolCount.style.fontSize = 'var(--font-size-sm)';
        toolCount.style.padding = 'var(--space-1) var(--space-3)';
        toolCount.textContent = (App.tools ? App.tools.length : 0) + ' tools';
        heroActions.appendChild(toolCount);
        this._toolCountEl = toolCount;

        hero.appendChild(heroActions);
        el.appendChild(hero);

        // -- Category Sections --------------------------------------------
        const categories = App.categories || {};
        const tools = App.tools || [];
        const catKeys = Object.keys(categories);

        // Color rotation for icon badges
        const iconColors = ['icon-accent', 'icon-green', 'icon-amber', 'icon-red'];

        this._categorySections = [];

        for (let ci = 0; ci < catKeys.length; ci++) {
            const catKey = catKeys[ci];
            const catLabel = categories[catKey];
            const catTools = tools.filter(function (t) { return t.category === catKey; });
            if (catTools.length === 0) continue;

            // Category header
            const headerEl = document.createElement('div');
            headerEl.className = 'category-header';

            const titleEl = document.createElement('div');
            titleEl.className = 'category-title';
            titleEl.textContent = catLabel;
            headerEl.appendChild(titleEl);

            const countEl = document.createElement('span');
            countEl.className = 'category-count';
            countEl.textContent = catTools.length + ' tool' + (catTools.length !== 1 ? 's' : '');
            titleEl.appendChild(countEl);

            el.appendChild(headerEl);

            // Tool grid
            const gridEl = document.createElement('div');
            gridEl.className = 'tool-grid';

            const cards = [];

            for (let ti = 0; ti < catTools.length; ti++) {
                const tool = catTools[ti];
                const colorClass = iconColors[(ci + ti) % iconColors.length];
                const card = this._createToolCard(tool, colorClass);
                gridEl.appendChild(card.el);
                cards.push(card);
            }

            el.appendChild(gridEl);

            this._categorySections.push({
                key: catKey,
                headerEl: headerEl,
                gridEl: gridEl,
                cards: cards,
            });
        }
    }

    /**
     * Create a single tool card.
     * @returns {{ el: HTMLElement, tool: Object }}
     */
    _createToolCard(tool, colorClass) {
        const card = document.createElement('div');
        card.className = 'card card-interactive tool-card';

        // Top row: icon + arrow
        const topRow = document.createElement('div');
        topRow.style.display = 'flex';
        topRow.style.alignItems = 'center';
        topRow.style.justifyContent = 'space-between';
        topRow.style.width = '100%';

        // Icon badge
        const iconBadge = document.createElement('div');
        iconBadge.className = 'tool-card-icon ' + colorClass;
        iconBadge.innerHTML = getIcon(tool.icon, 22);
        topRow.appendChild(iconBadge);

        // Arrow affordance
        const arrow = document.createElement('span');
        arrow.style.fontSize = 'var(--font-size-xl)';
        arrow.style.color = 'var(--color-text-3)';
        arrow.style.transition = 'transform var(--transition-fast), color var(--transition-fast)';
        arrow.textContent = '\u203A';
        topRow.appendChild(arrow);

        card.appendChild(topRow);

        // Title
        const titleEl = document.createElement('div');
        titleEl.className = 'tool-card-title';
        titleEl.textContent = tool.title;
        card.appendChild(titleEl);

        // Description
        const descEl = document.createElement('div');
        descEl.className = 'tool-card-desc';
        descEl.textContent = tool.description;
        card.appendChild(descEl);

        // Hover: animate arrow
        card.addEventListener('mouseenter', function () {
            arrow.style.transform = 'translateX(3px)';
            arrow.style.color = 'var(--color-accent)';
        });
        card.addEventListener('mouseleave', function () {
            arrow.style.transform = '';
            arrow.style.color = 'var(--color-text-3)';
        });

        // Click -> navigate
        card.addEventListener('click', function () {
            Router.navigate(tool.key);
        });

        return { el: card, tool: tool };
    }

    /* ------------------------------------------------------------------
       Search / Filter
       ------------------------------------------------------------------ */

    _onSearch(query) {
        const q = query.trim().toLowerCase();

        // Show/hide clear button
        if (this._clearBtn) {
            this._clearBtn.style.display = q.length > 0 ? '' : 'none';
        }

        let visibleCount = 0;

        for (const section of this._categorySections) {
            let sectionVisible = false;

            for (const card of section.cards) {
                const t = card.tool;
                const matches = q.length === 0 ||
                    t.title.toLowerCase().indexOf(q) !== -1 ||
                    t.description.toLowerCase().indexOf(q) !== -1;

                card.el.style.display = matches ? '' : 'none';
                if (matches) {
                    sectionVisible = true;
                    visibleCount++;
                }
            }

            section.headerEl.style.display = sectionVisible ? '' : 'none';
            section.gridEl.style.display = sectionVisible ? '' : 'none';
        }

        // Update tool count badge during search
        if (this._toolCountEl) {
            if (q.length > 0) {
                this._toolCountEl.textContent = visibleCount + ' match' + (visibleCount !== 1 ? 'es' : '');
            } else {
                this._toolCountEl.textContent = (App.tools ? App.tools.length : 0) + ' tools';
            }
        }
    }
}

/* --------------------------------------------------------------------------
   Route registration
   -------------------------------------------------------------------------- */

Router.register('home', function () {
    return new HomePage();
});
