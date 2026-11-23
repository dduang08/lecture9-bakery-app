"""Run the structured bakery exercise with Python 3.9 or newer."""

import csv
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from html import escape, unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "bakery.db"
START = date(2026, 11, 23)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PRODUCTS = {"cupcakes": (40, 30), "cake": (120, 120)}
TOKEN = secrets.token_urlsafe(24)


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def pickup_date(text):
    for pattern in ("%a %b %d %Y", "%a %d %b %Y"):
        try:
            return datetime.strptime(f"{text} 2026", pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized pickup date: {text}")


def parse_item(text):
    match = re.fullmatch(r"([1-9]\d*) (dozen cupcakes|cakes?)", text.lower())
    if not match:
        raise ValueError(f"Unrecognized item: {text}")
    return ("cupcakes" if match[2] == "dozen cupcakes" else "cake", int(match[1]))


def ensure_day(db, day):
    db.execute("INSERT OR IGNORE INTO days(day) VALUES (?)", (day,))


def initialize(path):
    with database(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS days (
                day TEXT PRIMARY KEY, helper INTEGER NOT NULL DEFAULT 0,
                fetched INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS exercise (
                id INTEGER PRIMARY KEY CHECK (id = 1), today TEXT NOT NULL REFERENCES days(day)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY, customer TEXT NOT NULL, product TEXT NOT NULL,
                qty INTEGER NOT NULL CHECK (qty > 0), pickup TEXT NOT NULL REFERENCES days(day),
                status TEXT NOT NULL CHECK (status IN ('Requested','Accepted','Rejected','Picked up','Cancelled'))
            );
            CREATE TABLE IF NOT EXISTS messages (
                received TEXT NOT NULL REFERENCES days(day), position INTEGER NOT NULL,
                raw TEXT NOT NULL, order_id INTEGER REFERENCES orders(id),
                outcome TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (received, position)
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM exercise").fetchone():
            return
        ensure_day(db, START.isoformat())
        db.execute("INSERT INTO exercise VALUES (1, ?)", (START.isoformat(),))
        with (ROOT / "data" / "existing_orders.csv").open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                product, qty = parse_item(row["item"])
                pickup = pickup_date(row["pickup_day"])
                ensure_day(db, pickup)
                db.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, 'Accepted')",
                           (int(row["order"].lstrip("#")), row["customer"], product, qty, pickup))


def read_messages(today):
    if not START <= today < START + timedelta(days=7):
        return []
    source = ROOT / "data" / f"InstagramOrders_{WEEKDAYS[today.weekday()]}.html"
    html = source.read_text(encoding="utf-8")
    return [unescape(raw) for raw in re.findall(r'<p class="message">(.*?)</p>', html, re.S)]


def today_at(db):
    return date.fromisoformat(db.execute("SELECT today FROM exercise").fetchone()[0])


def commitments(db, pickup):
    return db.execute("""SELECT * FROM orders WHERE pickup = ? AND status IN ('Accepted', 'Picked up')
                         ORDER BY id""", (pickup,)).fetchall()


def helper_booked(db, pickup):
    row = db.execute("SELECT helper FROM days WHERE day = ?", (pickup,)).fetchone()
    return bool(row and row[0])


def oven_minutes(order):
    return PRODUCTS[order["product"]][1] * order["qty"]


def day_totals(db, pickup):
    orders = commitments(db, pickup)
    helper = helper_booked(db, pickup)
    used = sum(map(oven_minutes, orders))
    revenue = sum(PRODUCTS[order["product"]][0] * order["qty"] for order in orders)
    return orders, 480 + 240 * helper - used, revenue, 100 * helper


def week_totals(db, monday):
    totals = [day_totals(db, (monday + timedelta(days=i)).isoformat()) for i in range(7)]
    return sum(day[2] for day in totals), sum(day[3] for day in totals)


def enough_notice(today, pickup):
    return date.fromisoformat(pickup) >= today + timedelta(days=2)


def check_order(db, today, order, replaced=None):
    if not enough_notice(today, order["pickup"]):
        return "Needs at least two days’ notice."
    _, available, _, _ = day_totals(db, order["pickup"])
    if replaced:
        available += oven_minutes(replaced)
    needed = oven_minutes(order)
    if needed > available:
        return f"Needs {needed / 60:g} hr of oven time. Only {available / 60:g} hr available."
    return ""


def process_message(db, today, raw):
    fields = dict(line.split(": ", 1) for line in raw.splitlines())
    customer = fields["Name"]
    pickup = pickup_date(fields["Pickup Date"])
    ensure_day(db, pickup)
    text = fields["Order"]
    if text != "Cancel" and not text.startswith("Change to "):
        product, qty = parse_item(text)
        proposal = dict(product=product, qty=qty, pickup=pickup)
        reason = check_order(db, today, proposal)
        status = "Rejected" if reason else "Requested"
        order = db.execute("INSERT INTO orders(customer,product,qty,pickup,status) VALUES (?,?,?,?,?)",
                           (customer, product, qty, pickup, status))
        return order.lastrowid, status, reason
    if not enough_notice(today, pickup):
        return None, "Rejected", "Needs at least two days’ notice."
    matches = [order for order in commitments(db, pickup)
               if order["status"] == "Accepted" and order["customer"].casefold() == customer.casefold()]
    if len(matches) != 1:
        return None, "Rejected", "No unique accepted order matches this customer and date."
    target = matches[0]
    if text == "Cancel":
        db.execute("UPDATE orders SET status = 'Cancelled' WHERE id = ?", (target["id"],))
        return target["id"], "Cancelled", ""
    product, qty = parse_item(text.removeprefix("Change to "))
    reason = check_order(db, today, dict(product=product, qty=qty, pickup=pickup), target)
    if reason:
        return target["id"], "Rejected", reason
    db.execute("UPDATE orders SET product = ?, qty = ? WHERE id = ?", (product, qty, target["id"]))
    return target["id"], "Changed", ""


def fetch(db, today):
    key = today.isoformat()
    if db.execute("SELECT fetched FROM days WHERE day = ?", (key,)).fetchone()[0]:
        return "Today’s DMs have already been fetched."
    messages = read_messages(today)
    for position, raw in enumerate(messages, 1):
        order_id, outcome, reason = process_message(db, today, raw)
        db.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                   (key, position, raw, order_id, outcome, reason))
    db.execute("UPDATE days SET fetched = 1 WHERE day = ?", (key,))
    return f"Fetched {len(messages)} DMs."


def resolve_order(db, order_id, status, reason=""):
    db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    db.execute("""UPDATE messages SET outcome = ?, reason = ?
                  WHERE order_id = ? AND outcome = 'Requested'""", (status, reason, order_id))


def advance(db, today):
    key = today.isoformat()
    if not db.execute("SELECT fetched FROM days WHERE day = ?", (key,)).fetchone()[0]:
        raise ValueError("Fetch today’s DMs before advancing.")
    pending = db.execute("SELECT * FROM orders WHERE status = 'Requested' ORDER BY id").fetchall()
    rejected = 0
    for order in pending:
        reason = check_order(db, today, order)
        resolve_order(db, order["id"], "Rejected" if reason else "Accepted", reason)
        rejected += bool(reason)
    picked_up = db.execute("UPDATE orders SET status = 'Picked up' WHERE pickup = ? AND status = 'Accepted'",
                           (key,)).rowcount
    tomorrow = (today + timedelta(days=1)).isoformat()
    ensure_day(db, tomorrow)
    db.execute("UPDATE exercise SET today = ?", (tomorrow,))
    return f"Accepted {len(pending) - rejected}, rejected {rejected}, and marked {picked_up} picked up at 5 PM."


def apply_action(db, fields):
    today = today_at(db)
    if fields["today"] != today.isoformat():
        raise ValueError("The exercise date changed. Review the current day and try again.")
    action = fields["action"]
    if action == "fetch":
        return fetch(db, today)
    if action == "advance":
        return advance(db, today)
    if action == "reject":
        order = db.execute("SELECT id FROM orders WHERE id = ? AND status = 'Requested'",
                           (int(fields["id"]),)).fetchone()
        if not order:
            raise ValueError("That order is already resolved.")
        resolve_order(db, order["id"], "Rejected", "Rejected by the baker.")
        return f"Rejected order #{order['id']}."
    if action == "helper":
        pickup = date.fromisoformat(fields["pickup"]).isoformat()
        if not enough_notice(today, pickup):
            raise ValueError("A helper needs at least two days’ notice.")
        if helper_booked(db, pickup):
            return "A helper is already booked for this date."
        ensure_day(db, pickup)
        db.execute("UPDATE days SET helper = 1 WHERE day = ?", (pickup,))
        return "Booked one helper for $100 and added four oven hours."
    raise ValueError("Unknown action.")


def label(day):
    return f"{day:%a, %b} {day.day}"


def item(order):
    unit = "dozen cupcakes" if order["product"] == "cupcakes" else "cake"
    if unit == "cake" and order["qty"] != 1:
        unit = "cakes"
    return f"{order['qty']} {unit}"


def button(today, action, caption, *, field="", value="", disabled=False):
    extra = f'<input type="hidden" name="{field}" value="{escape(str(value))}">' if field else ""
    return f"""<form method="post" action="/">
        <input type="hidden" name="token" value="{TOKEN}">
        <input type="hidden" name="today" value="{today}">{extra}
        <button name="action" value="{action}" {'disabled' if disabled else ''}>{caption}</button></form>"""


def schedule(db, today, pickup):
    orders, available, revenue, cost = day_totals(db, pickup)
    limit = 12 if cost else 8
    booking = ""
    if cost:
        booking = '<p class="flag ready">Helper booked · $100</p>'
    elif enough_notice(today, pickup):
        booking = button(today, "helper", "Hire helper · $100", field="pickup", value=pickup)
    rows = "".join(f"<li><span>#{order['id']} · {escape(order['customer'])}</span>"
                   f"<span>{item(order)} · {order['status']}</span></li>"
                   for order in orders)
    return f"""<article class="schedule"><div class="row"><strong>{label(date.fromisoformat(pickup))}</strong>
        <span>{(limit * 60 - available) / 60:g} hr / {limit} hr</span></div>
        <meter min="0" max="{limit * 60}" value="{limit * 60 - available}" aria-label="Oven minutes booked for {pickup}"></meter>
        <p>{available / 60:g} hr free · ${revenue} in orders · 5 PM pickup</p>
        <ul>{rows}</ul>{booking}</article>"""


def page(db, feedback):
    today = today_at(db)
    key = today.isoformat()
    fetched = db.execute("SELECT fetched FROM days WHERE day = ?", (key,)).fetchone()[0]
    monday = today - timedelta(days=today.weekday())
    revenue, cost = week_totals(db, monday)
    orders = db.execute("SELECT * FROM orders WHERE status = 'Requested' ORDER BY id").fetchall()
    pending = "".join(
        f"""<article class="request"><div class="row"><strong>{escape(order['customer'])}</strong><span class="kind">New</span></div>
        <p>{item(order)} · {oven_minutes(order) / 60:g} hr · ${order['qty'] * PRODUCTS[order['product']][0]}</p>
        <p>Pickup {label(date.fromisoformat(order['pickup']))} at 5 PM</p>
        {button(today, 'reject', 'Reject', field='id', value=order['id'])}</article>""" for order in orders)
    log = "".join(f"""<article><strong>{row['outcome']}</strong> · {label(date.fromisoformat(row['received']))}
        <p class="raw">{escape(row['raw'])}</p><p>{escape(row['reason'])}</p></article>"""
        for row in db.execute("SELECT * FROM messages ORDER BY received DESC, position"))
    dates = {(today + timedelta(days=i)).isoformat() for i in range(7)}
    dates.update(row[0] for row in db.execute("SELECT DISTINCT pickup FROM orders WHERE status = 'Accepted'"))
    past = [row[0] for row in db.execute("SELECT DISTINCT pickup FROM orders WHERE status = 'Picked up' ORDER BY pickup DESC")]
    feedback = f'<p class="feedback" role="status">{escape(feedback)}</p>' if feedback else ""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Bakery Order Tracker V2</title><style>{CSS}</style><main>
    <header><div><h1>Bakery Order Tracker <span class="version">V2</span></h1><p>{today:%A, %B} {today.day}, {today.year} · Pickup at 5 PM</p></div>
    <nav><a href="/guide">User guide</a></nav></header>
    <div class="actions">{button(today, 'fetch', 'Fetch DMs' if not fetched else 'DMs fetched', disabled=fetched)}
    {button(today, 'advance', 'Accept all and go to next day', disabled=not fetched)}</div>{feedback}
    <section class="totals"><h2>Week of {label(monday)}</h2><dl>
        <div><dt>Order revenue</dt><dd>${revenue}</dd></div>
        <div><dt>Helper cost</dt><dd>${cost}</dd></div>
        <div><dt>Revenue after helpers</dt><dd>${revenue - cost}</dd></div></dl></section>
    <div class="columns"><section><h2>Inbox <span class="count">{len(orders)} pending</span></h2>
        <p class="hint">Remaining requests are checked in message order when you advance.</p>
        {pending or '<p>No requests awaiting your decision.</p>'}
        <details><summary>Decision log</summary>{log or '<p>No DMs fetched yet.</p>'}</details></section>
    <section><h2>Pickup schedule</h2>{''.join(schedule(db, today, day) for day in sorted(dates))}
        <details><summary>Past pickups</summary>
        {''.join(schedule(db, today, day) for day in past) or '<p>No completed pickups yet.</p>'}</details>
    </section></div></main></html>"""


CSS = """
    :root { color-scheme: light; font: 16px/1.48 "Segoe UI", system-ui, sans-serif;
        color: #402530; background: #fff5f8; }
    * { box-sizing: border-box; }
    body { margin: 0; border-top: 6px solid #ad476a; }
    main { max-width: 1120px; margin: auto; padding: 36px 24px; }
    header, nav, .row, h2, .actions, dl { display: flex; align-items: center; gap: 12px; }
    header, .row { justify-content: space-between; flex-wrap: wrap; }
    header { border-bottom: 1px solid #e7c6d1; padding-bottom: 18px; }
    h1, h2 { font-family: Georgia, serif; }
    h1 { font-size: clamp(1.85rem, 5vw, 3rem); line-height: 1.1; margin: 0; }
    h2 { font-size: 1.6rem; margin: 24px 0 12px; flex-wrap: wrap; }
    h3 { font-size: 1rem; }
    p { margin: 5px 0; }
    a { color: #913b5b; font-weight: 650; }
    .version, .kind, .count, .flag { font: 650 .875rem/1.4 "Segoe UI", system-ui, sans-serif;
        border-radius: 8px; padding: 4px 8px; background: #f7e1e9; color: #80324f; }
    .version { display: inline-block; vertical-align: middle; }
    .actions { flex-wrap: wrap; margin: 18px 0; }
    dl { margin: 0; flex-wrap: wrap; align-items: start; gap: 16px 40px; }
    dt { font-size: .875rem; color: #754e5c; }
    dd { margin: 2px 0 0; font-weight: 700; font-size: 1.5rem; }
    article, details, .totals { background: #fffdfd; border: 1px solid #ebd0da;
        border-radius: 12px; padding: 14px 16px; margin: 0 0 10px; overflow-wrap: anywhere; }
    .totals { background: #fbe8ef; margin: 0; padding: 16px 20px; }
    .totals h2 { font-size: 1.2rem; margin: 0 0 12px; }
    .columns { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
    .hint { color: #754e5c; margin-bottom: 12px; font-size: .875rem; }
    button { font: inherit; font-weight: 600; background: #913b5b; border: 1px solid #913b5b;
        border-radius: 6px; color: white; padding: 5px 11px; cursor: pointer; }
    button:hover:not(:disabled) { background: #78304b; }
    article button { background: #fffdfd; color: #913b5b; border-color: #d5a1b4; }
    article button:hover:not(:disabled) { background: #fbe8ef; }
    button:disabled { background: #f0e3e8; border-color: #f0e3e8; color: #725b65; cursor: default; }
    a:focus-visible, button:focus-visible, summary:focus-visible { outline: 3px solid #ad476a; outline-offset: 3px; }
    summary { font-weight: 600; cursor: pointer; }
    details article { margin-top: 10px; }
    .raw { white-space: pre-line; }
    .flag { display: inline-block; margin: 8px 0; }
    .ready { background: #f7e1e9; color: #80324f; }
    .feedback { background: #fbe1eb; border: 1px solid #e7b5c7; padding: 10px 14px;
        margin: 12px 0; border-radius: 8px; }
    meter { width: 100%; height: 12px; accent-color: #ad476a; }
    meter::-webkit-meter-bar { background: #f5e6ec; border: none; border-radius: 6px; }
    meter::-webkit-meter-optimum-value { background: #ad476a; border-radius: 6px; }
    meter::-moz-meter-bar { background: #ad476a; }
    ul { list-style: none; padding: 0; margin: 8px 0; }
    li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px 12px;
        border-top: 1px solid #f1e1e7; padding: 5px 0; }
    ul:empty { display: none; }
    @media(max-width: 720px) { .columns { grid-template-columns: 1fr; gap: 0; } }
    @media(max-width: 420px) { main { padding: 24px 16px; } .actions form { width: 100%; }
        .actions button { width: 100%; } }
"""


class Handler(BaseHTTPRequestHandler):
    db_path = DEFAULT_DB

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/guide":
            html = (ROOT / "README.html").read_text(encoding="utf-8")
        elif url.path == "/":
            with database(self.db_path) as db:
                db.execute("BEGIN")
                html = page(db, parse_qs(url.query).get("message", [""])[0])
        else:
            self.send_error(404)
            return
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError
            fields = {key: values[0] for key, values in parse_qs(
                self.rfile.read(size).decode(), max_num_fields=12).items()}
        except ValueError:
            self.send_error(400)
            return
        if fields.pop("token", None) != TOKEN:
            self.send_error(403, "Reload the page and try again.")
            return
        try:
            with database(self.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                feedback = apply_action(db, fields)
        except (KeyError, ValueError, OSError) as error:
            feedback = str(error) if isinstance(error, ValueError) else "Could not process this action."
        self.send_response(303)
        self.send_header("Location", "/?" + urlencode({"message": feedback}))
        self.end_headers()


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 8001), Handler) as server:
        initialize(DEFAULT_DB)
        print("Bakery Order Tracker V2 is running at http://127.0.0.1:8001/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
