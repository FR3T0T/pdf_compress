// icons.js - Inline SVG icon library for PDF Toolkit web UI
// Replaces QPainter-drawn icons from the old Qt UI
// All icons use stroke style on a 24x24 viewBox

const ICONS = {
    home: `<path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>`,

    compress: `<path d="M12 3v14m0 0l-4-4m4 4l4-4"/>
        <line x1="8" y1="21" x2="16" y2="21"/>
        <line x1="6" y1="21" x2="6" y2="17"/>
        <line x1="18" y1="21" x2="18" y2="17"/>`,

    merge: `<rect x="2" y="3" width="7" height="9" rx="1"/>
        <rect x="15" y="3" width="7" height="9" rx="1"/>
        <path d="M5.5 12v2l6.5 5 6.5-5v-2"/>
        <rect x="8" y="17" width="8" height="4" rx="1"/>`,

    split: `<rect x="8" y="2" width="8" height="5" rx="1"/>
        <path d="M12 7v3m0 0l-6.5 4v2m6.5-6l6.5 4v2"/>
        <rect x="2" y="14" width="7" height="8" rx="1"/>
        <rect x="15" y="14" width="7" height="8" rx="1"/>`,

    lock: `<rect x="5" y="11" width="14" height="10" rx="2"/>
        <path d="M8 11V7a4 4 0 0 1 8 0v4"/>
        <circle cx="12" cy="16" r="1.5"/>`,

    unlock: `<rect x="5" y="11" width="14" height="10" rx="2"/>
        <path d="M8 11V7a4 4 0 0 1 7.83-1"/>
        <circle cx="12" cy="16" r="1.5"/>`,

    image: `<rect x="3" y="3" width="18" height="18" rx="2"/>
        <circle cx="8.5" cy="8.5" r="1.5"/>
        <path d="M21 15l-5-5L5 21"/>`,

    image_to_pdf: `<rect x="2" y="3" width="9" height="9" rx="1"/>
        <circle cx="5" cy="6.5" r="1"/>
        <path d="M11 9l-3-3-4 6"/>
        <path d="M13 7.5h5.5m0 0l-2.5-2.5m2.5 2.5L16 10"/>
        <rect x="14" y="12" width="8" height="10" rx="1"/>
        <path d="M17 15h2m-2 2h2m-2 2h1"/>`,

    word: `<rect x="4" y="2" width="16" height="20" rx="2"/>
        <text x="12" y="16" text-anchor="middle" font-size="10" font-weight="bold" font-family="sans-serif" fill="none" stroke-width="1">W</text>`,

    pages: `<rect x="6" y="4" width="14" height="18" rx="2"/>
        <path d="M4 8V4a2 2 0 0 1 2-2h10"/>
        <line x1="10" y1="9" x2="16" y2="9"/>
        <line x1="10" y1="13" x2="16" y2="13"/>
        <line x1="10" y1="17" x2="14" y2="17"/>`,

    crop: `<path d="M6 2v4H2m20 12h-4v4"/>
        <path d="M6 6h12a2 2 0 0 1 2 2v10"/>
        <path d="M18 18H6a2 2 0 0 1-2-2V6"/>`,

    flatten: `<rect x="4" y="2" width="16" height="20" rx="2"/>
        <path d="M9 10h6m-3-3v6"/>
        <path d="M8 18h8"/>
        <path d="M12 15v3"/>`,

    grid: `<rect x="3" y="3" width="8" height="8" rx="1"/>
        <rect x="13" y="3" width="8" height="8" rx="1"/>
        <rect x="3" y="13" width="8" height="8" rx="1"/>
        <rect x="13" y="13" width="8" height="8" rx="1"/>`,

    watermark: `<rect x="4" y="2" width="16" height="20" rx="2"/>
        <line x1="8" y1="18" x2="18" y2="5" opacity="0.5"/>
        <line x1="6" y1="19" x2="14" y2="9" opacity="0.5"/>
        <line x1="10" y1="19" x2="19" y2="8" opacity="0.5"/>`,

    numbers: `<rect x="4" y="2" width="16" height="20" rx="2"/>
        <text x="12" y="16" text-anchor="middle" font-size="11" font-weight="bold" font-family="sans-serif" fill="none" stroke-width="1.2">#</text>`,

    metadata: `<circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="16" x2="12" y2="12"/>
        <circle cx="12" cy="8" r="0.5"/>`,

    extract_img: `<rect x="3" y="2" width="12" height="16" rx="1"/>
        <path d="M6 13l2.5-3 2.5 3"/>
        <circle cx="7" cy="7" r="1.5"/>
        <path d="M15 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-3"/>
        <path d="M15 8l3 3m-3-3v3h3"/>`,

    extract_text: `<rect x="3" y="2" width="12" height="16" rx="1"/>
        <line x1="6" y1="6" x2="12" y2="6"/>
        <line x1="6" y1="9" x2="11" y2="9"/>
        <line x1="6" y1="12" x2="10" y2="12"/>
        <path d="M15 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-3"/>
        <path d="M15 8l3 3m-3-3v3h3"/>`,

    repair: `<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94L6.73 20.15a2.13 2.13 0 0 1-3-3l6.72-6.72a6 6 0 0 1 7.94-7.94z"/>`,

    compare: `<rect x="2" y="3" width="8" height="11" rx="1"/>
        <rect x="14" y="3" width="8" height="11" rx="1"/>
        <path d="M10 8h4m0 0l-1.5-1.5M14 8l-1.5 1.5"/>
        <line x1="5" y1="6" x2="7" y2="6"/>
        <line x1="5" y1="8" x2="7" y2="8"/>
        <line x1="5" y1="10" x2="7" y2="10"/>
        <line x1="17" y1="6" x2="19" y2="6"/>
        <line x1="17" y1="8" x2="19" y2="8"/>
        <line x1="17" y1="10" x2="19" y2="10"/>
        <path d="M6 17h12m0 0l-2-2m2 2l-2 2"/>`,

    sun: `<circle cx="12" cy="12" r="4"/>
        <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/>`,

    moon: `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>`,

    menu: `<line x1="4" y1="6" x2="20" y2="6"/>
        <line x1="4" y1="12" x2="20" y2="12"/>
        <line x1="4" y1="18" x2="20" y2="18"/>`,

    upload: `<path d="M16 16c2.21 0 4-1.79 4-4a4 4 0 0 0-3.2-3.92A5.5 5.5 0 0 0 6.5 9.5 3.5 3.5 0 0 0 4 16"/>
        <path d="M12 12v8"/>
        <path d="M8 14l4-4 4 4"/>`,

    redact: `<rect x="4" y="2" width="16" height="20" rx="2"/>
        <rect x="7" y="7" width="10" height="2.5" rx="0.5" fill="currentColor" stroke="none"/>
        <rect x="7" y="12" width="8" height="2.5" rx="0.5" fill="currentColor" stroke="none"/>
        <rect x="7" y="17" width="6" height="2.5" rx="0.5" fill="currentColor" stroke="none"/>`,
};

/**
 * Returns an SVG string for the given icon name.
 * @param {string} name - Icon name (e.g. 'home', 'compress')
 * @param {number} [size=20] - Width and height in pixels
 * @param {string} [color='currentColor'] - Stroke/fill color
 * @returns {string} SVG markup string, or empty string if icon not found
 */
function getIcon(name, size = 20, color = 'currentColor') {
    const paths = ICONS[name];
    if (!paths) {
        console.warn(`[icons] Unknown icon: "${name}"`);
        return '';
    }

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

/**
 * Returns a DOM element (span) containing the rendered SVG icon.
 * @param {string} name - Icon name
 * @param {number} [size=20] - Width and height in pixels
 * @param {string} [color='currentColor'] - Stroke/fill color
 * @returns {HTMLSpanElement} Span element with the SVG as innerHTML
 */
function getIconEl(name, size = 20, color = 'currentColor') {
    const span = document.createElement('span');
    span.className = 'icon';
    span.setAttribute('aria-hidden', 'true');
    span.style.display = 'inline-flex';
    span.style.alignItems = 'center';
    span.style.justifyContent = 'center';
    span.style.width = `${size}px`;
    span.style.height = `${size}px`;
    span.style.verticalAlign = 'middle';
    span.innerHTML = getIcon(name, size, color);
    return span;
}

// Export for ES module usage if available
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { getIcon, getIconEl, ICONS };
}
