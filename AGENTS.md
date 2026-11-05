# AGENTS.md

Guidance for AI coding agents (and humans) working on weavtui, a Textual TUI for
exploring Weaviate databases. User-facing docs are in `README.md`.

## Stack

- Python 3.13 (`requires-python = ">=3.13"`), managed with **uv**; build backend `uv_build`.
- [Textual](https://textual.textualize.io) 8.x for the UI, `weaviate-client` 4.x (v4 API).
- Tested against Weaviate 1.40 (see `docker-compose.yml`).
- Released into the public domain (Unlicense).

## Commands

```bash
uv sync                                   # install deps incl. dev group
uv run weavtui [--start browse]           # run against localhost:8080 / 50051
uv run pytest -q                          # all tests (~20 s)
uv run ruff check . && uv run ruff format .
docker compose up -d                      # local Weaviate for integration tests
uv run python scripts/seed.py --reset     # sample data: 3 collections, aliases, references
uv run python scripts/screenshots.py      # regenerate docs/images (needs seeded DB + inkscape)
scripts/publish.sh [--test]               # release to (Test)PyPI
```

Before finishing a change, run ruff (check and format) and the full test suite. Ruff is
configured in `pyproject.toml` (line length 100, rules E, F, I, UP, B, SIM).

## Layout

| Path | Purpose |
| --- | --- |
| `src/weavtui/cli.py` | argparse entry point (`weavtui` script); options also read `WEAVTUI_*` env vars |
| `src/weavtui/backend.py` | **Only** place that talks to Weaviate. `Backend` protocol, `WeaviateBackend`, dataclasses (`CollectionInfo`, `ObjectRecord`, `Page`, ...), `BackendError` |
| `src/weavtui/filters.py` | Small filter language → `weaviate.classes.query.Filter` |
| `src/weavtui/app.py` | `WeavtuiApp`: sidebar + tabs (Diagram, Objects, Schema), paging, CRUD actions |
| `src/weavtui/diagram.py` | Schema diagram: layered layout, box-drawing renderer, `DiagramView` widget, Mermaid export |
| `src/weavtui/references.py` | Reference graph helpers and labels used in the Schema tab |
| `src/weavtui/screens.py` | Object view, JSON editor, confirm / save-path / connection-error dialogs |
| `src/weavtui/theme.py` | `weaviate` / `weaviate-light` themes (terminal background, Weaviate green) |
| `src/weavtui/weavtui.tcss` | Textual CSS |
| `tests/fake_backend.py` | In-memory `Backend` used by UI tests |
| `tests/test_integration.py` | Real-Weaviate tests; auto-skipped when no server is reachable |
| `scripts/` | `seed.py`, `screenshots.py`, `publish.sh` |

## Architecture rules

- **UI never imports or calls the Weaviate client directly.** Add a method to the
  `Backend` protocol, implement it in `WeaviateBackend` (wrap errors in `BackendError` with
  a user-readable message), and implement it in `tests/fake_backend.py`.
- Backend calls are synchronous and slow; call them from Textual workers via
  `await asyncio.to_thread(self.backend.method, ...)` inside `@work(exclusive=True, group=...)`
  methods. Never block the event loop.
- Dialogs that return a value use `await self.push_screen_wait(...)` inside a worker.
- Values from Weaviate (datetime, UUID, GeoCoordinate, ...) go through
  `backend.to_jsonable` before being shown or edited as JSON. Edits send only changed keys
  (`data.update`, PATCH semantics); a removed key is sent as `None`.
- Main-screen shortcuts are disabled over modal screens and some are hidden on the Diagram
  tab, both via `WeavtuiApp.check_action`; call `refresh_bindings()` when that state
  changes.
- The diagram and the sidebar stay in sync: `DiagramView.Selected` → sidebar highlight →
  `select_collection` → `DiagramView.select`. Avoid loops by making `select` a no-op for the
  current name.
- Colours: use theme variables in TCSS. Rich `Text` styles cannot use `$primary`; pass
  concrete colours (see `DiagramView._colors`, `_rich_color` for `ansi_*` names).

## Textual pitfalls we have hit

- **Do not shadow Textual internals.** `App._resize_timer` and `Widget._render` exist;
  overriding them broke resizing and rendering. Check with
  `hasattr(textual.app.App, name)` / `hasattr(textual.widget.Widget, name)` before adding a
  private attribute or method to an App, Screen or Widget subclass.
- `App` does not receive resize events for its children; listen on the widget (see
  `ObjectsTable.on_resize`).
- A container next to a fixed-width sidebar needs `width: 1fr`, or it is 100 % wide and
  its right part is off-screen.
- With the ANSI-based themes, Textual needs `ansi-background` / `ansi-foreground` theme
  variables.

## Tests

- UI tests use `app.run_test(size=...)` with `FakeBackend`. Construct the app with
  `start="browse"` unless the test is about the diagram (the default start tab is the
  diagram).
- `await app.workers.wait_for_complete()` hangs while a worker awaits a dialog
  (`push_screen_wait`); use `pilot.pause()` in that case.
- Page size depends on the terminal height (`page_size_for`); tests use `SIZE = (160, 24)`
  → 20 rows per page with 60 fake objects.
- Diagram layout tests check invariants (no overlapping boxes, no lines under boxes, an
  arrow for every reference) rather than exact pictures; keep them passing when changing
  the layout.
- Integration tests create and delete their own `WeavtuiTest<random>` collection; never
  touch other collections in a shared database.

## Screenshots

`app.save_screenshot()` writes SVG. Convert with Inkscape (`scripts/screenshots.py`);
`rsvg-convert` drops spaces and makes correct output look broken.
