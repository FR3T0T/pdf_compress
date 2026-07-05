import { defineConfig } from 'vite';
import type { Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// Vite's HTML injector hardcodes type="module" on the entry <script> tag for
// standard (non-lib) builds, regardless of the Rollup output.format above —
// so the IIFE setting alone doesn't stop Chromium from treating it as a
// module subject to file:// CORS restrictions. Strip type="module"/
// crossorigin post-injection so the tag is a plain classic script (defer
// preserves the same "runs after parse" timing as type="module" did).
function classicScriptTag(): Plugin {
  return {
    name: 'classic-script-tag',
    transformIndexHtml: {
      order: 'post',
      handler(html) {
        return html
          .replace(/<script type="module" crossorigin src="/g, '<script defer src="')
          .replace(/ crossorigin href="\.\/assets/g, ' href="./assets');
      },
    },
  };
}

// Production target: bundled assets loaded via file:// (QUrl.fromLocalFile,
// ui/web_shell.py) inside a QWebEngineView with `script-src 'self' qrc:` and
// `connect-src 'none'`. Every setting below exists to satisfy that:
//   - base: './'            -> relative asset URLs, never absolute /assets/
//   - modulePreload.polyfill: false -> no inline <script> injected into index.html
//   - output.format: 'iife' -> a classic <script> bundle, NOT type="module".
//     web/index.html has a load-bearing comment ("no ES6 modules on
//     file://") — Chromium's module loader requires CORS that file:// origins
//     can't satisfy, so an ES module entry would silently fail to execute
//     under QUrl.fromLocalFile(). IIFE avoids that failure mode entirely.
//   - no CDN/external resources anywhere in the source
export default defineConfig({
  plugins: [react(), classicScriptTag()],
  base: './',
  // Respect an externally-assigned PORT (e.g. from a preview harness) for
  // `vite dev`; irrelevant to the production build config above.
  server: {
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    modulePreload: {
      polyfill: false,
    },
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        format: 'iife',
        // Single self-contained file — no dynamic import()/code-splitting
        // in this app, so one entry chunk is all IIFE format needs.
        inlineDynamicImports: true,
        entryFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
});
