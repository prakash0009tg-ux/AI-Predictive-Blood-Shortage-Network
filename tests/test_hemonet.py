"""Automated unit & integration tests for HemoNet."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from hemonet.campaigns import evaluate_donor_dialog_step
from hemonet.domain import (
    compatible_recipients,
    differentially_private_aggregate,
    fhir_bundle_to_inventory_signals,
    generate_isbt128_din,
    parse_isbt128_payload,
)
from hemonet.engine import HemoNetEngine
from hemonet.forecaster import forecast_network
from hemonet.optimizer import optimize_redistribution
from hemonet.server import SSEBroadcaster, build_request_handler
from hemonet.simulator import build_default_facilities, generate_synthetic_network_state


class TestDomainAndInterop(unittest.TestCase):
    def test_compatibility_rules(self) -> None:
        self.assertIn("AB+", compatible_recipients("O-", "RBC"))
        self.assertEqual(compatible_recipients("AB+", "RBC"), ("AB+",))
        # Plasma inverse: AB+ can give FFP to everyone
        self.assertIn("O-", compatible_recipients("AB+", "FFP"))

    def test_isbt128_roundtrip(self) -> None:
        din = generate_isbt128_din("USNYC", 2026, 42)
        barcode = f"{din}=%9500=<E0306"
        parsed = parse_isbt128_payload(barcode)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["blood_type"], "O-")
        self.assertEqual(parsed["component"], "RBC")

    def test_fhir_strips_phi(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {"resourceType": "Organization", "id": "hosp-nyc-pres"}},
                {
                    "resource": {
                        "resourceType": "BiologicallyDerivedProduct",
                        "productCode": {"text": "PLT"},
                        "quantity": 5,
                        "property": [{"valueCodeableConcept": {"text": "A+"}}],
                    }
                },
                {"resource": {"resourceType": "Appointment", "patient": {"reference": "Patient/SECRET-123"}}},
            ],
        }
        sig = fhir_bundle_to_inventory_signals(bundle)
        self.assertTrue(sig["phi_stripped"])
        self.assertEqual(sig["scheduled_surgical_demand_index"], 1)
        self.assertEqual(sig["inventory"][0]["units"], 5)
        self.assertNotIn("SECRET-123", json.dumps(sig))

    def test_differential_privacy(self) -> None:
        res = differentially_private_aggregate([10.0, 20.0, 30.0], epsilon=1.5, seed=7)
        self.assertEqual(res["count"], 3)
        self.assertGreaterEqual(res["noisy_sum"], 0.0)


class TestForecasterAndOptimizer(unittest.TestCase):
    def test_forecast_and_optimization_pipeline(self) -> None:
        facs = build_default_facilities()
        sim = generate_synthetic_network_state(facs, history_days=30, seed=42)
        fc = forecast_network(facs, sim["inventory_lots"], sim["daily_history"], sim["facility_signals"])
        self.assertGreater(len(fc["forecasts"]), 50)
        self.assertGreater(fc["backtest_metrics"]["evaluated_samples"], 0)
        self.assertGreaterEqual(fc["backtest_metrics"]["critical_shortage_recall"], 0.0)

        opt = optimize_redistribution(facs, fc["forecasts"], sim["inventory_lots"])
        self.assertGreater(opt["total_units_matched"], 0)
        fac_map = {f.id: f for f in facs}
        for p in opt["proposals"]:
            # Every matched proposal must obey ABO/Rh compatibility
            self.assertIn(p["target_blood_type"], compatible_recipients(p["source_blood_type"], p["component"]))
            self.assertIn(p["source_facility_id"], fac_map)
            self.assertIn(p["target_facility_id"], fac_map)
            self.assertGreater(p["units"], 0)


class TestEngineAndHTTPServer(unittest.TestCase):
    def test_engine_connect_transition_and_pledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.db")
            eng = HemoNetEngine(db_path=db, seed=11)
            snap = eng.get_snapshot()
            self.assertGreater(len(snap["alerts"]), 0)
            self.assertGreater(len(snap["transfers"]), 0)

            # Connect a new hospital anywhere in the world
            created = eng.connect_facility({
                "name": "Manila Metro Trauma Hospital",
                "city": "Manila",
                "country": "Philippines",
                "region": "East Asia",
                "lat": 14.59,
                "lon": 120.98,
                "initial_inventory": {"RBC:O-": 1},
            })
            self.assertEqual(created["city"], "Manila")
            self.assertTrue(any(f["id"] == created["id"] for f in eng.get_snapshot()["facilities"]))

            # Transition a transfer through full lifecycle
            tid = eng.get_snapshot()["transfers"][0]["transfer_id"]
            self.assertEqual(eng.transition_transfer(tid, "APPROVED")["status"], "APPROVED")
            self.assertEqual(eng.transition_transfer(tid, "IN_TRANSIT")["status"], "IN_TRANSIT")
            self.assertEqual(eng.transition_transfer(tid, "RECEIVED")["status"], "RECEIVED")

            # Donor dialog & pledge
            reply = evaluate_donor_dialog_step("Am I eligible to donate?")
            self.assertIn("18", reply["reply"])
            pledge = eng.submit_pledge({"donor_name": "Elena", "blood_type": "O-", "component": "PLT", "city": "Manila"})
            self.assertTrue(pledge["pledge_id"].startswith("PLG-"))

    def test_http_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "http_test.db")
            engine = HemoNetEngine(db_path=db, seed=42)
            handler_cls = build_request_handler(engine, SSEBroadcaster())

            def invoke(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
                payload = json.dumps(body).encode("utf-8") if body is not None else b""
                headers = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
                if body is not None:
                    headers += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
                headers += "\r\n"
                raw_req = headers.encode("utf-8") + payload

                class _Sock:
                    def __init__(self, data: bytes) -> None:
                        self.in_buf = io.BytesIO(data)
                        self.out_buf = io.BytesIO()

                    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BytesIO:
                        return self.in_buf if "r" in mode else self.out_buf

                    def sendall(self, b: bytes) -> None:
                        self.out_buf.write(b)

                sock = _Sock(raw_req)
                handler_cls(sock, ("127.0.0.1", 0), None)
                raw_resp = sock.out_buf.getvalue().decode("utf-8", errors="replace")
                head, _, resp_body = raw_resp.partition("\r\n\r\n")
                status_code = int(head.splitlines()[0].split()[1])
                return status_code, resp_body

            status, overview_raw = invoke("GET", "/api/overview")
            self.assertEqual(status, 200)
            overview = json.loads(overview_raw)
            self.assertIn("alerts", overview)
            self.assertIn("donor_feed", overview)

            status, spec_raw = invoke("GET", "/openapi.json")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(spec_raw)["openapi"], "3.1.0")

            status, chat_raw = invoke("POST", "/api/donor/dialog", {"message": "I am scared of needles"})
            self.assertEqual(status, 200)
            self.assertIn("pinch", json.loads(chat_raw)["reply"].lower())

            status, html = invoke("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("HemoNet", html)

            # Real-time inventory & historic consumption monitor
            status, mon_raw = invoke("GET", "/api/inventory/monitor?blood_type=O-&component=RBC")
            self.assertEqual(status, 200)
            mon = json.loads(mon_raw)
            self.assertIn("blood_group_matrix", mon["analytics"])
            self.assertGreater(len(mon["analytics"]["historic_consumption_series"]), 10)
            self.assertTrue(all(s["blood_type"] == "O-" and s["component"] == "RBC" for s in mon["filtered_streams"]))

            # WHO-verified doctors, medical colleges, drug manufacturers & experts
            status, eco_raw = invoke("GET", "/api/ecosystem?who_only=true")
            self.assertEqual(status, 200)
            eco = json.loads(eco_raw)
            self.assertGreater(len(eco["doctors"]), 0)
            self.assertGreater(len(eco["medical_colleges"]), 0)
            self.assertGreater(len(eco["drug_manufacturers"]), 0)
            self.assertGreater(len(eco["who_experts"]), 0)
            for doc in eco["doctors"]:
                self.assertIn("experience_years", doc)
                self.assertIn("success_rate_pct", doc)
                self.assertIn("mental_strength_score", doc)
                self.assertTrue(doc["who_verified"])

            # Caretaker -> Specialist Blood-Group Doctor connection
            status, care_raw = invoke(
                "POST",
                "/api/caretaker/connect",
                {
                    "caretaker_name": "Sunita Verma",
                    "patient_relation": "Mother",
                    "blood_type": "O-",
                    "component": "PLT",
                    "city": "New Delhi",
                    "urgency": "CRITICAL",
                    "clinical_notes": "Dengue platelet drop",
                },
            )
            self.assertEqual(status, 201)
            care = json.loads(care_raw)
            self.assertTrue(care["connection"]["request_id"].startswith("CARE-"))
            self.assertTrue(care["assigned_doctor"]["who_verified"])
            self.assertIn("AIIMS", care["assigned_doctor"]["hospital_name"])


if __name__ == "__main__":
    unittest.main()
