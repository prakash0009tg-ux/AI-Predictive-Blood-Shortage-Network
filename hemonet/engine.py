"""HemoNet runtime coordinator tying simulation, AI forecasting, MCMF optimization, and campaigns."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import threading
from typing import Any, Dict, List, Optional

from .analytics import compute_inventory_and_consumption_analytics
from .campaigns import build_continuous_donor_feed, evaluate_donor_dialog_step
from .domain import (
    BLOOD_TYPES,
    COMPONENTS,
    Facility,
    anonymize_facility_token,
    differentially_private_aggregate,
    fhir_bundle_to_inventory_signals,
    generate_isbt128_din,
    parse_isbt128_payload,
)
from .ecosystem import get_ecosystem_bundle, match_specialist_doctors
from .forecaster import forecast_network
from .optimizer import generate_shortage_alerts, optimize_redistribution
from .simulator import build_default_facilities, generate_synthetic_network_state
from .storage import HemoNetStore


class HemoNetEngine:
    """Thread-safe application service powering API, CLI, and real-time dashboard."""

    def __init__(self, db_path: str, seed: int = 42) -> None:
        self.store = HemoNetStore(db_path)
        self.store.seed_initial_pledges()
        self.seed = seed
        self._lock = threading.RLock()
        self._tick = 0
        self._what_if: Dict[str, Any] = {}
        self._listeners: List[Any] = []
        self._state: Dict[str, Any] = {}
        self.recompute()

    def _load_all_facilities(self) -> List[Facility]:
        base = build_default_facilities()
        existing_ids = {f.id for f in base}
        for raw in self.store.list_custom_facilities():
            if raw["id"] not in existing_ids:
                base.append(Facility(**raw))
        return base

    def recompute(self, what_if_surge: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if what_if_surge is not None:
                self._what_if = dict(what_if_surge)
            facilities = self._load_all_facilities()
            sim = generate_synthetic_network_state(facilities, history_days=45, seed=self.seed)

            # Apply persisted inventory overrides (e.g. from FHIR sync or manual hospital update)
            overrides = self.store.list_inventory_overrides()
            lots = sim["inventory_lots"]
            if overrides:
                filtered = [
                    lot for lot in lots
                    if (lot["facility_id"], lot["component"], lot["blood_type"]) not in overrides
                ]
                today = date.today()
                for (fid, comp, bt), units in overrides.items():
                    if units <= 0:
                        continue
                    shelf = COMPONENTS[comp]["shelf_life_days"]
                    filtered.append({
                        "lot_id": f"OVR-{fid}-{comp}-{bt}",
                        "isbt_din": generate_isbt128_din(fid[:5], today.year, 9001),
                        "facility_id": fid,
                        "component": comp,
                        "blood_type": bt,
                        "units": units,
                        "expires_on": (today + timedelta(days=max(2, int(shelf * 0.6)))).isoformat(),
                        "remaining_shelf_days": max(2, int(shelf * 0.6)),
                    })
                lots = filtered

            fc_result = forecast_network(
                facilities=facilities,
                inventory_lots=lots,
                daily_history=sim["daily_history"],
                facility_signals=sim["facility_signals"],
                what_if_surge=self._what_if,
            )

            opt_result = optimize_redistribution(
                facilities=facilities,
                forecasts=fc_result["forecasts"],
                inventory_lots=lots,
            )
            self.store.sync_proposed_transfers(opt_result["proposals"])
            all_transfers = self.store.list_transfers()

            alerts = generate_shortage_alerts(fc_result["forecasts"], all_transfers)
            pledges = self.store.list_pledges()
            donor_feed = build_continuous_donor_feed(
                alerts=alerts,
                uncovered_deficits=opt_result["uncovered_deficits"],
                pledges=pledges,
                tick=self._tick,
            )
            analytics = compute_inventory_and_consumption_analytics(
                facilities=facilities,
                inventory_lots=lots,
                daily_history=sim["daily_history"],
                forecasts=fc_result["forecasts"],
            )
            ecosystem = get_ecosystem_bundle()
            caretaker_requests = self.store.list_caretaker_requests()

            # Regional federated differential-privacy summary
            by_region: Dict[str, List[float]] = defaultdict(list)
            for fc in fc_result["forecasts"]:
                if fc["facility_kind"] == "hospital":
                    by_region[fc["region"]].append(fc["predicted_daily_demand"])
            federated_summary = {
                region: differentially_private_aggregate(vals, epsilon=1.5, seed=self.seed + idx)
                for idx, (region, vals) in enumerate(sorted(by_region.items()))
            }

            # Facility-level summary cards
            fc_by_fac: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for fc in fc_result["forecasts"]:
                fc_by_fac[fc["facility_id"]].append(fc)

            facility_summaries = []
            for fac in facilities:
                items = fc_by_fac.get(fac.id, [])
                total_units = sum(i["current_units"] for i in items)
                crit_count = sum(1 for i in items if i["risk_level"] == "CRITICAL")
                warn_count = sum(1 for i in items if i["risk_level"] == "WARNING")
                min_cover = min((i["days_of_cover"] for i in items), default=30.0)
                overall = "CRITICAL" if crit_count > 0 else ("WARNING" if warn_count > 0 else "SAFE")
                facility_summaries.append({
                    **fac.to_dict(),
                    "anonymized_token": anonymize_facility_token(fac.id),
                    "total_units": total_units,
                    "critical_streams": crit_count,
                    "warning_streams": warn_count,
                    "min_days_of_cover": min_cover,
                    "overall_status": overall,
                    "signal": sim["facility_signals"].get(fac.id, {}),
                })

            self._state = {
                "facilities": facility_summaries,
                "forecasts": fc_result["forecasts"],
                "backtest_metrics": fc_result["backtest_metrics"],
                "alerts": alerts,
                "transfers": all_transfers,
                "uncovered_deficits": opt_result["uncovered_deficits"],
                "optimization_summary": {
                    "total_units_matched": opt_result["total_units_matched"],
                    "total_uncovered_units": opt_result["total_uncovered_units"],
                    "proposal_count": len(opt_result["proposals"]),
                },
                "donor_feed": donor_feed,
                "pledges": pledges,
                "analytics": analytics,
                "ecosystem": ecosystem,
                "caretaker_requests": caretaker_requests,
                "federated_privacy": federated_summary,
                "audit_log": self.store.list_audit_log(),
                "what_if": self._what_if,
                "tick": self._tick,
            }
            return self._state

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._state

    def advance_donor_tick(self) -> Dict[str, Any]:
        with self._lock:
            self._tick += 1
            pledges = self.store.list_pledges()
            donor_feed = build_continuous_donor_feed(
                alerts=self._state.get("alerts", []),
                uncovered_deficits=self._state.get("uncovered_deficits", []),
                pledges=pledges,
                tick=self._tick,
            )
            self._state["donor_feed"] = donor_feed
            self._state["tick"] = self._tick
            self._state["pledges"] = pledges
            return donor_feed

    def connect_facility(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = str(data.get("name", "")).strip()
        city = str(data.get("city", "")).strip()
        country = str(data.get("country", "")).strip()
        if not name or not city or not country:
            raise ValueError("name, city, and country are required")

        slug = "".join(ch for ch in name.lower() if ch.isalnum())[:8] or "node"
        fid = str(data.get("id") or f"custom-{slug}-{abs(hash(name + city)) % 10000}")
        facility = Facility(
            id=fid,
            name=name,
            kind=str(data.get("kind", "hospital")),
            city=city,
            country=country,
            region=str(data.get("region", "North America")),
            lat=float(data.get("lat", 20.0)),
            lon=float(data.get("lon", 0.0)),
            capacity_units=max(50, int(data.get("capacity_units", 500))),
            trauma_level=int(data.get("trauma_level", 1)),
            cold_chain_certified=bool(data.get("cold_chain_certified", True)),
            connected=True,
            isbt_code=str(data.get("isbt_code", slug[:5].upper())).ljust(5, "0")[:5],
            contact_channel=str(data.get("contact_channel", "FHIR-R4 / Web Portal")),
        )
        self.store.add_custom_facility(facility.to_dict())

        # Optional initial critical stock override for immediate demo/testing
        if isinstance(data.get("initial_inventory"), dict):
            for key_str, qty in data["initial_inventory"].items():
                if ":" in key_str:
                    comp, bt = key_str.split(":", 1)
                    if comp in COMPONENTS and bt in BLOOD_TYPES:
                        self.store.set_inventory_override(fid, comp, bt, int(qty))

        self.recompute()
        return facility.to_dict()

    def update_inventory(self, facility_id: str, component: str, blood_type: str, units: int) -> Dict[str, Any]:
        if component not in COMPONENTS:
            raise ValueError(f"Unsupported component: {component}")
        if blood_type not in BLOOD_TYPES:
            raise ValueError(f"Unsupported blood_type: {blood_type}")
        self.store.set_inventory_override(facility_id, component, blood_type, units)
        return self.recompute()

    def ingest_fhir(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        signals = fhir_bundle_to_inventory_signals(bundle)
        fid = signals["facility_id"]
        if not fid:
            raise ValueError("FHIR Bundle must include an Organization resource with id")
        for item in signals["inventory"]:
            self.store.set_inventory_override(fid, item["component"], item["blood_type"], item["units"])
        self.recompute()
        return signals

    def parse_isbt(self, barcode: str) -> Dict[str, Any]:
        return parse_isbt128_payload(barcode)

    def transition_transfer(self, transfer_id: str, next_status: str, actor: str = "clinical-coordinator") -> Dict[str, Any]:
        updated = self.store.transition_transfer(transfer_id, next_status, actor)
        self.recompute()
        return updated

    def submit_pledge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        donor_name = str(payload.get("donor_name", "")).strip() or "Community Hero"
        blood_type = str(payload.get("blood_type", "O-")).strip().upper()
        if blood_type not in BLOOD_TYPES:
            blood_type = "O-"
        component = str(payload.get("component", "RBC")).strip().upper()
        if component not in COMPONENTS:
            component = "RBC"
        city = str(payload.get("city", "New York")).strip() or "Global Network"
        preferred_window = str(payload.get("preferred_window", "Within 48h")).strip()
        team_name = str(payload.get("team_name", "")).strip()
        scheme_id = str(payload.get("scheme_id", "scheme-rapid-match")).strip()
        pledge = self.store.add_pledge(donor_name, blood_type, component, city, preferred_window, team_name, scheme_id)
        self.advance_donor_tick()
        return pledge

    def donor_dialog_reply(self, message: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        urgent = self._state.get("donor_feed", {}).get("urgent_blood_types", [])
        return evaluate_donor_dialog_step(message, context=context, urgent_blood_types=urgent)

    def get_inventory_monitor(
        self,
        blood_type: str = "",
        component: str = "",
        region: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            bt = blood_type.upper().strip()
            comp = component.upper().strip()
            reg = region.strip()
            rows = []
            for fc in self._state.get("forecasts", []):
                if bt and bt != "ALL" and fc["blood_type"] != bt:
                    continue
                if comp and comp != "ALL" and fc["component"] != comp:
                    continue
                if reg and reg != "ALL" and fc["region"] != reg:
                    continue
                rows.append(fc)
            return {
                "analytics": self._state.get("analytics", {}),
                "filtered_streams": rows[:160],
                "total_matching_streams": len(rows),
            }

    def query_ecosystem(self, region: str = "ALL", search: str = "", who_only: bool = False) -> Dict[str, Any]:
        return get_ecosystem_bundle(region=region, search=search, who_only=who_only)

    def connect_caretaker(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        caretaker_name = str(payload.get("caretaker_name", "")).strip() or "Patient Guardian"
        blood_type = str(payload.get("blood_type", "O-")).strip().upper()
        if blood_type not in BLOOD_TYPES:
            blood_type = "O-"
        component = str(payload.get("component", "RBC")).strip().upper()
        if component not in COMPONENTS:
            component = "RBC"
        city = str(payload.get("city", "")).strip() or "New York"
        region = str(payload.get("region", "")).strip()
        urgency = str(payload.get("urgency", "URGENT")).strip().upper()

        matches = match_specialist_doctors(
            blood_type=blood_type,
            component=component,
            city=city,
            region=region,
            urgency=urgency,
        )
        preferred_id = str(payload.get("doctor_id", "")).strip()
        chosen = next((d for d in matches if d["doctor_id"] == preferred_id), matches[0])

        saved = self.store.add_caretaker_request({
            "caretaker_name": caretaker_name,
            "patient_relation": str(payload.get("patient_relation", "Family Guardian")).strip(),
            "blood_type": blood_type,
            "component": component,
            "city": city,
            "urgency": urgency,
            "clinical_notes": str(payload.get("clinical_notes", "")).strip(),
            "assigned_doctor_id": chosen["doctor_id"],
            "assigned_doctor_name": chosen["name"],
            "hospital_name": chosen["hospital_name"],
            "who_verification_id": chosen["who_verification_id"],
        })
        self.recompute()
        return {
            "connection": saved,
            "assigned_doctor": chosen,
            "alternative_specialists": matches[:4],
            "caretaker_requests": self.store.list_caretaker_requests(),
        }
