"""Clinical domain models, ABO/Rh compatibility, ISBT 128, FHIR R4, and privacy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import math
import random
import re
from typing import Any, Dict, List, Optional, Tuple

BLOOD_TYPES: Tuple[str, ...] = ("O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+")

COMPONENTS: Dict[str, Dict[str, Any]] = {
    "RBC": {
        "name": "Packed Red Blood Cells",
        "shelf_life_days": 42,
        "storage_temp_c": "2-6 C",
        "max_transit_hours": 36,
        "safety_days_cover": 4.0,
        "critical_days_cover": 1.5,
    },
    "PLT": {
        "name": "Platelets",
        "shelf_life_days": 7,
        "storage_temp_c": "20-24 C (agitated)",
        "max_transit_hours": 18,
        "safety_days_cover": 2.5,
        "critical_days_cover": 1.0,
    },
    "FFP": {
        "name": "Fresh Frozen Plasma",
        "shelf_life_days": 365,
        "storage_temp_c": "<= -18 C",
        "max_transit_hours": 72,
        "safety_days_cover": 7.0,
        "critical_days_cover": 2.5,
    },
    "CRYO": {
        "name": "Cryoprecipitate",
        "shelf_life_days": 365,
        "storage_temp_c": "<= -18 C",
        "max_transit_hours": 72,
        "safety_days_cover": 7.0,
        "critical_days_cover": 2.5,
    },
}

# RBC compatibility: donor_type -> list of compatible recipient_types (ordered by preference: exact first)
RBC_COMPATIBILITY: Dict[str, Tuple[str, ...]] = {
    "O-": ("O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"),
    "O+": ("O+", "A+", "B+", "AB+"),
    "A-": ("A-", "A+", "AB-", "AB+"),
    "A+": ("A+", "AB+"),
    "B-": ("B-", "B+", "AB-", "AB+"),
    "B+": ("B+", "AB+"),
    "AB-": ("AB-", "AB+"),
    "AB+": ("AB+",),
}

# Plasma (FFP) compatibility is inverse of RBC antibodies: AB is universal plasma donor
FFP_COMPATIBILITY: Dict[str, Tuple[str, ...]] = {
    "AB+": ("AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"),
    "AB-": ("AB-", "AB+", "A-", "A+", "B-", "B+", "O-", "O+"),
    "A+": ("A+", "A-", "O+", "O-"),
    "A-": ("A-", "A+", "O-", "O+"),
    "B+": ("B+", "B-", "O+", "O-"),
    "B-": ("B-", "B+", "O-", "O+"),
    "O+": ("O+", "O-"),
    "O-": ("O-", "O+"),
}

# Platelets & Cryo: ABO identical preferred, group O / AB acceptable with substitution penalty
PLT_COMPATIBILITY: Dict[str, Tuple[str, ...]] = {
    "O-": ("O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"),
    "O+": ("O+", "A+", "B+", "AB+"),
    "A-": ("A-", "A+", "AB-", "AB+"),
    "A+": ("A+", "AB+"),
    "B-": ("B-", "B+", "AB-", "AB+"),
    "B+": ("B+", "AB+"),
    "AB-": ("AB-", "AB+"),
    "AB+": ("AB+",),
}

ISBT_ABO_CODES: Dict[str, str] = {
    "O-": "9500",
    "O+": "5100",
    "A-": "0600",
    "A+": "6200",
    "B-": "1700",
    "B+": "7300",
    "AB-": "2800",
    "AB+": "8400",
}
ISBT_CODE_TO_ABO: Dict[str, str] = {v: k for k, v in ISBT_ABO_CODES.items()}

ISBT_PRODUCT_CODES: Dict[str, str] = {
    "RBC": "E0306",
    "PLT": "E3012",
    "FFP": "E4670",
    "CRYO": "E5100",
}
ISBT_PRODUCT_TO_COMP: Dict[str, str] = {v: k for k, v in ISBT_PRODUCT_CODES.items()}


def compatible_recipients(donor_type: str, component: str) -> Tuple[str, ...]:
    """Return compatible recipient blood types for a donor blood type and component."""
    if component in ("FFP", "CRYO"):
        return FFP_COMPATIBILITY.get(donor_type, (donor_type,))
    if component == "PLT":
        return PLT_COMPATIBILITY.get(donor_type, (donor_type,))
    return RBC_COMPATIBILITY.get(donor_type, (donor_type,))


def substitution_penalty(donor_type: str, recipient_type: str, component: str) -> float:
    """Cost penalty for non-identical ABO/Rh matching to preserve universal donor stock."""
    if donor_type == recipient_type:
        return 0.0
    if component in ("FFP", "CRYO"):
        # Preserve AB plasma for unknown/universal trauma recipients
        if donor_type.startswith("AB"):
            return 18.0
        return 8.0
    # RBC / PLT: strongly penalize spending O- on non-O- recipients unless critical
    if donor_type == "O-":
        return 25.0
    if donor_type == "O+":
        return 12.0
    if donor_type.endswith("-") and recipient_type.endswith("+"):
        return 8.0
    return 10.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def estimate_transport_mode(distance_km: float, same_country: bool) -> Tuple[str, float, float]:
    """Return (mode, transit_hours, base_cost_usd_per_shipment)."""
    if distance_km <= 35:
        return ("Medical Drone / Rapid Courier", max(0.4, distance_km / 55.0), 45.0 + distance_km * 1.1)
    if distance_km <= 450 and same_country:
        return ("Refrigerated Ground Courier", max(1.0, distance_km / 72.0), 85.0 + distance_km * 0.65)
    if same_country:
        return ("Domestic Cold-Chain Air", max(2.5, 1.8 + distance_km / 680.0), 320.0 + distance_km * 0.35)
    return ("International Cold-Chain Air + Customs", max(6.0, 4.5 + distance_km / 720.0), 750.0 + distance_km * 0.42)


def generate_isbt128_din(facility_prefix: str, year: int, seq: int) -> str:
    """Generate a compliant-shaped ISBT 128 Donation Identification Number (DIN)."""
    clean = re.sub(r"[^A-Z0-9]", "", facility_prefix.upper())[:5].ljust(5, "0")
    yy = str(year)[-2:]
    return f"={clean}{yy}{seq:06d}"


def parse_isbt128_payload(barcode: str) -> Dict[str, Any]:
    """Parse simplified ISBT 128 concatenated data structures (DIN, Blood Group, Product)."""
    din_match = re.search(r"=([A-Z0-9]{5})(\d{2})(\d{6})", barcode)
    abo_match = re.search(r"=%(\d{4})", barcode)
    prod_match = re.search(r"=<([A-Z0-9]{5})", barcode)

    blood_type = ISBT_CODE_TO_ABO.get(abo_match.group(1), "O+") if abo_match else None
    component = ISBT_PRODUCT_TO_COMP.get(prod_match.group(1), "RBC") if prod_match else None

    return {
        "valid": bool(din_match),
        "din": din_match.group(0) if din_match else None,
        "facility_code": din_match.group(1) if din_match else None,
        "year": 2000 + int(din_match.group(2)) if din_match else None,
        "sequence": int(din_match.group(3)) if din_match else None,
        "blood_type": blood_type,
        "component": component,
    }


def fhir_bundle_to_inventory_signals(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an HL7 FHIR R4 Bundle (BiologicallyDerivedProduct + Schedule/Appointment)
    into privacy-preserving aggregate inventory & surgical demand signals (strips PHI).
    """
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("Expected HL7 FHIR resourceType='Bundle'")

    inventory_counts: Dict[Tuple[str, str], int] = {}
    scheduled_procedures = 0
    facility_id = None

    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        rtype = res.get("resourceType")
        if rtype == "Organization":
            facility_id = res.get("id") or res.get("name")
        elif rtype == "BiologicallyDerivedProduct":
            comp = "RBC"
            code_text = str(res.get("productCode", {}).get("text", "RBC")).upper()
            for k in COMPONENTS:
                if k in code_text or COMPONENTS[k]["name"].upper() in code_text:
                    comp = k
                    break
            blood_type = "O+"
            for prop in res.get("property", []):
                val = str(prop.get("valueCodeableConcept", {}).get("text", "")).upper().strip()
                if val in BLOOD_TYPES:
                    blood_type = val
            qty = int(res.get("quantity", 1))
            key = (component_key := comp, blood_type)
            inventory_counts[key] = inventory_counts.get(key, 0) + max(0, qty)
        elif rtype in ("Appointment", "Procedure", "ServiceRequest"):
            # Only count volume; explicitly discard patient reference / identifiers
            scheduled_procedures += 1

    return {
        "facility_id": facility_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "phi_stripped": True,
        "inventory": [
            {"component": c, "blood_type": bt, "units": u}
            for (c, bt), u in sorted(inventory_counts.items())
        ],
        "scheduled_surgical_demand_index": scheduled_procedures,
    }


def differentially_private_aggregate(values: List[float], epsilon: float = 1.5, clip_max: float = 200.0, seed: Optional[int] = None) -> Dict[str, float]:
    """
    Compute Laplace-noised aggregate sum & mean for federated cross-hospital telemetry.
    Ensures individual hospital spikes cannot be reverse-engineered to patient events.
    """
    if not values:
        return {"count": 0, "noisy_sum": 0.0, "noisy_mean": 0.0, "epsilon": epsilon}
    rng = random.Random(seed)
    clipped = [max(0.0, min(clip_max, float(v))) for v in values]
    true_sum = sum(clipped)
    scale = clip_max / max(0.1, epsilon)
    # Inverse CDF of Laplace(0, scale)
    u = rng.uniform(-0.499999, 0.499999)
    noise = -scale * (1 if u >= 0 else -1) * math.log(1 - 2 * abs(u))
    noisy_sum = max(0.0, true_sum + noise)
    return {
        "count": len(values),
        "noisy_sum": round(noisy_sum, 2),
        "noisy_mean": round(noisy_sum / len(values), 2),
        "epsilon": epsilon,
    }


def anonymize_facility_token(raw_id: str) -> str:
    """One-way salted hash identifier for cross-border federated telemetry."""
    return hashlib.sha256(f"hemonet-fed-v1:{raw_id}".encode("utf-8")).hexdigest()[:16]


@dataclass
class Facility:
    id: str
    name: str
    kind: str  # "hospital" | "blood_bank" | "regional_hub"
    city: str
    country: str
    region: str
    lat: float
    lon: float
    capacity_units: int
    trauma_level: int = 1  # 1 (highest) - 3
    cold_chain_certified: bool = True
    connected: bool = True
    isbt_code: str = "W0000"
    contact_channel: str = "FHIR-R4 / HTTPS"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
