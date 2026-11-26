# HemoNet — Global AI Predictive Blood Network

HemoNet connects hospitals and blood banks worldwide to forecast blood shortages **24 hours to 14 days before they become critical emergencies**, proactively match compatible blood reserves across cold-chain routes, and run a **continuous donor motivation dialog** that mobilizes community donors ahead of peak demand.

## Key Features
- **Global Hospital Connectivity**: Pre-loaded with 23 major hospitals and regional blood hubs across 8 global macro-regions, plus an instant **Connect Hospital** onboarding wizard and **HL7 FHIR R4 / ISBT 128** adapters.
- **Multi-Horizon AI Shortage Forecaster**: Ridge-regularized spatiotemporal demand & collection forecasting with conformal P10/P50/P90 uncertainty bands, walk-forward backtesting (MAE, Pinball loss, Critical Recall, Lead-Time Gained), and additive SHAP-style drivers.
- **Min-Cost Max-Flow Compatibility Optimizer**: Routes surplus units to at-risk hospitals while enforcing ABO/Rh rules (preserving universal O- RBCs and AB plasma), unit shelf life vs. cold-chain transit limits, and expiration-rescue prioritization.
- **Shortage Alerts & What-If Preparedness**: Early-warning cards with concrete clinical preparedness steps, 1-click route approval, and a real-time surge stress-tester.
- **Real-Time Inventory & Historic Consumption Analytics**: Live `8 blood groups x 4 components` availability matrix, 30-day historic consumption vs. collections SVG visualization, top consuming hospitals, and live unit syncing.
- **Geographical Visualization with Layered Medical Ecosystem**: Interactive world map filtering availability by blood group and toggling hospitals, blood hubs, WHO-verified doctors, medical colleges, and drug manufacturers.
- **Caretaker-to-Specialist Doctor Bridge & WHO-Verified Registry**: Connects patient caretakers/guardians directly to specialist blood-group doctors ranked by working experience, success rate, mental strength score, and WHO verification ID, alongside compiled directories of WHO-verified medical colleges, plasma/biologics drug manufacturers, and global medical experts.
- **Continuous Donor Motivation Dialog**: Live SSE-driven scheme rotator + interactive conversational Donor Companion (eligibility check, first-timer comfort guide, prep tips, team relays) + 20-second pledge flow.
- **Accessible for Everyone**: High-contrast mode, large-text toggle, keyboard navigation, ARIA live alerts, and zero external dependencies (runs on Python 3 standard library).

## Quick Start

```bash
python -m hemonet.server
```

Then open `http://127.0.0.1:8765` in your browser.

## Run Tests

```bash
python -m unittest discover -s tests -v
```
