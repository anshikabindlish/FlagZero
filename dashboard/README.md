# /dashboard — Person C (part 2)

Owns: the trackside/marshal-facing UI that turns `/core`'s WebSocket
messages into an actual flag display, vitals view, etc.

## Stub: `index.html`

A single self-contained HTML file — no build step. With `/core`'s
`fake_event_generator.py` running (see `core/README.md`), just open
`index.html` in a browser (double-click it, or `python -m http.server`
from this folder and visit the printed URL). It connects to
`ws://localhost:8765` and renders every message it receives as raw,
colour-tagged-by-type JSON, auto-reconnecting if the server isn't up yet.

## Replacing the stub

Swap the `appendRaw(...)` rendering in the `ws.onmessage` handler for real
UI per message `type` — `severity_update` → the actual flag/colour
display, `eta_update` → a countdown, etc. The four message shapes are
documented in `../shared/ws_messages.md`; the connection code above
shouldn't need to change as the real UI gets built.
