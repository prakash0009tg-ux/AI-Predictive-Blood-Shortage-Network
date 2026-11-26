"""SQLite (WAL) persistence layer for HemoNet state, transfers, pledges, and audit logs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional


class HemoNetStore:
    """Thread-safe SQLite store for facilities, transfer lifecycle, donor pledges, and audit events."""

    VALID_TRANSITIONS: Dict[str, tuple[str, ...]] = {
        "PROPOSED": ("APPROVED", "REJECTED"),
        "APPROVED": ("IN_TRANSIT", "CANCELLED"),
        "IN_TRANSIT": ("RECEIVED",),
        "RECEIVED": (),
        "REJECTED": (),
        "CANCELLED": (),
    }

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS custom_facilities (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_overrides (
                    facility_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    blood_type TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (facility_id, component, blood_type)
                );
                CREATE TABLE IF NOT EXISTS transfers (
                    transfer_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS donor_pledges (
                    pledge_id TEXT PRIMARY KEY,
                    donor_name TEXT NOT NULL,
                    blood_type TEXT NOT NULL,
                    component TEXT NOT NULL,
                    city TEXT NOT NULL,
                    team_name TEXT,
                    scheme_id TEXT,
                    preferred_window TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS caretaker_requests (
                    request_id TEXT PRIMARY KEY,
                    caretaker_name TEXT NOT NULL,
                    patient_relation TEXT NOT NULL,
                    blood_type TEXT NOT NULL,
                    component TEXT NOT NULL,
                    city TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    clinical_notes TEXT,
                    assigned_doctor_id TEXT NOT NULL,
                    assigned_doctor_name TEXT NOT NULL,
                    hospital_name TEXT NOT NULL,
                    who_verification_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS on_air_donors (
                    donor_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guardian_donor_messages (
                    msg_id TEXT PRIMARY KEY,
                    donor_id TEXT NOT NULL,
                    donor_name TEXT NOT NULL,
                    guardian_name TEXT NOT NULL,
                    blood_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    donor_reply TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS service_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    user_role TEXT NOT NULL,
                    category TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def add_custom_facility(self, facility_dict: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO custom_facilities (id, payload_json, created_at) VALUES (?, ?, ?)",
                (facility_dict["id"], json.dumps(facility_dict), now),
            )
            self._audit(conn, "FACILITY_CONNECTED", "network-onboarding", f"Connected {facility_dict['name']} ({facility_dict['city']}, {facility_dict['country']})")

    def list_custom_facilities(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM custom_facilities ORDER BY created_at ASC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def set_inventory_override(self, facility_id: str, component: str, blood_type: str, units: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO inventory_overrides (facility_id, component, blood_type, units, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (facility_id, component, blood_type, max(0, int(units)), now),
            )
            self._audit(conn, "INVENTORY_SYNC", facility_id, f"Synced {units}u of {blood_type} {component}")

    def list_inventory_overrides(self) -> Dict[tuple[str, str, str], int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT facility_id, component, blood_type, units FROM inventory_overrides").fetchall()
            return {(r["facility_id"], r["component"], r["blood_type"]): int(r["units"]) for r in rows}

    def sync_proposed_transfers(self, proposals: List[Dict[str, Any]]) -> None:
        """Persist new optimizer proposals while preserving transfers already in motion."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM transfers WHERE status = 'PROPOSED'")
            existing_active = {
                r["transfer_id"]
                for r in conn.execute("SELECT transfer_id FROM transfers").fetchall()
            }
            for p in proposals:
                tid = p["transfer_id"]
                if tid in existing_active:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO transfers (transfer_id, status, payload_json, updated_at) VALUES (?, ?, ?, ?)",
                    (tid, p["status"], json.dumps(p), now),
                )

    def list_transfers(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT status, payload_json, updated_at FROM transfers ORDER BY transfer_id ASC").fetchall()
            out = []
            for r in rows:
                d = json.loads(r["payload_json"])
                d["status"] = r["status"]
                d["updated_at"] = r["updated_at"]
                out.append(d)
            return out

    def transition_transfer(self, transfer_id: str, next_status: str, actor: str = "clinical-coordinator") -> Dict[str, Any]:
        next_status = next_status.upper().strip()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT status, payload_json FROM transfers WHERE transfer_id = ?", (transfer_id,)).fetchone()
            if not row:
                raise KeyError(f"Transfer {transfer_id} not found")
            current = row["status"]
            if next_status not in self.VALID_TRANSITIONS.get(current, ()):
                raise ValueError(f"Invalid transition {current} -> {next_status}")
            payload = json.loads(row["payload_json"])
            payload["status"] = next_status
            payload["last_actor"] = actor
            payload["updated_at"] = now
            history = payload.get("lifecycle_history", [{"status": current, "at": payload.get("created_at", now)}])
            history.append({"status": next_status, "actor": actor, "at": now})
            payload["lifecycle_history"] = history

            conn.execute(
                "UPDATE transfers SET status = ?, payload_json = ?, updated_at = ? WHERE transfer_id = ?",
                (next_status, json.dumps(payload), now, transfer_id),
            )
            self._audit(conn, f"TRANSFER_{next_status}", actor, f"{transfer_id}: {payload['units']}u {payload['source_blood_type']} {payload['component']} ({payload['source_city']} -> {payload['target_city']})")
            return payload

    def add_pledge(
        self,
        donor_name: str,
        blood_type: str,
        component: str,
        city: str,
        preferred_window: str,
        team_name: str = "",
        scheme_id: str = "scheme-rapid-match",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM donor_pledges").fetchone()["c"]
            pledge_id = f"PLG-{1001 + int(count)}"
            conn.execute(
                """
                INSERT INTO donor_pledges (pledge_id, donor_name, blood_type, component, city, team_name, scheme_id, preferred_window, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pledge_id, donor_name, blood_type, component, city, team_name, scheme_id, preferred_window, now),
            )
            self._audit(conn, "DONOR_PLEDGE", donor_name, f"Pledged {blood_type} {component} in {city} ({preferred_window})")
            return {
                "pledge_id": pledge_id,
                "donor_name": donor_name,
                "blood_type": blood_type,
                "component": component,
                "city": city,
                "team_name": team_name,
                "scheme_id": scheme_id,
                "preferred_window": preferred_window,
                "created_at": now,
            }

    def list_pledges(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM donor_pledges ORDER BY created_at DESC LIMIT 100").fetchall()
            return [dict(r) for r in rows]

    def seed_initial_pledges(self) -> None:
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM donor_pledges").fetchone()["c"]
            if count > 0:
                return
        samples = [
            ("Aarav S.", "O-", "PLT", "New Delhi", "Tomorrow Morning", "AIIMS Med Students Relay", "scheme-platelet-heroes"),
            ("Maya L.", "O-", "RBC", "New York", "Within 48h", "Metro Tech Relay", "scheme-rapid-match"),
            ("Wanjiku M.", "O+", "RBC", "Nairobi", "This Weekend", "Nairobi Runners Circle", "scheme-community-challenge"),
            ("Oliver K.", "AB+", "FFP", "London", "Within 72h", "", "scheme-rapid-match"),
        ]
        for args in samples:
            self.add_pledge(*args)

    def add_caretaker_request(self, record: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM caretaker_requests").fetchone()["c"]
            req_id = f"CARE-{2001 + int(count)}"
            row = {
                "request_id": req_id,
                "caretaker_name": record["caretaker_name"],
                "patient_relation": record.get("patient_relation", "Family Guardian"),
                "blood_type": record["blood_type"],
                "component": record["component"],
                "city": record["city"],
                "urgency": record["urgency"],
                "clinical_notes": record.get("clinical_notes", ""),
                "assigned_doctor_id": record["assigned_doctor_id"],
                "assigned_doctor_name": record["assigned_doctor_name"],
                "hospital_name": record["hospital_name"],
                "who_verification_id": record["who_verification_id"],
                "status": "CONNECTED",
                "created_at": now,
            }
            conn.execute(
                """
                INSERT INTO caretaker_requests (
                    request_id, caretaker_name, patient_relation, blood_type, component,
                    city, urgency, clinical_notes, assigned_doctor_id, assigned_doctor_name,
                    hospital_name, who_verification_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["request_id"], row["caretaker_name"], row["patient_relation"], row["blood_type"],
                    row["component"], row["city"], row["urgency"], row["clinical_notes"],
                    row["assigned_doctor_id"], row["assigned_doctor_name"], row["hospital_name"],
                    row["who_verification_id"], row["status"], row["created_at"],
                ),
            )
            self._audit(
                conn,
                "CARETAKER_DOCTOR_BRIDGE",
                row["caretaker_name"],
                f"{req_id}: Connected caretaker to {row['assigned_doctor_name']} ({row['who_verification_id']}) for {row['blood_type']} {row['component']}",
            )
            return row

    def list_caretaker_requests(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM caretaker_requests ORDER BY created_at DESC LIMIT 50").fetchall()
            return [dict(r) for r in rows]

    def seed_on_air_donors(self, defaults: List[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) AS c FROM on_air_donors").fetchone()["c"]
            if cnt > 0:
                return
            for d in defaults:
                conn.execute(
                    "INSERT OR REPLACE INTO on_air_donors (donor_id, payload_json, updated_at) VALUES (?, ?, ?)",
                    (d["donor_id"], json.dumps(d), now),
                )

    def upsert_on_air_donor(self, donor: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if not donor.get("donor_id"):
                cnt = conn.execute("SELECT COUNT(*) AS c FROM on_air_donors").fetchone()["c"]
                donor["donor_id"] = f"DON-ONAIR-{10 + int(cnt)}"
            conn.execute(
                "INSERT OR REPLACE INTO on_air_donors (donor_id, payload_json, updated_at) VALUES (?, ?, ?)",
                (donor["donor_id"], json.dumps(donor), now),
            )
            self._audit(conn, "DONOR_ON_AIR", donor["name"], f"{donor['name']} ({donor['blood_type']} {donor['component_ready']}) went On Air in {donor['city']}")
            return donor

    def list_on_air_donors(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM on_air_donors ORDER BY updated_at DESC").fetchall()
            return [json.loads(r["payload_json"]) for r in rows]

    def add_guardian_donor_message(
        self,
        donor_id: str,
        donor_name: str,
        guardian_name: str,
        blood_type: str,
        message_text: str,
        donor_reply: str,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) AS c FROM guardian_donor_messages").fetchone()["c"]
            msg_id = f"GDM-{3001 + int(cnt)}"
            rec = {
                "msg_id": msg_id,
                "donor_id": donor_id,
                "donor_name": donor_name,
                "guardian_name": guardian_name,
                "blood_type": blood_type,
                "message_text": message_text,
                "donor_reply": donor_reply,
                "created_at": now,
            }
            conn.execute(
                """
                INSERT INTO guardian_donor_messages (
                    msg_id, donor_id, donor_name, guardian_name, blood_type, message_text, donor_reply, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, donor_id, donor_name, guardian_name, blood_type, message_text, donor_reply, now),
            )
            self._audit(conn, "GUARDIAN_DONOR_CHAT", guardian_name, f"Guardian {guardian_name} contacted On-Air Donor {donor_name} ({blood_type})")
            return rec

    def list_guardian_donor_messages(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM guardian_donor_messages ORDER BY created_at DESC LIMIT 50").fetchall()
            return [dict(r) for r in rows]

    def add_feedback(
        self,
        user_name: str,
        user_role: str,
        category: str,
        rating: int,
        comment: str,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        clamped = max(1, min(5, int(rating)))
        with self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) AS c FROM service_feedback").fetchone()["c"]
            fid = f"FDB-{4001 + int(cnt)}"
            rec = {
                "feedback_id": fid,
                "user_name": user_name,
                "user_role": user_role,
                "category": category,
                "rating": clamped,
                "comment": comment,
                "created_at": now,
            }
            conn.execute(
                """
                INSERT INTO service_feedback (feedback_id, user_name, user_role, category, rating, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (fid, user_name, user_role, category, clamped, comment, now),
            )
            self._audit(conn, "SERVICE_FEEDBACK", user_name, f"Rated {category} {clamped}/5: {comment[:80]}")
            return rec

    def list_feedback(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM service_feedback ORDER BY created_at DESC LIMIT 50").fetchall()]
            avg = round(sum(r["rating"] for r in rows) / len(rows), 2) if rows else 0.0
            return {"items": rows, "count": len(rows), "average_rating": avg}

    def list_audit_log(self, limit: int = 40) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, event_type, actor, summary, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _audit(self, conn: sqlite3.Connection, event_type: str, actor: str, summary: str) -> None:
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, summary, created_at) VALUES (?, ?, ?, ?)",
            (event_type, actor, summary, datetime.now(timezone.utc).isoformat()),
        )
