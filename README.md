# Bakery Order Tracker V2

A local bakery planner for the MGOC50 Lecture 9 exercise, with a blush pink interface. Built with Python's standard library and SQLite; no packages to install.

## Run

1. Install Python 3.9 or later if needed.
2. Open a terminal in this project folder and run `python app.py` (or `python3 app.py`).
3. Open **http://127.0.0.1:8001/**. Keep the terminal open; press Ctrl+C to stop.

The included `bakery.db` resumes the saved exercise state. To start the exercise from Monday, November 23, 2026, stop the app and move `bakery.db` to a backup location before restarting. The app creates a fresh database from the included starting data.

## Use

- Review the pickup schedule and book helpers at least two days ahead.
- Click **Fetch DMs** to load that exercise day's messages.
- Reject new requests you do not want, then use **Accept all and go to next day**. The app rechecks each remaining request in order, completes today's pickups at 5 PM, and advances the exercise.
- Review the decision log, past pickups, and weekly revenue and helper costs.

Eligible changes and cancellations apply during fetch. Requested orders do not reserve capacity until accepted. All decisions follow fixed rules; there is no AI or live Instagram connection.

## Rules

- Cupcakes: $40 per dozen, 30 oven minutes per dozen.
- Cakes: $120 each, 2 oven hours each. One product per order.
- Eight oven hours per day; all baking happens on the pickup day.
- A helper costs $100 and adds four oven hours for one day.
- Orders, changes, cancellations, and helper bookings require two calendar days' notice.
- Revenue after helpers subtracts helper costs only, not ingredients or other expenses.

## Files

- `app.py`: server, interface, and business rules.
- `bakery.db`: saved exercise state, separate from the original V1 and V2 copies.
- `data/existing_orders.csv`: 13 starting orders.
- `data/InstagramOrders_*.html`: 25 example messages across seven days.
- `README.html`: detailed user guide, also available through **User guide** in the app.

This repository contains Version 2 and its required materials. The separate Workflow Builder and Version 1 are not required.

The app listens only on this computer. Stop another V2 instance if port 8001 is already in use. Changes made in the app are saved in the local database; GitHub does not update automatically.
