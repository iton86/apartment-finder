"""Assignment acceptance, freshness contract and local HTTP surface tests."""

import hashlib
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apartment_finder.application.use_cases.planning_unit_map import planning_unit_map
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment
from apartment_finder.interface.map_server import load_assignments, make_map_handler


def assignment(**changes):
    return {
        "listing_id": str(uuid4()),
        "planning_unit_id": "65",
        "release_id": "a" * 64,
        "description_sha256": hashlib.sha256(b"Example").hexdigest(),
        "resolver_version": "resolver-v1",
        "basis": "calibrated",
        "confidence": 0.95,
        "runner_up_confidence": 0.04,
        "calibrator_version": "heldout-v1",
        "evidence": ["Address resolved inside unit"],
        **changes,
    }


def model(**changes):
    return PlanningUnitAssignment.model_validate_json(json.dumps(assignment(**changes)))


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 0.89},
        {"confidence": float("nan")},
        {"confidence": 0.9, "runner_up_confidence": 0.8},
        {"calibrator_version": None},
        {"evidence": [" "]},
        {"release_id": "old"},
        {"planning_unit_id": " "},
        {"confidence": "0.95"},
        {"basis": "heuristic"},
        {"basis": "human_reviewed", "reviewed_by": "Reviewer"},
    ],
)
def test_unaccepted_assignments_rejected(changes):
    with pytest.raises(ValidationError):
        model(**changes)


def test_human_review_has_provenance_without_probability():
    result = model(
        basis="human_reviewed",
        reviewed_by="reviewer-1",
        confidence=None,
        runner_up_confidence=None,
        calibrator_version=None,
    )
    assert result.confidence is None


def test_handoff_and_duplicate_rejection(tmp_path):
    path = tmp_path / "assignments.json"
    row = assignment()
    path.write_text(json.dumps([row]))
    rows = load_assignments(path)
    assert len(rows) == 1
    with pytest.raises(ValueError, match="one accepted"):
        planning_unit_map(None, rows + rows)
    assert load_assignments(None) == []


@pytest.fixture
def http_map():
    class Repository:
        def map_data(self, assignments):
            return {"ads": [], "units": {"type": "FeatureCollection", "features": []}}

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_map_handler(Repository()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(port, path, method="GET", host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(method, path, headers={"Host": host} if host else {})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_local_read_only_http_routes(http_map):
    status, headers, body = request(http_map, "/")
    assert status == 200 and b"Planning unit explorer" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(http_map, "/api/map")[0] == 200
    assert request(http_map, "/vendor/leaflet.js")[0] == 200
    assert request(http_map, "/../../.env")[0] == 404
    assert request(http_map, "/api/map", host="attacker.example")[0] == 403
    assert request(http_map, "/api/map", method="POST")[0] == 501
