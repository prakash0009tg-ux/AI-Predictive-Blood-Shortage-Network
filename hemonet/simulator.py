"""Synthetic global multi-hospital and blood-bank network & time-series generator."""

from __future__ import annotations

from datetime import date, timedelta
import math
import random
from typing import Any, Dict, List, Tuple

from .domain import BLOOD_TYPES, COMPONENTS, Facility, generate_isbt128_din

# Global blood type prevalence approximations by macro-region
REGION_BLOOD_WEIGHTS: Dict[str, Dict[str, float]] = {
    "North America": {"O+": 0.37, "O-": 0.07, "A+": 0.36, "A-": 0.06, "B+": 0.09, "B-": 0.01, "AB+": 0.03, "AB-": 0.01},
    "Europe": {"O+": 0.35, "O-": 0.07, "A+": 0.37, "A-": 0.07, "B+": 0.08, "B-": 0.02, "AB+": 0.03, "AB-": 0.01},
    "South Asia": {"O+": 0.32, "O-": 0.03, "A+": 0.22, "A-": 0.02, "B+": 0.31, "B-": 0.02, "AB+": 0.07, "AB-": 0.01},
    "East Asia": {"O+": 0.34, "O-": 0.01, "A+": 0.28, "A-": 0.01, "B+": 0.27, "B-": 0.01, "AB+": 0.07, "AB-": 0.01},
    "Sub-Saharan Africa": {"O+": 0.46, "O-": 0.04, "A+": 0.23, "A-": 0.02, "B+": 0.20, "B-": 0.01, "AB+": 0.03, "AB-": 0.01},
    "Latin America": {"O+": 0.50, "O-": 0.05, "A+": 0.29, "A-": 0.03, "B+": 0.09, "B-": 0.01, "AB+": 0.02, "AB-": 0.01},
    "MENA": {"O+": 0.36, "O-": 0.05, "A+": 0.31, "A-": 0.04, "B+": 0.17, "B-": 0.02, "AB+": 0.04, "AB-": 0.01},
    "Oceania": {"O+": 0.40, "O-": 0.09, "A+": 0.31, "A-": 0.07, "B+": 0.08, "B-": 0.02, "AB+": 0.02, "AB-": 0.01},
}

DEFAULT_GLOBAL_FACILITIES: List[Dict[str, Any]] = [
    # North America
    {"id": "hosp-nyc-pres", "name": "NewYork-Presbyterian Trauma Hub", "kind": "hospital", "city": "New York", "country": "USA", "region": "North America", "lat": 40.8406, "lon": -73.9422, "capacity_units": 950, "trauma_level": 1, "isbt_code": "USNYC"},
    {"id": "bank-nybc-metro", "name": "New York Blood Center Regional Reserve", "kind": "blood_bank", "city": "New York", "country": "USA", "region": "North America", "lat": 40.7681, "lon": -73.9565, "capacity_units": 3200, "trauma_level": 1, "isbt_code": "USNYB"},
    {"id": "hosp-bos-mgh", "name": "Massachusetts General Hospital", "kind": "hospital", "city": "Boston", "country": "USA", "region": "North America", "lat": 42.3631, "lon": -71.0686, "capacity_units": 820, "trauma_level": 1, "isbt_code": "USBOS"},
    {"id": "hosp-tor-smh", "name": "St. Michael's Trauma & Heart Centre", "kind": "hospital", "city": "Toronto", "country": "Canada", "region": "North America", "lat": 43.6537, "lon": -79.3777, "capacity_units": 680, "trauma_level": 1, "isbt_code": "CATOR"},
    # Europe
    {"id": "hosp-lon-rlh", "name": "Royal London Hospital", "kind": "hospital", "city": "London", "country": "UK", "region": "Europe", "lat": 51.5186, "lon": -0.0596, "capacity_units": 880, "trauma_level": 1, "isbt_code": "UKRLH"},
    {"id": "bank-nhsbt-col", "name": "NHSBT Colindale Blood Hub", "kind": "regional_hub", "city": "London", "country": "UK", "region": "Europe", "lat": 51.5944, "lon": -0.2512, "capacity_units": 3500, "trauma_level": 1, "isbt_code": "UKNHS"},
    {"id": "hosp-ber-char", "name": "Charite Universitatsmedizin Berlin", "kind": "hospital", "city": "Berlin", "country": "Germany", "region": "Europe", "lat": 52.5260, "lon": 13.3777, "capacity_units": 900, "trauma_level": 1, "isbt_code": "DEBER"},
    {"id": "hosp-par-pit", "name": "Hopital Pitie-Salpetriere", "kind": "hospital", "city": "Paris", "country": "France", "region": "Europe", "lat": 48.8387, "lon": 2.3627, "capacity_units": 790, "trauma_level": 1, "isbt_code": "FRPAR"},
    # South Asia
    {"id": "hosp-del-aiims", "name": "AIIMS New Delhi Emergency & Trauma", "kind": "hospital", "city": "New Delhi", "country": "India", "region": "South Asia", "lat": 28.5672, "lon": 77.2100, "capacity_units": 1100, "trauma_level": 1, "isbt_code": "INDEL"},
    {"id": "bank-ind-ircs", "name": "Indian Red Cross National Blood Hub", "kind": "regional_hub", "city": "New Delhi", "country": "India", "region": "South Asia", "lat": 28.6219, "lon": 77.2090, "capacity_units": 2800, "trauma_level": 1, "isbt_code": "INIRC"},
    {"id": "hosp-mum-kem", "name": "KEM Hospital & Blood Centre", "kind": "hospital", "city": "Mumbai", "country": "India", "region": "South Asia", "lat": 19.0022, "lon": 72.8416, "capacity_units": 920, "trauma_level": 1, "isbt_code": "INMUM"},
    {"id": "hosp-blr-nim", "name": "St. John's & Victoria Regional Medical", "kind": "hospital", "city": "Bengaluru", "country": "India", "region": "South Asia", "lat": 12.9293, "lon": 77.6206, "capacity_units": 740, "trauma_level": 2, "isbt_code": "INBLR"},
    # East Asia
    {"id": "hosp-tok-tod", "name": "University of Tokyo Hospital", "kind": "hospital", "city": "Tokyo", "country": "Japan", "region": "East Asia", "lat": 35.7128, "lon": 139.7619, "capacity_units": 860, "trauma_level": 1, "isbt_code": "JPTOK"},
    {"id": "bank-jrc-kanto", "name": "Japanese Red Cross Kanto-Koshinetsu Block", "kind": "regional_hub", "city": "Tokyo", "country": "Japan", "region": "East Asia", "lat": 35.6619, "lon": 139.7495, "capacity_units": 3600, "trauma_level": 1, "isbt_code": "JPJRC"},
    {"id": "hosp-sgp-sgh", "name": "Singapore General Hospital", "kind": "hospital", "city": "Singapore", "country": "Singapore", "region": "East Asia", "lat": 1.2794, "lon": 103.8355, "capacity_units": 760, "trauma_level": 1, "isbt_code": "SGSGH"},
    # Sub-Saharan Africa
    {"id": "hosp-nbo-knh", "name": "Kenyatta National Hospital", "kind": "hospital", "city": "Nairobi", "country": "Kenya", "region": "Sub-Saharan Africa", "lat": -1.3013, "lon": 36.8070, "capacity_units": 780, "trauma_level": 1, "isbt_code": "KENBO"},
    {"id": "bank-kntbs-nbo", "name": "Kenya National Blood Transfusion Hub", "kind": "regional_hub", "city": "Nairobi", "country": "Kenya", "region": "Sub-Saharan Africa", "lat": -1.2980, "lon": 36.8030, "capacity_units": 1900, "trauma_level": 1, "isbt_code": "KEBTC"},
    {"id": "hosp-los-luth", "name": "Lagos University Teaching Hospital", "kind": "hospital", "city": "Lagos", "country": "Nigeria", "region": "Sub-Saharan Africa", "lat": 6.5180, "lon": 3.3536, "capacity_units": 700, "trauma_level": 1, "isbt_code": "NGLOS"},
    # Latin America
    {"id": "hosp-sao-hc", "name": "Hospital das Clinicas FMUSP", "kind": "hospital", "city": "Sao Paulo", "country": "Brazil", "region": "Latin America", "lat": -23.5579, "lon": -46.6695, "capacity_units": 940, "trauma_level": 1, "isbt_code": "BRSAO"},
    {"id": "bank-pro-sangue", "name": "Fundacao Pro-Sangue Hemocentro", "kind": "blood_bank", "city": "Sao Paulo", "country": "Brazil", "region": "Latin America", "lat": -23.5562, "lon": -46.6680, "capacity_units": 2600, "trauma_level": 1, "isbt_code": "BRPRO"},
    # MENA & Oceania
    {"id": "hosp-dxb-rash", "name": "Rashid Hospital Trauma Centre", "kind": "hospital", "city": "Dubai", "country": "UAE", "region": "MENA", "lat": 25.2435, "lon": 55.3175, "capacity_units": 650, "trauma_level": 1, "isbt_code": "AEDXB"},
    {"id": "bank-aus-lifeblood", "name": "Australian Red Cross Lifeblood Sydney", "kind": "regional_hub", "city": "Sydney", "country": "Australia", "region": "Oceania", "lat": -33.8688, "lon": 151.2093, "capacity_units": 2900, "trauma_level": 1, "isbt_code": "AUSYD"},
    {"id": "hosp-syd-rpa", "name": "Royal Prince Alfred Hospital", "kind": "hospital", "city": "Sydney", "country": "Australia", "region": "Oceania", "lat": -33.8895, "lon": 151.1835, "capacity_units": 720, "trauma_level": 1, "isbt_code": "AURPA"},
]


def build_default_facilities() -> List[Facility]:
    return [Facility(**item) for item in DEFAULT_GLOBAL_FACILITIES]


def generate_synthetic_network_state(
    facilities: List[Facility],
    history_days: int = 45,
    seed: int = 42,
    reference_date: date | None = None,
) -> Dict[str, Any]:
    """
    Generate realistic inventory lots (with age/expiration buckets), 45-day burn-rate &
    collection histories, surgical schedule indexes, and external shock signals.
    Intentionally creates realistic upcoming deficits (e.g. O- trauma surge, dengue platelet drop,
    holiday donor slump) so the predictive network demonstrates early-warning alerts & transfers.
    """
    rng = random.Random(seed)
    today = reference_date or date.today()

    # Specific scenario overlays to make the global network rich and realistic
    scenario_shocks: Dict[str, Dict[str, Any]] = {
        "hosp-del-aiims": {
            "outbreak_index": 0.85,  # Dengue season platelet demand spike
            "surgery_surge": 1.32,
            "stock_bias": {"PLT": 0.38, "O-": 0.42, "B+": 0.65},
            "reason": "Seasonal dengue platelet surge + high Level-1 trauma inflow",
        },
        "hosp-nyc-pres": {
            "outbreak_index": 0.15,
            "surgery_surge": 1.38,
            "stock_bias": {"O-": 0.40, "A-": 0.55},
            "reason": "Scheduled cardiothoracic/orthopedic surgeries (+38%) & O- trauma depletion",
        },
        "hosp-nbo-knh": {
            "outbreak_index": 0.72,
            "surgery_surge": 1.20,
            "stock_bias": {"O+": 0.45, "O-": 0.30, "RBC": 0.52},
            "reason": "Regional highway trauma spike + holiday donor turnout dip",
        },
        "hosp-mum-kem": {
            "outbreak_index": 0.65,
            "surgery_surge": 1.18,
            "stock_bias": {"PLT": 0.48, "O-": 0.46},
            "reason": "Monsoon vector-borne fever admissions raising platelet burn rate",
        },
        "hosp-lon-rlh": {
            "outbreak_index": 0.10,
            "surgery_surge": 1.24,
            "stock_bias": {"O-": 0.50, "AB+": 0.60},
            "reason": "Winter respiratory/surgical backlog increasing O- & FFP draw",
        },
        "bank-nhsbt-col": {"stock_bias": {"ALL": 1.65}, "surplus_hub": True, "reason": "High donor drive yield"},
        "bank-nybc-metro": {"stock_bias": {"ALL": 1.55}, "surplus_hub": True, "reason": "Metro corporate blood drives"},
        "bank-ind-ircs": {"stock_bias": {"ALL": 1.45, "PLT": 1.25}, "surplus_hub": True, "reason": "University blood camp surplus"},
        "bank-jrc-kanto": {"stock_bias": {"ALL": 1.70}, "surplus_hub": True, "reason": "Scheduled regional donor pool"},
        "bank-aus-lifeblood": {"stock_bias": {"ALL": 1.60}, "surplus_hub": True, "reason": "National Lifeblood reserve"},
        "bank-pro-sangue": {"stock_bias": {"ALL": 1.48}, "surplus_hub": True, "reason": "Hemocentro mobile drive surplus"},
    }

    inventory_lots: List[Dict[str, Any]] = []
    daily_history: List[Dict[str, Any]] = []
    facility_signals: Dict[str, Dict[str, Any]] = {}

    lot_seq = 1000
    for fac in facilities:
        weights = REGION_BLOOD_WEIGHTS.get(fac.region, REGION_BLOOD_WEIGHTS["North America"])
        shock = scenario_shocks.get(fac.id, {})
        is_bank = fac.kind in ("blood_bank", "regional_hub")

        base_daily_demand = 0.0 if is_bank else max(8.0, fac.capacity_units * (0.042 if fac.trauma_level == 1 else 0.03))
        base_daily_collection = fac.capacity_units * (0.075 if is_bank else 0.012)

        outbreak = shock.get("outbreak_index", rng.uniform(0.05, 0.25))
        surg_mult = shock.get("surgery_surge", rng.uniform(0.92, 1.12))
        holiday_dip = 0.76 if fac.region in ("North America", "Europe", "Sub-Saharan Africa") else 0.88

        facility_signals[fac.id] = {
            "outbreak_index": round(outbreak, 2),
            "surgery_surge_multiplier": round(surg_mult, 2),
            "trauma_ems_index": round(rng.uniform(0.9, 1.35 if fac.trauma_level == 1 else 1.05), 2),
            "donor_turnout_factor": round(holiday_dip if not is_bank else rng.uniform(0.95, 1.18), 2),
            "driver_note": shock.get("reason", "Baseline seasonal surgical & trauma flow"),
        }

        for comp, comp_meta in COMPONENTS.items():
            comp_demand_share = {"RBC": 0.56, "PLT": 0.16, "FFP": 0.20, "CRYO": 0.08}[comp]
            shelf = comp_meta["shelf_life_days"]

            for bt in BLOOD_TYPES:
                bt_weight = weights.get(bt, 0.05)
                bias = 1.0
                sb = shock.get("stock_bias", {})
                if "ALL" in sb:
                    bias *= sb["ALL"]
                if comp in sb:
                    bias *= sb[comp]
                if bt in sb:
                    bias *= sb[bt]

                # Daily demand and collection rates for this (facility, component, blood_type)
                mean_demand = base_daily_demand * comp_demand_share * bt_weight * (1.35 if (comp == "PLT" and outbreak > 0.6) else 1.0)
                if not is_bank:
                    mean_demand = max(0.35, mean_demand)
                mean_collection = base_daily_collection * comp_demand_share * bt_weight * (1.15 if shock.get("surplus_hub") else 1.0)

                # Generate history_days of time-series
                for offset in range(history_days, 0, -1):
                    d = today - timedelta(days=offset)
                    dow = d.weekday()  # 0=Mon..6=Sun
                    weekday_surg = 1.16 if dow < 5 else 0.72
                    seasonal = 1.0 + 0.12 * math.sin(2 * math.pi * (d.timetuple().tm_yday / 30.0))
                    # Ramp recent surge in last 5 days
                    recent_ramp = surg_mult if offset <= 5 else 1.0

                    dem = max(0.0, mean_demand * weekday_surg * seasonal * recent_ramp * rng.uniform(0.82, 1.18))
                    col = max(0.0, mean_collection * (1.15 if dow in (4, 5) else 0.94) * (holiday_dip if offset <= 4 else 1.0) * rng.uniform(0.85, 1.16))

                    daily_history.append({
                        "facility_id": fac.id,
                        "date": d.isoformat(),
                        "component": comp,
                        "blood_type": bt,
                        "demand_units": round(dem, 2),
                        "collected_units": round(col, 2),
                        "surgery_index": round(weekday_surg * recent_ramp, 2),
                        "outbreak_index": round(outbreak if offset <= 7 else outbreak * 0.6, 2),
                        "trauma_index": facility_signals[fac.id]["trauma_ems_index"],
                    })

                # Current live inventory broken into 2-3 expiration lots
                if is_bank:
                    target_units = max(2, int(round(mean_collection * comp_meta["safety_days_cover"] * 2.2 * bias)))
                else:
                    target_units = max(1, int(round(mean_demand * comp_meta["safety_days_cover"] * 1.15 * bias)))

                # Split into fresh, mid-life, and near-expiry lots
                if target_units <= 2:
                    buckets = [(target_units, max(2, int(shelf * 0.55)))]
                else:
                    near = max(0, int(round(target_units * (0.22 if comp == "PLT" else 0.14))))
                    mid = int(round(target_units * 0.40))
                    fresh = max(0, target_units - near - mid)
                    buckets = [
                        (fresh, max(2, int(shelf * 0.82))),
                        (mid, max(2, int(shelf * 0.45))),
                        (near, max(1, min(3 if comp == "PLT" else 6, shelf - 1))),
                    ]

                for qty, rem_days in buckets:
                    if qty <= 0:
                        continue
                    lot_seq += 1
                    exp_date = today + timedelta(days=rem_days)
                    inventory_lots.append({
                        "lot_id": f"LOT-{lot_seq}",
                        "isbt_din": generate_isbt128_din(fac.isbt_code, today.year, lot_seq),
                        "facility_id": fac.id,
                        "component": comp,
                        "blood_type": bt,
                        "units": qty,
                        "expires_on": exp_date.isoformat(),
                        "remaining_shelf_days": rem_days,
                    })

    return {
        "generated_at": today.isoformat(),
        "inventory_lots": inventory_lots,
        "daily_history": daily_history,
        "facility_signals": facility_signals,
    }
