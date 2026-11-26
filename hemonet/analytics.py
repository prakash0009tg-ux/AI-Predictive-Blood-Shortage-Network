"""Historic blood consumption, real-time inventory, and blood-group availability analytics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from .domain import BLOOD_TYPES, COMPONENTS, Facility


def compute_inventory_and_consumption_analytics(
    facilities: List[Facility],
    inventory_lots: List[Dict[str, Any]],
    daily_history: List[Dict[str, Any]],
    forecasts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Analyze historic daily blood consumption vs. collections, live inventory levels,
    and global blood group x component availability in real time.
    """
    fac_map = {f.id: f for f in facilities}

    # 1. Blood group x component live matrix
    matrix: Dict[str, Dict[str, Dict[str, Any]]] = {
        bt: {
            comp: {
                "current_units": 0,
                "expiring_7d_units": 0,
                "daily_demand": 0.0,
                "daily_collection": 0.0,
                "critical_hospitals": 0,
                "status": "SAFE",
            }
            for comp in COMPONENTS
        }
        for bt in BLOOD_TYPES
    }

    for lot in inventory_lots:
        bt = lot["blood_type"]
        comp = lot["component"]
        if bt in matrix and comp in matrix[bt]:
            matrix[bt][comp]["current_units"] += int(lot["units"])
            if int(lot.get("remaining_shelf_days", 99)) <= 7:
                matrix[bt][comp]["expiring_7d_units"] += int(lot["units"])

    for fc in forecasts:
        bt = fc["blood_type"]
        comp = fc["component"]
        if bt in matrix and comp in matrix[bt]:
            matrix[bt][comp]["daily_demand"] += float(fc["predicted_daily_demand"])
            matrix[bt][comp]["daily_collection"] += float(fc["predicted_daily_collection"])
            if fc["risk_level"] == "CRITICAL":
                matrix[bt][comp]["critical_hospitals"] += 1

    for bt in BLOOD_TYPES:
        for comp in COMPONENTS:
            cell = matrix[bt][comp]
            cell["daily_demand"] = round(cell["daily_demand"], 1)
            cell["daily_collection"] = round(cell["daily_collection"], 1)
            cover = cell["current_units"] / max(0.5, cell["daily_demand"])
            cell["network_days_cover"] = round(cover, 1)
            if cell["critical_hospitals"] >= 2 or cover < 2.0:
                cell["status"] = "CRITICAL"
            elif cell["critical_hospitals"] == 1 or cover < 4.5:
                cell["status"] = "WARNING"

    # 2. Historic daily consumption vs collection series (last 30 days)
    by_date: Dict[str, Dict[str, float]] = defaultdict(lambda: {"demand": 0.0, "collected": 0.0})
    by_region_consumption: Dict[str, Dict[str, float]] = defaultdict(lambda: {"demand_30d": 0.0, "collected_30d": 0.0})

    for row in daily_history:
        d = row["date"]
        dem = float(row["demand_units"])
        col = float(row["collected_units"])
        by_date[d]["demand"] += dem
        by_date[d]["collected"] += col
        fac = fac_map.get(row["facility_id"])
        if fac:
            by_region_consumption[fac.region]["demand_30d"] += dem
            by_region_consumption[fac.region]["collected_30d"] += col

    sorted_dates = sorted(by_date.keys())[-30:]
    historic_series = [
        {
            "date": d,
            "consumption_units": round(by_date[d]["demand"], 1),
            "collection_units": round(by_date[d]["collected"], 1),
            "net_balance": round(by_date[d]["collected"] - by_date[d]["demand"], 1),
        }
        for d in sorted_dates
    ]

    # 3. Top historic consumers (hospitals) & regional balance
    hospital_30d: Dict[str, float] = defaultdict(float)
    for row in daily_history:
        if row["date"] in set(sorted_dates):
            hospital_30d[row["facility_id"]] += float(row["demand_units"])

    top_consumers = []
    for fid, total_dem in sorted(hospital_30d.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        fac = fac_map.get(fid)
        if not fac or fac.kind != "hospital":
            continue
        top_consumers.append({
            "facility_id": fid,
            "facility_name": fac.name,
            "city": fac.city,
            "country": fac.country,
            "consumption_30d_units": round(total_dem, 1),
            "avg_daily_burn": round(total_dem / max(1, len(sorted_dates)), 1),
        })

    regional_balance = [
        {
            "region": reg,
            "consumption_units": round(vals["demand_30d"], 1),
            "collection_units": round(vals["collected_30d"], 1),
            "surplus_deficit": round(vals["collected_30d"] - vals["demand_30d"], 1),
        }
        for reg, vals in sorted(by_region_consumption.items())
    ]

    total_inventory = sum(int(l["units"]) for l in inventory_lots)
    avg_daily_burn = round(sum(p["consumption_units"] for p in historic_series) / max(1, len(historic_series)), 1)

    return {
        "total_live_units": total_inventory,
        "avg_global_daily_consumption": avg_daily_burn,
        "blood_group_matrix": matrix,
        "historic_consumption_series": historic_series,
        "top_consuming_hospitals": top_consumers,
        "regional_balance": regional_balance,
    }
