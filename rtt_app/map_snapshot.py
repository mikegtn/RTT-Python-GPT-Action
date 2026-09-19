"""On-demand, whole-route PNGs with a bounded, shared OSM tile cache.

Pillow is optional: normal API and interactive maps work without it. Only an
authenticated Action request calls render(); public URLs serve completed files.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import math
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


WIDTH, HEIGHT = 1200, 800
HEADER, FOOTER, MARGIN = 80, 32, 48
TILE_TTL = 7 * 24 * 60 * 60
_render_slot = threading.BoundedSemaphore(1)


class SnapshotError(Exception):
    """A snapshot could not be completed; the interactive map remains usable."""


def project(coordinate):
    lat, lon = map(float, coordinate)
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 <= lat <= 85 and -180 <= lon <= 180):
        raise SnapshotError("Invalid map coordinates")
    return (lon + 180) / 360, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2


def viewport(coordinates):
    """Return zoom, world-pixel top-left and screen coordinates for every vertex."""
    if not coordinates:
        raise SnapshotError("The route has no map geometry")
    points = [project(p) for p in coordinates]
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    for zoom in range(16, -1, -1):
        scale = 256 * 2**zoom
        if ((xmax - xmin) * scale <= WIDTH - 2 * MARGIN and
                (ymax - ymin) * scale <= HEIGHT - HEADER - FOOTER - 2 * MARGIN):
            break
    left = round((xmin + xmax) * scale / 2 - WIDTH / 2)
    top = round((ymin + ymax) * scale / 2 - (HEIGHT - HEADER - FOOTER) / 2)
    screen = [(x * scale - left, y * scale - top + HEADER) for x, y in points]
    return zoom, left, top, screen


def _tile(cache: Path, z: int, x: int, y: int, deadline: float):
    from PIL import Image

    target = cache / f"{z}-{x}-{y}.png"
    metadata = target.with_suffix(".json")
    if target.exists() and time.time() - target.stat().st_mtime < TILE_TTL:
        try:
            with Image.open(target) as tile:
                if tile.size == (256, 256):
                    return tile.convert("RGB")
        except OSError:
            pass
    headers = {"User-Agent": "RTT-Route-Snapshot/1.0 (+https://rail.mikegtn.net/privacy)",
               "Referer": "https://rail.mikegtn.net/"}
    try:
        saved = json.loads(metadata.read_text())
        if target.exists():
            for name in ("ETag", "Last-Modified"):
                if saved.get(name):
                    headers[{"ETag": "If-None-Match", "Last-Modified": "If-Modified-Since"}[name]] = saved[name]
    except (OSError, ValueError):
        pass
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SnapshotError("Map tiles took too long to load; try again")
    try:
        request = Request(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png", headers=headers)
        with urlopen(request, timeout=min(4, remaining)) as response:
            data = response.read(1_000_001)
            validators = {key: response.headers.get(key) for key in ("ETag", "Last-Modified")}
    except HTTPError as exc:
        if exc.code != 304 or not target.exists():
            raise SnapshotError("Map tiles are temporarily unavailable; try again") from exc
        target.touch()
        with Image.open(target) as tile:
            return tile.convert("RGB")
    if len(data) > 1_000_000:
        raise SnapshotError("Invalid map tile response")
    with Image.open(BytesIO(data)) as tile:
        if tile.size != (256, 256):
            raise SnapshotError("Invalid map tile size")
        result = tile.convert("RGB")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    metadata.write_text(json.dumps(validators))
    return result


def render(route: dict, target: Path) -> None:
    """Atomically publish a complete PNG, never a partially loaded map."""
    if target.exists():
        return
    if not _render_slot.acquire(blocking=False):
        raise SnapshotError("Another map snapshot is rendering; try again shortly")
    try:
        if target.exists():
            return
        from PIL import Image, ImageDraw, ImageFont

        coords = route.get("coordinates") or []
        if not coords:
            raise SnapshotError("The route has no map geometry")
        markers = [p for p in route.get("points", []) if p.get("coordinate")]
        zoom, left, top, screen = viewport(coords + [p["coordinate"] for p in markers])
        cache = target.parent / "tiles"
        cache.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (WIDTH, HEIGHT), "#edf2f5")
        map_height = HEIGHT - HEADER - FOOTER
        jobs = [(x, y) for x in range(left // 256, (left + WIDTH - 1) // 256 + 1)
                for y in range(top // 256, (top + map_height - 1) // 256 + 1)
                if 0 <= y < 2**zoom]
        deadline = time.monotonic() + 18
        def fetch(job):
            x, y = job
            return x, y, _tile(cache, zoom, x % 2**zoom, y, deadline)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for x, y, tile in pool.map(fetch, jobs):
                image.paste(tile, (x * 256 - left, y * 256 - top + HEADER))
        draw = ImageDraw.Draw(image)
        route_pixels = screen[:len(coords)]
        if len(route_pixels) > 1:
            draw.line(route_pixels, fill="white", width=9, joint="curve")
            draw.line(route_pixels, fill="#6f42c1", width=5, joint="curve")
        for index, point in enumerate(markers):
            x, y = screen[len(coords) + index]
            radius = 6 if point.get("role") == "via" else 9
            draw.ellipse((x-radius, y-radius, x+radius, y+radius),
                         fill="#6f42c1" if radius == 6 else "#111111", outline="white", width=2)
        def font(size):
            for name in ("DejaVuSans.ttf", "Arial.ttf"):
                try:
                    return ImageFont.truetype(name, size)
                except OSError:
                    pass
            return ImageFont.load_default()
        def fit_text(text, size, width):
            chosen = font(size)
            while draw.textlength(text, font=chosen) > width and len(text) > 1:
                text = text[:-2] + "…"
            return text, chosen
        draw.rectangle((0, 0, WIDTH, HEADER-1), fill="white")
        title, title_font = fit_text(f"{route.get('origin', 'Origin')} to {route.get('destination', 'Destination')}", 24, WIDTH-40)
        draw.text((20, 12), title, font=title_font, fill="#111111")
        draw.text((20, 47), f"{route.get('mileage', '?')} railway miles · topology-based suggested route",
                  font=font(17), fill="#444444")
        draw.rectangle((0, HEIGHT-FOOTER, WIDTH, HEIGHT), fill="white")
        attribution = "© OpenStreetMap contributors · openstreetmap.org/copyright"
        draw.text((WIDTH-12-draw.textlength(attribution, font=font(14)), HEIGHT-25),
                  attribution, font=font(14), fill="#333333")
        temporary = target.with_suffix(".png.tmp")
        image.save(temporary, format="PNG")
        temporary.replace(target)
    except SnapshotError:
        raise
    except ImportError as exc:
        raise SnapshotError("Map snapshots are not configured on this server") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise SnapshotError("The map snapshot could not be completed; try again") from exc
    finally:
        _render_slot.release()
