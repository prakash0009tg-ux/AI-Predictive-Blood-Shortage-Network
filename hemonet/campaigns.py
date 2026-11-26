"""Continuous Donor Motivation Scheme & Interactive Conversational Dialog Engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


MOTIVATION_SCHEMES: List[Dict[str, Any]] = [
    {
        "scheme_id": "scheme-rapid-match",
        "title": "72-Hour Life-Bridge: Pre-Shortage Rapid Match",
        "badge": "LIVE FORECAST TRIGGERED",
        "tagline": "Your single 45-minute visit bridges predicted hospital gaps before an emergency starts.",
        "impact_fact": "1 whole-blood donation separates into RBCs, platelets, and plasma — helping up to 3 patients.",
        "cta_label": "Check 30s Eligibility & Pledge",
        "incentive": "Verified Life-Bridge Digital Certificate + Live Hospital Delivery Milestone Alert",
    },
    {
        "scheme_id": "scheme-platelet-heroes",
        "title": "5-Day Freshness Circle (Platelet & O- Guardians)",
        "badge": "HIGH-URGENCY COMPONENT",
        "tagline": "Platelets last only 5–7 days. Scheduled apheresis donors keep cancer & dengue wards safe.",
        "impact_fact": "Platelets cannot be stockpiled long-term — steady weekly heroes prevent sudden ward delays.",
        "cta_label": "Join Platelet Guardian Circle",
        "incentive": "Priority Fast-Track Appointment + Quarterly Impact Report",
    },
    {
        "scheme_id": "scheme-community-challenge",
        "title": "Campus, Workplace & Neighborhood Relay",
        "badge": "COMMUNITY MULTIPLIER",
        "tagline": "Team up with colleagues or neighbors: every 10 pledges unlocks a mobile blood drive van.",
        "impact_fact": "Group drives lift regional donor turnout by 34% during holiday & weather dips.",
        "cta_label": "Start or Join a Local Relay",
        "incentive": "Team Resilience Badge + Live City Readiness Leaderboard",
    },
    {
        "scheme_id": "scheme-myth-buster",
        "title": "First-Timer Comfort & Myth-Buster Guide",
        "badge": "FIRST-TIME FRIENDLY",
        "tagline": "Nervous about needles or time? The actual draw takes 8–10 minutes with trained care staff.",
        "impact_fact": "Your body replaces plasma within 24–48 hours and red cells safely over a few weeks.",
        "cta_label": "Ask the Donor Guide",
        "incentive": "Step-by-Step Prep Checklist + Gentle First-Timer Support",
    },
]


def build_continuous_donor_feed(
    alerts: List[Dict[str, Any]],
    uncovered_deficits: List[Dict[str, Any]],
    pledges: List[Dict[str, Any]],
    tick: int = 0,
) -> Dict[str, Any]:
    """
    Generate the continuously rotating donor motivation dialog payload grounded in
    live predicted shortages so donors see real-time, meaningful local urgency.
    """
    critical_alerts = [a for a in alerts if a["severity"] in ("CRITICAL", "WARNING")]
    top_needs = critical_alerts[:4]

    # Aggregate needed blood types for targeted callouts
    urgent_types: Dict[str, int] = {}
    urgent_cities: List[str] = []
    for a in critical_alerts:
        urgent_types[a["blood_type"]] = urgent_types.get(a["blood_type"], 0) + max(1, a["recommended_units"])
        if a["city"] not in urgent_cities:
            urgent_cities.append(a["city"])
    for u in uncovered_deficits:
        urgent_types[u["blood_type"]] = urgent_types.get(u["blood_type"], 0) + u["uncovered_units"]

    sorted_types = sorted(urgent_types.items(), key=lambda kv: kv[1], reverse=True)
    active_scheme = MOTIVATION_SCHEMES[tick % len(MOTIVATION_SCHEMES)]

    prompts = [
        {
            "speaker": "HemoNet Donor Companion",
            "message": (
                f"Right now our AI early-warning radar sees elevated need for "
                f"{', '.join(bt for bt, _ in sorted_types[:3]) or 'O- & Platelets'} "
                f"across {', '.join(urgent_cities[:3]) or 'partner trauma centers'} in the next 72 hours."
            ),
            "tone": "urgent-hopeful",
        },
        {
            "speaker": "Motivation Scheme",
            "message": f"{active_scheme['title']}: {active_scheme['tagline']}",
            "tone": "inspiring",
        },
        {
            "speaker": "Impact Counter",
            "message": f"{len(pledges)} community pledges logged — potentially protecting {len(pledges) * 3} hospital patients ahead of peak demand.",
            "tone": "celebratory",
        },
    ]

    return {
        "tick": tick,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "active_scheme": active_scheme,
        "all_schemes": MOTIVATION_SCHEMES,
        "urgent_blood_types": [{"blood_type": bt, "units_needed": u} for bt, u in sorted_types[:5]],
        "priority_cities": urgent_cities[:6],
        "top_hospital_callouts": [
            {
                "facility_name": a["facility_name"],
                "city": a["city"],
                "country": a["country"],
                "blood_type": a["blood_type"],
                "component": a["component"],
                "lead_time_hours": a["lead_time_hours"],
                "reason": a["driver_note"],
            }
            for a in top_needs
        ],
        "live_dialog_stream": prompts,
        "community_pledge_count": len(pledges),
        "estimated_lives_impacted": len(pledges) * 3,
    }


def evaluate_donor_dialog_step(
    user_message: str,
    context: Dict[str, Any] | None = None,
    urgent_blood_types: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Deterministic, safe conversational responder for the continuously running
    Donor Motivation Dialog (handles myth-busting, eligibility pre-check, prep tips,
    blood-type urgency, and group challenge creation).
    """
    text = (user_message or "").strip().lower()
    ctx = context or {}
    urgent = urgent_blood_types or []
    top_bt = urgent[0]["blood_type"] if urgent else "O-"

    if any(k in text for k in ("hurt", "pain", "needle", "scared", "afraid", "nervous")):
        return {
            "reply": (
                "It is completely normal to feel nervous! Only a quick 1-second pinch is felt at the start, "
                "and the draw itself takes ~8–10 minutes while you relax. Let the nurse know you're a first-timer — "
                "they offer extra comfort, hydration, and slow recovery time."
            ),
            "quick_replies": ["Am I eligible today?", "How should I prepare?", "Pledge a donation"],
            "motivation_nudge": "92% of first-time donors say it was much easier than expected!",
        }

    if any(k in text for k in ("eligible", "eligibility", "can i", "weight", "age", "qualify")):
        return {
            "reply": (
                "General baseline checklist (final screening is always done safely by clinic staff): "
                "typically age 18–65 (16–17 with consent in some regions), weight >= 50 kg (110 lbs), "
                "feeling well and fever-free today, and at least 56 days since your last whole-blood donation. "
                "Have you eaten a good meal and hydrated today?"
            ),
            "quick_replies": ["Yes, I feel great — Pledge now", "What should I eat/drink?", "Organize a workplace relay"],
            "motivation_nudge": f"Even if you aren't sure of your blood type, clinics test it free — and {top_bt} & Platelets are especially prized right now.",
        }

    if any(k in text for k in ("prepare", "prep", "eat", "drink", "before", "after")):
        return {
            "reply": (
                "3-step Hero Prep: (1) Drink an extra 500 ml of water before arriving, "
                "(2) Eat an iron-rich balanced meal (avoid heavy greasy foods right before), "
                "(3) Bring a photo ID and wear sleeves that roll up easily. Afterward, enjoy snacks and rest 15 minutes!"
            ),
            "quick_replies": ["I'm ready to pledge", "Which hospitals need help?", "Start a team challenge"],
            "motivation_nudge": "Hydration makes donation smoother and faster by up to 20%.",
        }

    if any(k in text for k in ("team", "group", "company", "college", "university", "challenge", "relay")):
        return {
            "reply": (
                "Awesome! Community Relays are our highest-leverage scheme: gather 5–10 friends, classmates, or coworkers "
                "to pledge within the same 7-day window. You'll unlock a shared Resilience Badge and help protect regional "
                "hospitals against sudden surgical or seasonal surges."
            ),
            "quick_replies": ["Pledge & tag a team", "See live hospital shortages", "Check eligibility"],
            "motivation_nudge": "A 10-person relay can replenish an entire hospital trauma unit for a weekend.",
        }

    if any(k in text for k in ("need", "shortage", "where", "urgent", "hospital")):
        bt_list = ", ".join(f"{item['blood_type']} ({item['units_needed']}u)" for item in urgent[:4]) or "O-, O+, Platelets"
        return {
            "reply": (
                f"Our predictive network is currently preparing hospitals ahead of projected gaps in: {bt_list}. "
                "Booking a routine slot in the next 48–72 hours lets blood banks process, test, and route units calmly before an emergency hits."
            ),
            "quick_replies": ["Pledge a donation slot", "Am I eligible today?", "How should I prepare?"],
            "motivation_nudge": "Early donors eliminate crisis delays!",
        }

    city = ctx.get("city", "your nearest connected blood center")
    return {
        "reply": (
            f"Welcome to the HemoNet Donor Companion! Every pledge in {city} helps hospitals stay ahead of upcoming shortages. "
            "Ask me about eligibility, first-timer comfort, preparation tips, live local hospital needs, or tap below to pledge a slot."
        ),
        "quick_replies": ["Which hospitals need help?", "Am I eligible today?", "First-timer comfort tips", "Pledge a donation"],
        "motivation_nudge": "1 donation = up to 3 lives protected before emergencies arrive.",
    }
