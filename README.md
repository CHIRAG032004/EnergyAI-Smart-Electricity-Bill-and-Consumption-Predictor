# EnergyAI – Smart Electricity Bill & Consumption Predictor

Python standard library only (3.8+), SQLite database, works offline. No `pip install` needed.

## Run in VS Code
1. File > Open Folder... and choose this `EnergyAI` folder.
2. Install the **Python** extension if VS Code asks.
3. Press **F5** (pick *Run EnergyAI server*), or in the terminal run: `python server.py`
4. The browser opens at http://127.0.0.1:8000 (the port moves up if 8000 is busy; see the terminal).
5. Open the **Upload** tab and drop `data/demo_monthly_bills.csv` to see the 30-month analysis.

Run the tests: `python -m unittest discover -s tests -v`

## Files
| File | Purpose |
|---|---|
| `server.py` | HTTP server, CSV parser, report builder (GET `/api/data`, `/api/report`; POST `/api/upload`, `/api/reset`) |
| `energy_predictor.py` | Forecast, anomaly detection, slab tariff, appliance simulator, SQLite (`energy_demo.db`, created on first run) |
| `index.html` | Single-page dashboard (7 tabs) |
| `data/demo_monthly_bills.csv` | Sample monthly bills |
| `docs/` | Project report, presentation and the reference report |

Keep `server.py`, `energy_predictor.py` and `index.html` in the same folder.
Team: Gagan Kapoor (26MCA033), Chirag (26MCA035).
