"""Min-Cost Max-Flow blood redistribution optimizer & early-warning alert generator."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import heapq
import math
from typing import Any, Dict, List, Tuple

from .domain import (
    COMPONENTS,
    Facility,
    compatible_recipients,
    estimate_transport_mode,
    haversine_km,
    substitution_penalty,
)


class _Edge:
    __slots__ = ("to", "rev", "cap", "cost", "meta")

    def __init__(self, to: int, rev: int, cap: int, cost: float, meta: Dict[str, Any] | None = None) -> None:
        self.to = to
        self.rev = rev
        self.cap = cap
        self.cost = cost
        self.meta = meta or {}


class MinCostFlowGraph:
    """Successive shortest augmenting path Min-Cost Max-Flow solver with potentials."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.adj: List[List[_Edge]] = [[] for _ in range(n)]

    def add_edge(self, fr: int, to: int, cap: int, cost: float, meta: Dict[str, Any] | None = None) -> _Edge:
        fwd = _Edge(to, len(self.adj[to]), cap, cost, meta)
        rev = _Edge(fr, len(self.adj[fr]), 0, -cost)
        self.adj[fr].append(fwd)
        self.adj[to].append(rev)
        return fwd

    def min_cost_max_flow(self, source: int, sink: int) -> Tuple[int, float]:
        n = self.n
        pot = [0.0] * n
        total_flow = 0
        total_cost = 0.0
        inf = 1e18

        # Shift initial negative costs if any via Bellman-Ford passes (small graphs)
        for _ in range(n - 1):
            changed = False
            for u in range(n):
                if pot[u] >= inf:
                    continue
                for e in self.adj[u]:
                    if e.cap > 0 and pot[u] + e.cost < pot[e.to] - 1e-9:
                        pot[e.to] = pot[u] + e.cost
                        changed = True
            if not changed:
                break

        while True:
            dist = [inf] * n
            parent_v = [-1] * n
            parent_e = [-1] * n
            dist[source] = 0.0
            pq: List[Tuple[float, int]] = [(0.0, source)]

            while pq:
                d, u = heapq.heappop(pq)
                if d > dist[u] + 1e-9:
                    continue
                for idx, e in enumerate(self.adj[u]):
                    if e.cap <= 0:
                        continue
                    nd = d + e.cost + pot[u] - pot[e.to]
                    if nd < dist[e.to] - 1e-9:
                        dist[e.to] = nd
                        parent_v[e.to] = u
                        parent_e[e.to] = idx
                        heapq.heappush(pq, (nd, e.to))

            if dist[sink] >= inf / 2:
                break

            for v in range(n):
                if dist[v] < inf / 2:
                    pot[v] += dist[v]

            # Augment along path
            push = 10**9
            v = sink
            while v != source:
                u = parent_v[v]
                e = self.adj[u][parent_e[v]]
                if e.cap < push:
                    push = e.cap
                v = u

            v = sink
            while v != source:
                u = parent_v[v]
                e = self.adj[u][parent_e[v]]
                e.cap -= push
                self.adj[v][e.rev].cap += push
                v = u

            total_flow += push
            total_cost += push * pot[sink]

        return total_flow, total_cost


def optimize_redistribution(
    facilities: List[Facility],
    forecasts: List[Dict[str, Any]],
    inventory_lots: List[Dict[str, Any]],
    clinical_signoff_unit_threshold: int = 12,
) -> Dict[str, Any]:
    """
    Pair surplus blood banks & hospitals with at-risk hospitals per component using
    Min-Cost Max-Flow honoring ABO/Rh compatibility, shelf life, and cold-chain SLAs.
    """
    fac_map = {f.id: f for f in facilities if f.connected}

    # Maximum usable remaining shelf life per (facility, component, blood_type)
    max_shelf_by_key: Dict[Tuple[str, str, str], int] = defaultdict(int)
    for lot in inventory_lots:
        k = (lot["facility_id"], lot["component"], lot["blood_type"])
        max_shelf_by_key[k] = max(max_shelf_by_key[k], int(lot.get("remaining_shelf_days", 10)))

    proposals: List[Dict[str, Any]] = []
    uncovered_deficits: List[Dict[str, Any]] = []
    proposal_seq = 1

    for comp, comp_meta in COMPONENTS.items():
        comp_forecasts = [f for f in forecasts if f["component"] == comp and f["facility_id"] in fac_map]
        supply_nodes = [f for f in comp_forecasts if f["available_surplus_units"] > 0]
        demand_nodes = [
            f for f in comp_forecasts
            if f["recommended_replenish_units"] > 0 and f["risk_level"] in ("CRITICAL", "WARNING", "WATCH")
        ]

        if not demand_nodes:
            continue

        # Sort highest risk first for deterministic allocation tracking
        risk_rank = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "SAFE": 3}
        demand_nodes.sort(key=lambda d: (risk_rank.get(d["risk_level"], 3), d["days_of_cover"]))

        if not supply_nodes:
            for d in demand_nodes:
                uncovered_deficits.append({
                    "facility_id": d["facility_id"],
                    "facility_name": d["facility_name"],
                    "city": d["city"],
                    "country": d["country"],
                    "region": d["region"],
                    "component": comp,
                    "blood_type": d["blood_type"],
                    "uncovered_units": d["recommended_replenish_units"],
                    "risk_level": d["risk_level"],
                })
            continue

        n_sup = len(supply_nodes)
        n_dem = len(demand_nodes)
        src = 0
        snk = 1 + n_sup + n_dem
        graph = MinCostFlowGraph(snk + 1)

        for i, s in enumerate(supply_nodes):
            graph.add_edge(src, 1 + i, int(s["available_surplus_units"]), 0.0)

        # Urgency discount on sink edges so critical hospitals are always served first
        for j, d in enumerate(demand_nodes):
            urgency_cost = {"CRITICAL": -120.0, "WARNING": -60.0, "WATCH": -20.0}.get(d["risk_level"], 0.0)
            graph.add_edge(1 + n_sup + j, snk, int(d["recommended_replenish_units"]), urgency_cost)

        candidate_edges: List[Tuple[int, int, _Edge, int]] = []
        for i, s in enumerate(supply_nodes):
            sf = fac_map[s["facility_id"]]
            allowed_targets = compatible_recipients(s["blood_type"], comp)
            shelf_days = max_shelf_by_key.get((s["facility_id"], comp, s["blood_type"]), comp_meta["shelf_life_days"])

            for j, d in enumerate(demand_nodes):
                if s["facility_id"] == d["facility_id"]:
                    continue
                if d["blood_type"] not in allowed_targets:
                    continue

                df = fac_map[d["facility_id"]]
                dist_km = haversine_km(sf.lat, sf.lon, df.lat, df.lon)
                same_country = sf.country == df.country
                mode, transit_hrs, ship_cost = estimate_transport_mode(dist_km, same_country)

                # Enforce cold-chain transit limit and ensure units don't expire in transit
                if transit_hrs > comp_meta["max_transit_hours"]:
                    continue
                if shelf_days * 24 <= transit_hrs + 24:
                    continue

                sub_pen = substitution_penalty(s["blood_type"], d["blood_type"], comp)
                border_pen = 0.0 if same_country else 18.0
                waste_rescue_bonus = -8.0 if s["expiring_7d_units"] > 0 else 0.0
                edge_cost = max(0.5, transit_hrs * 1.8 + ship_cost * 0.04 + sub_pen + border_pen + waste_rescue_bonus)

                cap = min(int(s["available_surplus_units"]), int(d["recommended_replenish_units"]))
                edge = graph.add_edge(
                    1 + i,
                    1 + n_sup + j,
                    cap,
                    edge_cost,
                    meta={
                        "distance_km": round(dist_km, 1),
                        "mode": mode,
                        "transit_hours": round(transit_hrs, 1),
                        "estimated_cost_usd": round(ship_cost, 2),
                        "same_country": same_country,
                        "substitution_used": s["blood_type"] != d["blood_type"],
                        "rescues_expiring": s["expiring_7d_units"] > 0,
                    },
                )
                candidate_edges.append((i, j, edge, cap))

        graph.min_cost_max_flow(src, snk)

        received_by_dem: Dict[int, int] = defaultdict(int)
        for i, j, edge, orig_cap in candidate_edges:
            shipped = orig_cap - edge.cap
            if shipped <= 0:
                continue
            received_by_dem[j] += shipped
            s = supply_nodes[i]
            d = demand_nodes[j]
            meta = edge.meta
            tier = "LOCAL" if meta["distance_km"] <= 80 else ("REGIONAL" if meta["same_country"] else "CROSS_BORDER")
            needs_signoff = (
                meta["substitution_used"]
                or not meta["same_country"]
                or shipped >= clinical_signoff_unit_threshold
            )
            proposals.append({
                "transfer_id": f"TXF-{proposal_seq:04d}",
                "status": "PROPOSED",
                "component": comp,
                "source_facility_id": s["facility_id"],
                "source_facility_name": s["facility_name"],
                "source_city": s["city"],
                "source_country": s["country"],
                "source_blood_type": s["blood_type"],
                "target_facility_id": d["facility_id"],
                "target_facility_name": d["facility_name"],
                "target_city": d["city"],
                "target_country": d["country"],
                "target_blood_type": d["blood_type"],
                "units": shipped,
                "target_risk_level": d["risk_level"],
                "escalation_tier": tier,
                "transport_mode": meta["mode"],
                "distance_km": meta["distance_km"],
                "transit_hours": meta["transit_hours"],
                "estimated_cost_usd": meta["estimated_cost_usd"],
                "substitution_used": meta["substitution_used"],
                "rescues_expiring_units": meta["rescues_expiring"],
                "requires_clinical_signoff": needs_signoff,
                "signoff_reason": (
                    "Cross-type ABO/Rh substitution"
                    if meta["substitution_used"]
                    else ("International cold-chain customs" if not meta["same_country"] else "Standard clinical release")
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            proposal_seq += 1

        for j, d in enumerate(demand_nodes):
            rem = int(d["recommended_replenish_units"]) - received_by_dem[j]
            if rem > 0:
                uncovered_deficits.append({
                    "facility_id": d["facility_id"],
                    "facility_name": d["facility_name"],
                    "city": d["city"],
                    "country": d["country"],
                    "region": d["region"],
                    "component": comp,
                    "blood_type": d["blood_type"],
                    "uncovered_units": rem,
                    "risk_level": d["risk_level"],
                })

    return {
        "proposals": proposals,
        "uncovered_deficits": uncovered_deficits,
        "total_units_matched": sum(p["units"] for p in proposals),
        "total_uncovered_units": sum(u["uncovered_units"] for u in uncovered_deficits),
    }


def generate_shortage_alerts(
    forecasts: List[Dict[str, Any]],
    proposals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build actionable, human-readable early-warning shortage alerts."""
    proposals_by_target: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for p in proposals:
        proposals_by_target[(p["target_facility_id"], p["component"], p["target_blood_type"])].append(p)

    alerts: List[Dict[str, Any]] = []
    severity_rank = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2}

    for f in forecasts:
        if f["facility_kind"] != "hospital" or f["risk_level"] == "SAFE":
            continue
        key = (f["facility_id"], f["component"], f["blood_type"])
        matched = proposals_by_target.get(key, [])
        covered_units = sum(m["units"] for m in matched)
        lead_hours = max(12, int(round(f["days_of_cover"] * 24)))

        prep_steps = [
            f"Verify physical crossmatch fridge count for {f['blood_type']} {f['component']} ({f['current_units']} units on hand).",
        ]
        if matched:
            prep_steps.append(
                f"Approve {len(matched)} pre-matched transfer(s) totaling {covered_units} units (ETA {min(m['transit_hours'] for m in matched)}h)."
            )
        else:
            prep_steps.append(
                f"Activate local donor mobilization drive for {f['blood_type']} {f['component']} in {f['city']}."
            )
        if f["risk_level"] == "CRITICAL":
            prep_steps.append("Enable blood-conservation surgical review and hold elective non-urgent transfusions.")

        top_driver = f["top_drivers"][0]["feature"] if f["top_drivers"] else "Burn-rate acceleration"
        alerts.append({
            "alert_id": f"ALT-{f['facility_id']}-{f['component']}-{f['blood_type']}",
            "severity": f["risk_level"],
            "facility_id": f["facility_id"],
            "facility_name": f["facility_name"],
            "city": f["city"],
            "country": f["country"],
            "region": f["region"],
            "component": f["component"],
            "component_name": COMPONENTS[f["component"]]["name"],
            "blood_type": f["blood_type"],
            "current_units": f["current_units"],
            "days_of_cover": f["days_of_cover"],
            "shortage_probability_7d": f["shortage_probability_7d"],
            "recommended_units": f["recommended_replenish_units"],
            "matched_transfer_units": covered_units,
            "lead_time_hours": lead_hours,
            "primary_driver": top_driver,
            "driver_note": f["driver_note"],
            "preparedness_actions": prep_steps,
            "linked_transfers": [m["transfer_id"] for m in matched],
        })

    alerts.sort(key=lambda a: (severity_rank.get(a["severity"], 3), a["days_of_cover"], -a["shortage_probability_7d"]))
    return alerts
