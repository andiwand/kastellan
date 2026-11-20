# Changelog

## Unreleased (0.1.0)

- Checks: ping, TCP and HTTP per device, on an interval; config.yaml reloads by itself.
- History as daily CSV files, with retention; state survives restarts.
- Web app: overview, device list, device pages with uptime and response time; devices as a list or a
  graph of how they are connected.
  Light and dark, phone and desktop.
- Services: every service with the software and version it reports (SSH banners, HTTP
  headers and titles), status, how long, and uptime; software updates appear in the activity.
- Demo mode (`KASTELLAN_DEMO=1`): a made-up network with a day of simulated history.
- Project website (`website/`).
