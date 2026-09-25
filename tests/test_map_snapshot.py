from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import threading
import unittest
from urllib.request import urlopen

from http.server import ThreadingHTTPServer
from rtt_app.action_api import ActionApplication, make_handler
from rtt_app.map_snapshot import (HEADER, HEIGHT, FOOTER, MARGIN, WIDTH,
                                  SnapshotError, render, viewport, _tile)
from tests.test_action_api import FakeTools, FakeRouteEngine

try:
    from PIL import Image
except ImportError:
    Image = None


class SnapshotAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = ActionApplication(FakeTools(), api_key="test", base_url="https://rail.example",
                                     route_engine=FakeRouteEngine(), map_dir=self.temp.name)
        self.auth = {"Authorization": "Bearer test"}
        self.params = {"origin": ["Bristol Temple Meads"], "destination": ["London Paddington"]}

    def saved_map(self):
        response = self.app.dispatch("GET", "/v1/route", self.params, self.auth)
        return response.body["result"]["mapUrl"].rsplit("/", 1)[-1]

    def test_opt_in_cached_public_image_and_binary_transport(self):
        png = b"\x89PNG\r\n\x1a\n\x00\xff"
        def generate(route, target):
            target.write_bytes(png)
        with patch("rtt_app.railway_service.render_snapshot", side_effect=generate) as renderer:
            map_id = self.saved_map()
            renderer.assert_not_called()
            response = self.app.dispatch("GET", "/v1/route",
                                         {**self.params, "include_snapshot": ["true"]}, self.auth)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["mapImageUrl"],
                             f"https://rail.example/maps/{map_id}.png")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/maps/{map_id}.png") as result:
                self.assertEqual(result.read(), png)
                self.assertEqual(result.headers["Content-Type"], "image/png")
                self.assertIn("max-age", result.headers["Cache-Control"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_authentication_validation_and_no_public_rendering(self):
        map_id = self.saved_map()
        with patch("rtt_app.railway_service.render_snapshot") as renderer:
            self.assertEqual(self.app.dispatch("GET", "/v1/map-snapshot", {"map_id": [map_id]}, {}).status, 401)
            self.assertEqual(self.app.dispatch("GET", f"/maps/{map_id}.png", {}, {}).status, 404)
            for bad in ("../secret", "tiles/1-2-3", "a" * 24):
                self.assertEqual(self.app.dispatch("GET", "/v1/map-snapshot", {"map_id": [bad]}, self.auth).status, 404)
            renderer.assert_not_called()
        self.assertEqual(self.app.dispatch("GET", "/v1/route", {**self.params, "include_snapshot": ["invalid"]}, self.auth).status, 400)

    def test_failed_tiles_preserve_route_and_interactive_link(self):
        with patch("rtt_app.railway_service.render_snapshot", side_effect=SnapshotError("Tiles unavailable")):
            response = self.app.dispatch("GET", "/v1/route", {**self.params, "include_snapshot": ["true"]}, self.auth)
            result = response.body["result"]
            self.assertEqual(response.status, 200)
            self.assertIn("mapUrl", result)
            self.assertNotIn("mapImageUrl", result)
            self.assertEqual(result["snapshotError"], "Tiles unavailable")
            map_id = result["mapUrl"].rsplit("/", 1)[-1]
            response = self.app.dispatch("GET", "/v1/map-snapshot", {"map_id": [map_id]}, self.auth)
            self.assertEqual(response.status, 503)


class BoundsTests(unittest.TestCase):
    def test_entire_route_inside_padded_map_area(self):
        routes = [ [[50.7, -3.5], [58.6, -3.5]], [[51, -5], [51, 1]],
                   [[51, -2], [51, -2]], [[50, -5], [58, 1], [54, -7]] ]
        for route in routes:
            with self.subTest(route=route):
                _, _, _, points = viewport(route)
                for x, y in points:
                    self.assertGreaterEqual(x, MARGIN - 1)
                    self.assertLessEqual(x, WIDTH - MARGIN + 1)
                    self.assertGreaterEqual(y, HEADER + MARGIN - 1)
                    self.assertLessEqual(y, HEIGHT - FOOTER - MARGIN + 1)
        for invalid in ([], [[float("nan"), 0]], [[90, 0]]):
            with self.assertRaises(SnapshotError):
                viewport(invalid)


@unittest.skipIf(Image is None, "Install the snapshots extra for PNG renderer tests")
class RendererTests(unittest.TestCase):
    def test_complete_png_cache_and_failed_tile_atomicity(self):
        route = FakeRouteEngine().route("Bristol", "Paddington", [])
        with TemporaryDirectory() as folder:
            target = Path(folder) / "snapshot.png"
            with patch("rtt_app.map_snapshot._tile", return_value=Image.new("RGB", (256, 256), "#aaccbb")) as tiles:
                render(route, target)
                first_calls = tiles.call_count
                render(route, target)
                self.assertEqual(tiles.call_count, first_calls)
                with Image.open(target) as image:
                    self.assertEqual(image.size, (1200, 800))
                    self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                    self.assertEqual(image.getpixel((0, 400)), (170, 204, 187))
                    self.assertIn((111, 66, 193), image.getdata())
            other = Path(folder) / "failed.png"
            with patch("rtt_app.map_snapshot._tile", side_effect=SnapshotError("missing tile")):
                with self.assertRaises(SnapshotError):
                    render(route, other)
            self.assertFalse(other.exists())

    def test_tile_cache_avoids_network(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "8-126-83.png"
            Image.new("RGB", (256, 256), "green").save(target)
            with patch("rtt_app.map_snapshot.urlopen") as network:
                image = _tile(Path(folder), 8, 126, 83, 0)
                self.assertEqual(image.size, (256, 256))
                network.assert_not_called()
