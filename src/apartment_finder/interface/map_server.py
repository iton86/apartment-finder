"""Local read-only planning-unit map server; no database credentials in the browser."""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from apartment_finder.application.use_cases.planning_unit_map import planning_unit_map
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment

logger = logging.getLogger(__name__)
MAX_ASSIGNMENTS = 5000


def load_assignments(path: Path | None) -> list[PlanningUnitAssignment]:
    """Read a bounded, explicit resolver handoff; a missing file means no ads."""
    if path is None:
        return []
    if path.stat().st_size > 10_000_000:
        raise ValueError("Assignment file exceeds 10 MB")
    raw = path.read_text()
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) > MAX_ASSIGNMENTS:
        raise ValueError("Expected at most 5,000 assignments")
    return [PlanningUnitAssignment.model_validate_json(json.dumps(row)) for row in rows]


def make_map_handler(repository, assignments_path: Path | None = None):
    """Create a handler exposing only allowlisted static assets and one read endpoint."""
    assets = files("apartment_finder.interface").joinpath("map_assets")
    static = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
        "/vendor/leaflet.js": ("vendor/leaflet.js", "text/javascript; charset=utf-8"),
        "/vendor/leaflet.css": ("vendor/leaflet.css", "text/css; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """Serve current data or a packaged static asset without arbitrary file access."""
            # Mitigate browser DNS rebinding to the loopback-only development server.
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"localhost", "127.0.0.1"}:
                self.send_error(403)
                return
            if self.path == "/api/map":
                try:
                    data = planning_unit_map(
                        repository, load_assignments(assignments_path) if assignments_path else None
                    )
                    body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
                    self._respond(200, "application/json; charset=utf-8", body)
                except Exception:
                    logger.exception("Planning-unit map data could not be loaded")
                    self._respond(
                        503,
                        "application/json",
                        json.dumps(
                            {"error": "Map unavailable. Check the catalogue and server logs."}
                        ).encode(),
                    )
            elif self.path in static:
                name, content_type = static[self.path]
                self._respond(200, content_type, assets.joinpath(name).read_bytes())
            else:
                self.send_error(404)

        def _respond(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https://tile.openstreetmap.org; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve_map(repository, port: int, assignments_path: Path | None = None) -> None:
    """Serve on loopback until interrupted; the caller owns the engine lifecycle."""
    if not 1024 <= port <= 65535:
        raise ValueError("Map port must be between 1024 and 65535")
    load_assignments(assignments_path)  # Fail before starting on malformed input.
    with ThreadingHTTPServer(
        ("127.0.0.1", port), make_map_handler(repository, assignments_path)
    ) as server:
        print(f"Planning-unit map: http://127.0.0.1:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
