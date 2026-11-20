# AGENTS.md

Orientation for agents: what this is, the layout, the build loop, and the conventions. The plan
and its decisions are in [`PLAN.md`](PLAN.md), the user documentation in [`README.md`](README.md).

## What this is

Kastellan is a self-hosted hub that watches every device on a home network (routers, access
points, servers, PCs, printers, cameras) and is meant to grow into one place to administer them.
It runs locally, reached through a VPN, as one Docker image. The name may change later.

## Layout

- `backend/src/kastellan/` (Python 3.13, FastAPI, managed with uv):
  - `config.py`: `config.yaml` is the only configuration; there is no database. A `Device` has a
    `host`, a `kind`, an optional `parent` (for the map), `tags`, `always_on` (default: false
    for `pc` and `laptop`) and `checks`. A check is written `ping`, `tcp: <port>` or
    `http: <url>` (`Check._shorthand`); its `name` defaults to the type and target.
  - `checks.py`: runs one check and returns a `Result`; it never raises. Ping is unprivileged
    ICMP (icmplib). HTTP does not verify certificates by default (home devices are
    self-signed, and a check sends no credentials); any status below 500 is up unless `expect`.
  - `monitor.py`: one asyncio task per (device, check). `apply(config)` keeps unchanged loops
    running. `restore(rows)` takes the last known state from the history at start, so `since`
    survives restarts; `since` is None when the state began before the history.
  - `history.py`: check results as CSV, one file per UTC day under `<data>/checks/`. The folder
    is the index; retention deletes files. The only place to change if this becomes SQLite.
  - `stats.py`: buckets (uptime share and mean latency per slice) and changes (up/down moments)
    computed from history rows, for the web app.
  - `hub.py`: ties it together; reloads config.yaml when its mtime changes, keeps the last good
    config on an error (`config_error`, shown in the app).
  - `demo.py`: `KASTELLAN_DEMO=1` swaps in a made-up network with simulated results (a pure
    function of device, check and time) and a backfilled day of history in a temp folder. It
    never reads config.yaml or touches the real data. Used for trying it out and the website.
  - `api.py`: `/api/health`, `/api/devices`, `/api/devices/{id}/history`, `/api/series`,
    `/api/changes`, and the web app (single-page, index.html fallback).
- `frontend/` (SvelteKit, static adapter, Svelte 5 runes, Tailwind 4, Lucide icons, Geist):
  - `src/lib/state.svelte.ts`: the store; polls devices every 5 s, buckets and changes every
    30 s, while a page uses it.
  - `src/lib/status.ts`: what a state means to a person. `statusOf` maps a device to
    up / degraded / down / offline / waiting: a down device that isn't `always_on` is
    **offline**, not an alarm, and doesn't count as needing attention.
  - Components: `UptimeStrip` (one bar per bucket, hover tooltip), `LatencyChart` (line + area,
    crosshair), `StatusBadge`/`StatusDot` (color always with a word), `KindIcon`, `Logo`.
  - `src/lib/layout.ts`: the network map's left-to-right tree layout.
  - Routes: `/` overview, `/devices`, `/devices/[id]`, `/map`.
- `website/`: the project website, plain HTML/CSS/JS, published by `.github/workflows/pages.yml`.
  Screenshots in `website/img/` come from demo mode, in light and dark (`<picture>`).

## Build and test

```sh
cd backend && uv sync && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm install && npm run check && npm test && npm run lint && npm run build
# Run: the hub serves the built app from backend/src/kastellan/web
cp -r frontend/build backend/src/kastellan/web
cd backend && KASTELLAN_DEMO=1 uv run kastellan     # http://localhost:8100
# Or the dev server with hot reload (proxies /api to :8100):
cd frontend && npm run dev
```

Node 24 (`frontend/.nvmrc`); older Node fails to install the toolchain.

## Conventions

- Plain files over databases; YAML for configuration.
- Colors: status colors (`--good`, `--warning`, `--critical`, `--offline`) are reserved for
  state and always come with an icon or a word. Healthy uptime cells use the calmer
  `--good-soft`, so problems stand out. Light and dark are both designed (tokens in
  `frontend/src/routes/layout.css`, mirrored in `website/style.css`).
- Check layouts in a browser (both themes, and phone width) before calling UI work done.
- Build on existing protocols and libraries (Guacamole, xterm.js, restic, vendor libraries)
  instead of writing our own.
- Anything that changes a device is admin-only and goes through the audit log (once roles land).
