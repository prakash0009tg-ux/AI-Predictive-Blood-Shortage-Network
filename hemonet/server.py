"""HTTP REST, Server-Sent Events (SSE), OpenAPI JSON, and static web server for HemoNet."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import queue
import threading
import time
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .domain import BLOOD_TYPES, COMPONENTS
from .engine import HemoNetEngine

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_openapi_spec() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HemoNet Global Predictive Blood Network API",
            "version": "1.0.0",
            "description": (
                "Predicts hospital blood shortages (24h-14d), matches compatible blood banks via "
                "Min-Cost Max-Flow optimization, ingests HL7 FHIR R4 & ISBT 128 feeds, and drives "
                "continuous donor motivation campaigns."
            ),
        },
        "paths": {
            "/api/overview": {"get": {"summary": "Fetch full network state, forecasts, alerts, transfers, and donor feed"}},
            "/api/facilities": {
                "get": {"summary": "List connected global hospitals and blood banks"},
                "post": {"summary": "Connect a new hospital or blood bank to HemoNet"},
            },
            "/api/inventory/sync": {"post": {"summary": "Update facility blood inventory count"}},
            "/api/fhir/ingest": {"post": {"summary": "Ingest HL7 FHIR R4 Bundle (PHI stripped automatically)"}},
            "/api/isbt128/parse": {"post": {"summary": "Decode ISBT 128 barcode payload"}},
            "/api/simulate": {"post": {"summary": "Run what-if surge scenario across forecasting & optimizer"}},
            "/api/transfers/{transfer_id}/transition": {"post": {"summary": "Transition transfer state (PROPOSED -> APPROVED -> IN_TRANSIT -> RECEIVED)"}},
            "/api/donor/feed": {"get": {"summary": "Get live rotating donor motivation scheme & urgent callouts"}},
            "/api/donor/dialog": {"post": {"summary": "Interactive donor motivation & eligibility dialog"}},
            "/api/donor/pledges": {"post": {"summary": "Submit a blood donation pledge"}},
            "/api/inventory/monitor": {"get": {"summary": "Real-time inventory monitoring, historic consumption, and blood group matrix"}},
            "/api/ecosystem": {"get": {"summary": "Compiled WHO-verified doctors, medical colleges, drug manufacturers, and experts"}},
            "/api/caretaker/requests": {"get": {"summary": "List caretaker-to-specialist doctor connection tickets"}},
            "/api/caretaker/connect": {"post": {"summary": "Match and connect a patient caretaker/guardian to a specialist blood-group doctor"}},
            "/api/events": {"get": {"summary": "Server-Sent Events stream for real-time alerts and donor scheme ticks"}},
        },
    }


class SSEBroadcaster:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: List[queue.Queue[str]] = []

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=25)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, event: str, data: Dict[str, Any]) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            dead = []
            for q in self._clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._clients.remove(q)


def build_request_handler(engine: HemoNetEngine, broadcaster: SSEBroadcaster) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # quiet clean stdout

        def _send_json(self, status: int, body: Any) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _serve_static(self, rel_path: str) -> None:
            safe_rel = rel_path.lstrip("/") or "index.html"
            if ".." in safe_rel:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid path"})
                return
            full = os.path.join(STATIC_DIR, safe_rel)
            if not os.path.isfile(full):
                full = os.path.join(STATIC_DIR, "index.html")
            ctype, _ = mimetypes.guess_type(full)
            with open(full, "rb") as f:
                data = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "blood_types": BLOOD_TYPES, "components": list(COMPONENTS.keys())})
                return
            if path == "/openapi.json":
                self._send_json(HTTPStatus.OK, build_openapi_spec())
                return
            if path == "/api/overview":
                self._send_json(HTTPStatus.OK, engine.get_snapshot())
                return
            if path == "/api/facilities":
                self._send_json(HTTPStatus.OK, {"facilities": engine.get_snapshot()["facilities"]})
                return
            if path == "/api/alerts":
                self._send_json(HTTPStatus.OK, {"alerts": engine.get_snapshot()["alerts"]})
                return
            if path == "/api/transfers":
                self._send_json(HTTPStatus.OK, {"transfers": engine.get_snapshot()["transfers"]})
                return
            if path == "/api/donor/feed":
                self._send_json(HTTPStatus.OK, engine.get_snapshot()["donor_feed"])
                return
            if path == "/api/inventory/monitor":
                qs = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    engine.get_inventory_monitor(
                        blood_type=qs.get("blood_type", [""])[0],
                        component=qs.get("component", [""])[0],
                        region=qs.get("region", [""])[0],
                    ),
                )
                return
            if path == "/api/ecosystem":
                qs = parse_qs(parsed.query)
                who_only = qs.get("who_only", ["false"])[0].lower() == "true"
                self._send_json(
                    HTTPStatus.OK,
                    engine.query_ecosystem(
                        region=qs.get("region", ["ALL"])[0],
                        search=qs.get("search", [""])[0],
                        who_only=who_only,
                    ),
                )
                return
            if path == "/api/caretaker/requests":
                self._send_json(HTTPStatus.OK, {"requests": engine.get_snapshot().get("caretaker_requests", [])})
                return
            if path == "/api/events":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = broadcaster.subscribe()
                try:
                    snap = engine.get_snapshot()
                    init_msg = f"event: donor_tick\ndata: {json.dumps(snap['donor_feed'])}\n\n"
                    self.wfile.write(init_msg.encode("utf-8"))
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15)
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    broadcaster.unsubscribe(q)
                return

            self._serve_static(path)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json()
                if path == "/api/facilities":
                    created = engine.connect_facility(body)
                    broadcaster.publish("network_update", {"reason": "facility_connected", "facility": created["name"]})
                    self._send_json(HTTPStatus.CREATED, {"facility": created, "overview": engine.get_snapshot()})
                    return

                if path == "/api/inventory/sync":
                    snap = engine.update_inventory(
                        facility_id=str(body["facility_id"]),
                        component=str(body["component"]),
                        blood_type=str(body["blood_type"]),
                        units=int(body["units"]),
                    )
                    broadcaster.publish("network_update", {"reason": "inventory_sync"})
                    self._send_json(HTTPStatus.OK, snap)
                    return

                if path == "/api/fhir/ingest":
                    signals = engine.ingest_fhir(body)
                    broadcaster.publish("network_update", {"reason": "fhir_ingest"})
                    self._send_json(HTTPStatus.OK, {"signals": signals, "overview": engine.get_snapshot()})
                    return

                if path == "/api/isbt128/parse":
                    parsed_code = engine.parse_isbt(str(body.get("barcode", "")))
                    self._send_json(HTTPStatus.OK, parsed_code)
                    return

                if path == "/api/simulate":
                    snap = engine.recompute(what_if_surge=body)
                    broadcaster.publish("network_update", {"reason": "what_if_simulation"})
                    self._send_json(HTTPStatus.OK, snap)
                    return

                if path.startswith("/api/transfers/") and path.endswith("/transition"):
                    parts = path.strip("/").split("/")
                    if len(parts) == 4:
                        tid = parts[2]
                        updated = engine.transition_transfer(
                            transfer_id=tid,
                            next_status=str(body.get("status", "")),
                            actor=str(body.get("actor", "clinical-coordinator")),
                        )
                        broadcaster.publish("network_update", {"reason": "transfer_transition", "transfer_id": tid})
                        self._send_json(HTTPStatus.OK, {"transfer": updated, "overview": engine.get_snapshot()})
                        return

                if path == "/api/donor/dialog":
                    reply = engine.donor_dialog_reply(
                        message=str(body.get("message", "")),
                        context=body.get("context"),
                    )
                    self._send_json(HTTPStatus.OK, reply)
                    return

                if path == "/api/donor/pledges":
                    pledge = engine.submit_pledge(body)
                    broadcaster.publish("donor_tick", engine.get_snapshot()["donor_feed"])
                    self._send_json(HTTPStatus.CREATED, {"pledge": pledge, "donor_feed": engine.get_snapshot()["donor_feed"]})
                    return

                if path == "/api/caretaker/connect":
                    result = engine.connect_caretaker(body)
                    broadcaster.publish("network_update", {"reason": "caretaker_connected"})
                    self._send_json(HTTPStatus.CREATED, {**result, "overview": engine.get_snapshot()})
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown endpoint {path}"})
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    return RequestHandler


def create_server(host: str = "127.0.0.1", port: int = 8765, db_path: str | None = None) -> ThreadingHTTPServer:
    resolved_db = db_path or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "hemonet.db")
    engine = HemoNetEngine(db_path=resolved_db)
    broadcaster = SSEBroadcaster()
    handler_cls = build_request_handler(engine, broadcaster)

    server = ThreadingHTTPServer((host, port), handler_cls)
    server.hemonet_engine = engine  # type: ignore[attr-defined]
    server.hemonet_broadcaster = broadcaster  # type: ignore[attr-defined]

    # Background thread continuously rotating donor motivation schemes every 8 seconds
    stop_event = threading.Event()
    server.hemonet_stop = stop_event  # type: ignore[attr-defined]

    def _donor_campaign_loop() -> None:
        while not stop_event.wait(8.0):
            try:
                feed = engine.advance_donor_tick()
                broadcaster.publish("donor_tick", feed)
            except Exception:
                pass

    t = threading.Thread(target=_donor_campaign_loop, daemon=True, name="hemonet-donor-loop")
    t.start()
    return server


def main() -> None:
    host = os.environ.get("HEMONET_HOST", "127.0.0.1")
    port = int(os.environ.get("HEMONET_PORT", "8765"))
    srv = create_server(host=host, port=port)
    print(f"HemoNet Command Center running at http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.hemonet_stop.set()  # type: ignore[attr-defined]
        srv.server_close()


if __name__ == "__main__":
    main()
