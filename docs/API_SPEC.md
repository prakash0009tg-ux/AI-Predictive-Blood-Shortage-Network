# HemoNet API & Event Specification

Live machine-readable OpenAPI 3.1 JSON is served at `GET /openapi.json` when the server is running.

## REST Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Health check & supported blood types / components |
| `GET` | `/openapi.json` | OpenAPI 3.1 specification |
| `GET` | `/api/overview` | Complete network snapshot (facilities, forecasts, backtest metrics, alerts, transfers, donor feed, privacy, audit) |
| `GET` | `/api/facilities` | List all connected hospitals and blood banks worldwide |
| `POST` | `/api/facilities` | Connect a new hospital or blood bank immediately into the global network |
| `POST` | `/api/inventory/sync` | Sync unit count for `(facility_id, component, blood_type)` |
| `POST` | `/api/fhir/ingest` | Ingest HL7 FHIR R4 Bundle (automatically strips PHI) |
| `POST` | `/api/isbt128/parse` | Parse ISBT 128 concatenated blood unit barcode |
| `POST` | `/api/simulate` | Run what-if surge simulation (`region`, `surgery_multiplier`, `trauma_multiplier`, `outbreak_boost`, `donor_turnout_multiplier`) |
| `POST` | `/api/transfers/{transfer_id}/transition` | Advance transfer lifecycle (`PROPOSED -> APPROVED -> IN_TRANSIT -> RECEIVED`, or `REJECTED`/`CANCELLED`) |
| `GET` | `/api/donor/feed` | Current rotating donor motivation scheme, urgent blood types, and live dialog stream |
| `POST` | `/api/donor/dialog` | Send message to the interactive Donor Companion dialog |
| `POST` | `/api/donor/pledges` | Record a blood donation pledge & optional team relay |
| `GET` | `/api/inventory/monitor` | Real-time inventory monitoring, historic 30d consumption series, and `8x4` blood group availability matrix (`?blood_type=&component=&region=`) |
| `GET` | `/api/ecosystem` | Compiled WHO-verified specialist doctors (with experience, success rate, mental strength), medical colleges, drug manufacturers, and experts |
| `GET` | `/api/caretaker/requests` | List logged patient caretaker-to-specialist doctor consultation bridges |
| `POST` | `/api/caretaker/connect` | Match and connect a patient caretaker/guardian to the best WHO-verified blood-group specialist doctor |

## Real-Time Event Stream (SSE / AsyncAPI Mapping)

Endpoint: `GET /api/events` (`text/event-stream`)

- **`donor_tick`**: Emitted every 8 seconds and whenever a donor pledge is submitted; rotates active motivation schemes and updates community lives-protected counters.
- **`network_update`**: Emitted whenever a hospital connects, inventory/FHIR syncs, a what-if simulation runs, or a transfer changes state.

### Transfer Lifecycle State Machine
`PROPOSED` $\rightarrow$ `APPROVED` $\rightarrow$ `IN_TRANSIT` $\rightarrow$ `RECEIVED`  
(Alternative terminal states: `PROPOSED -> REJECTED`, `APPROVED -> CANCELLED`)
