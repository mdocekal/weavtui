"""Regenerate the README screenshots in docs/images.

Needs the local test Weaviate with sample data and Inkscape for SVG -> PNG:

    docker compose up -d
    uv run python scripts/seed.py --reset
    uv run python scripts/screenshots.py

Textual exports SVG; PNGs are produced with Inkscape because they render the same on
GitHub and PyPI (other converters such as rsvg-convert drop spaces).
"""

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from textual.widgets import DataTable, Input, TabbedContent

from weavtui.app import WeavtuiApp
from weavtui.backend import ConnectionSettings, WeaviateBackend
from weavtui.diagram import DiagramView

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SIZE = (150, 36)


async def capture(out: Path) -> list[Path]:
    shots: list[Path] = []
    app = WeavtuiApp(WeaviateBackend(ConnectionSettings()), start="diagram")

    def save(name: str) -> None:
        shots.append(Path(app.save_screenshot(filename=f"{name}.svg", path=str(out))))

    async with app.run_test(size=SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause(0.5)
        view = app.query_one(DiagramView)
        view.select("WeavtuiArticle")
        view.post_message(DiagramView.Selected("WeavtuiArticle"))
        await app.workers.wait_for_complete()
        await pilot.pause(0.5)
        save("diagram")

        app.query_one(TabbedContent).active = "tab-objects"
        await pilot.pause(0.5)
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        app.query_one(DataTable).focus()
        await pilot.pause(0.3)
        save("objects")

        app.query_one("#search", Input).value = "vector"
        app.query_one("#filter", Input).value = "year >= 2020 and published = true"
        app.query_one("#filter", Input).focus()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.5)
        save("search")

        await pilot.press("enter")  # open the first result
        await pilot.pause(1.0)
        save("object")

        await pilot.press("e")
        await pilot.pause(0.5)
        save("edit")
        await pilot.press("escape", "escape")
        await pilot.pause(0.3)

        app.query_one(TabbedContent).active = "tab-schema"
        await pilot.pause(0.5)
        save("schema")
    return shots


def to_png(svg: Path) -> Path:
    png = svg.with_suffix(".png")
    subprocess.run(
        [
            "inkscape",
            str(svg),
            "--export-type=png",
            f"--export-filename={png}",
            "--export-width=1600",
        ],
        check=True,
        capture_output=True,
    )
    return png


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-svg", action="store_true", help="keep the SVG files too")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    svgs = asyncio.run(capture(OUT))
    if shutil.which("inkscape") is None:
        raise SystemExit("inkscape not found; SVG screenshots are in " + str(OUT))
    for svg in svgs:
        print(to_png(svg).relative_to(OUT.parent.parent))
        if not args.keep_svg:
            svg.unlink()


if __name__ == "__main__":
    main()
