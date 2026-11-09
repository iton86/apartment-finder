"""Opt-in Chromium checks for unit-only pins, filtering and safe ad rendering."""

import os
import threading
from http.server import ThreadingHTTPServer

import pytest
from playwright.sync_api import expect, sync_playwright

from apartment_finder.interface.map_server import make_map_handler

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 with Playwright Chromium installed",
)


def test_map_markers_filters_mobile_and_failure():
    class Repository:
        fail = False

        def map_data(self, assignments):
            if self.fail:
                raise ValueError("Test source failure")
            units = []
            for identifier, x in [("1", 23.3), ("2", 23.32)]:
                units.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "id": identifier,
                            "name": "Test unit " + identifier,
                            "marker": [x + 0.005, 42.695],
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [x, 42.69],
                                    [x + 0.01, 42.69],
                                    [x + 0.01, 42.7],
                                    [x, 42.7],
                                    [x, 42.69],
                                ]
                            ],
                        },
                    }
                )
            ads = [
                {
                    "listing_id": str(i),
                    "title": title,
                    "url": None,
                    "planning_unit_id": unit,
                    "confidence": confidence,
                    "basis": basis,
                    "evidence": ["Test evidence"],
                    "price": "100000",
                    "currency": "EUR",
                }
                for i, (title, unit, confidence, basis) in enumerate(
                    [
                        ("Test ad <img src=x onerror=alert(1)>", "1", 0.94, "calibrated"),
                        ("Test ad 2", "2", 0.99, "calibrated"),
                        ("Test reviewed ad", "1", None, "human_reviewed"),
                    ]
                )
            ]
            return {
                "release_id": "a" * 64,
                "units": {"type": "FeatureCollection", "features": units},
                "ads": ads,
                "excluded_assignments": 0,
            }

    repository = Repository()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_map_handler(repository))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator("#ad-count")).to_have_text("3")
            expect(page.locator(".pin-count")).to_have_count(2)
            page.get_by_role("button", name="PLANNING UNIT 1", exact=False).click()
            expect(page.locator(".ad-card")).to_have_count(2)
            expect(page.locator(".ad-card img")).to_have_count(0)
            page.locator("#confidence").fill("99")
            expect(page.locator("#ad-count")).to_have_text("2")
            expect(page.locator(".ad-card")).to_have_count(1)
            expect(page.locator(".ad-badge")).to_have_text("Human reviewed")
            page.locator("#clear").click()
            page.locator("#search").fill("unit 2")
            expect(page.locator(".unit-card")).to_have_count(1)
            page.locator("#reset").click()
            expect(page.locator(".unit-card")).to_have_count(2)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("body").bounding_box()["width"] == 390
            repository.fail = True
            page.locator("#reload").click()
            expect(page.locator("#status")).to_contain_text("Could not load")
            expect(page.locator(".pin-count")).to_have_count(0)
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
