# Horizon website

Public product and download website. Replaces the previous agent GUI; it does not connect to the local API or read user settings.

Requires Node.js 22.12+ for website development.

```sh
cd frontend
npm ci
npm run dev
```

`npm run build` produces static files in `frontend/dist`. Deploy this directory to any static host. Assets use relative URLs for subdirectory hosting.

Release data loads from GitHub's public releases API, with a checked-in `public/releases.json` fallback. Refresh the fallback from the public releases endpoint when releasing the site. Download links use actual release assets. Supports Chinese/English, light/dark theme, installer command copy, OS switching and release search. Scroll reveals play once when content first enters the viewport; scrolling back does not replay them. Release history uses a bounded vertical arc with spring animation, wheel/keyboard navigation, touch swipes. The public API loads up to 100 stable releases; older archives remain available through the GitHub releases link.

Original TUI screenshots are reused from `docs/assets`. The illustrated terminal in the hero is a presentation, not an interactive agent.

GitHub Pages publishes automatically from `main` website changes and public releases using `.github/workflows/website.yml`. Public URL: https://01ny-yzx.github.io/horizon-harness/ .
