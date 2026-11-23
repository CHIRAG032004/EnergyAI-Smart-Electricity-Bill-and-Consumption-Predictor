#!/usr/bin/env python3
"""
EnergyAI backend  (Python standard library only, SQLite database, works offline)
Keep these 3 files in one folder:  server.py  energy_predictor.py  index.html
Run:  python server.py   ->  http://127.0.0.1:8000
API:  GET /  /api/data  /api/report   POST /api/upload  /api/reset
"""
import csv, io, json, os, re, statistics, threading, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import energy_predictor as ep

HERE = os.path.dirname(os.path.abspath(__file__))
MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FORMATS = [("%Y-%m-%d", 1), ("%d-%m-%Y", 1), ("%d/%m/%Y", 1), ("%Y/%m/%d", 1), ("%d-%b-%Y", 1),
           ("%Y-%m", 0), ("%Y/%m", 0), ("%b %Y", 0), ("%B %Y", 0), ("%b-%Y", 0), ("%m/%Y", 0), ("%b-%y", 0)]


def mlabel(m): return f"{MN[int(m[5:]) - 1]} {m[:4]}"
def grade(s): return "Excellent" if s >= 85 else "Good" if s >= 70 else "Needs attention" if s >= 50 else "High risk"


# ---------------------------------------------------------------- parsing
def num(s):
    s = re.sub(r"[^\d.\-]", "", s or "")
    try: return float(s)
    except ValueError: return None


def pdate(s):
    s = (s or "").strip()
    for f, dd in FORMATS:
        try: return datetime.strptime(s, f).date(), dd
        except ValueError: pass
    return None, 0


def col(h, names):
    for i, x in enumerate(h):
        if any(n in x for n in names): return i
    return None


def parse(text):
    text = text.lstrip("\ufeff").strip()
    if not text: raise ValueError("The file is empty.")
    delim = max([",", ";", "\t"], key=text.splitlines()[0].count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(x.strip() for x in r)]
    h = [x.strip().lower() for x in rows[0]]
    if pdate(rows[0][0])[0]:                      # no header row
        di, ui, bi, body = 0, 1, (2 if len(h) > 2 else None), rows
    else:
        di = col(h, ("date", "month", "period", "day"))
        ui = col(h, ("unit", "kwh", "consum", "usage", "reading"))
        bi = col(h, ("bill", "amount", "cost", "charge"))
        body = rows[1:]
    if di is None or ui is None or di == ui:
        raise ValueError("Could not find a date/month column and a units (kWh) column. Example header: Month,Units_kWh,Bill_Amount")
    out, dcount = [], 0
    for r in body[:20000]:
        if len(r) <= max(di, ui): continue
        d, dd = pdate(r[di])
        u = num(r[ui])
        if d is None or u is None or u < 0: continue
        b = num(r[bi]) if bi is not None and bi < len(r) else None
        out.append((d, u, b))
        dcount += dd
    if not out:
        raise ValueError("No valid rows found. Use dates like 2026-01 or 2026-01-31 and numeric units.")
    return ("daily" if dcount > len(out) / 2 else "monthly"), out


def store(kind, out):
    c = ep.db()
    c.execute("CREATE TABLE IF NOT EXISTS bills(month TEXT PRIMARY KEY, bill REAL)")
    agg, bl = {}, {}
    for d, u, b in out:
        k = d.isoformat() if kind == "daily" else f"{d.year}-{d.month:02d}"
        agg[k] = agg.get(k, 0) + u
        if b is not None and kind == "monthly": bl[k] = bl.get(k, 0) + b
    if kind == "monthly" and len(agg) < 6:
        raise ValueError("Need at least 6 months of data for a forecast (12+ recommended).")
    if kind == "daily" and len(agg) < 14:
        raise ValueError("Need at least 14 days of readings for anomaly detection.")
    tbl = "monthly" if kind == "monthly" else "daily"
    c.execute(f"DELETE FROM {tbl}")
    c.executemany(f"INSERT INTO {tbl} VALUES(?,?)", [(k, round(v, 2)) for k, v in agg.items()])
    if kind == "monthly":
        c.execute("DELETE FROM bills")
        c.executemany("INSERT INTO bills VALUES(?,?)", list(bl.items()))
    c.commit()
    c.close()


# ---------------------------------------------------------------- analysis
def report_monthly():
    rows, fc, info = ep.forecast(6)
    c = ep.db()
    c.execute("CREATE TABLE IF NOT EXISTS bills(month TEXT PRIMARY KEY, bill REAL)")
    bills = dict(c.execute("SELECT month,bill FROM bills").fetchall())
    c.close()
    ms, us, n = [r[0] for r in rows], [r[1] for r in rows], len(rows)
    src = "actual" if len([m for m in ms if m in bills]) >= n / 2 else "estimated"
    tb = sum(bills.get(m, ep.bill(u)) if src == "actual" else ep.bill(u) for m, u in rows)
    tu = sum(us)
    avg = tu / n
    pk, lw = max(rows, key=lambda r: r[1]), min(rows, key=lambda r: r[1])
    F, T, pen = [], [], 0
    F.append(f"Total consumption over {n} months is {tu:,.0f} kWh, costing about ₹{tb:,.0f} ({src} bills).")
    F.append(f"Peak month was {mlabel(pk[0])} ({pk[1]:.0f} kWh) and the lowest was {mlabel(lw[0])} ({lw[1]:.0f} kWh).")
    yoy = None
    if n >= 24:
        a, b = sum(us[-12:]), sum(us[-24:-12])
        yoy = round((a - b) / b * 100, 1)
        F.append(f"The last 12 months are {abs(yoy)}% {'higher' if yoy > 0 else 'lower'} than the 12 months before.")
        if yoy > 10: pen += 15; T.append("Usage is growing year over year: review new appliances and long-running loads.")
        for i in range(n - 12, n):
            pct = (us[i] - us[i - 12]) / us[i - 12] * 100
            if abs(pct) > 25:
                pen += 5
                F.append(f"{mlabel(ms[i])} was {abs(pct):.0f}% {'above' if pct > 0 else 'below'} the same month last year. Check for a faulty appliance, guests or a meter issue.")
    summer = [u for m, u in rows if int(m[5:]) in (4, 5, 6)]
    if summer and statistics.mean(summer) / avg > 1.2:
        r = statistics.mean(summer) / avg
        pen += 10
        F.append(f"Summer months (Apr-Jun) use {(r - 1) * 100:.0f}% more than your monthly average, so cooling is the main driver.")
        T.append("Set the AC to 24-26 °C and run a fan with it; each degree higher saves roughly 6% of cooling energy.")
    if info["slope"] > 1.5:
        pen += 10
        F.append(f"Your baseline is rising by about {info['slope']} kWh every month.")
        T.append("Audit always-on loads: standby devices, an old refrigerator, water-heater timers.")
    last = us[-12:]
    hi = sum(max(u - 300, 0) for u in last) / sum(last) * 100
    if hi > 10:
        sv = sum(ep.bill(u) - ep.bill(u * .9) for u in last)
        pen += 10
        F.append(f"{hi:.0f}% of your last-12-month units fall in the top ₹7/unit slab.")
        T.append(f"Cutting usage by only 10% would save about ₹{sv:,.0f} a year, because the top slab is the most expensive.")
    k = 1.0
    if src == "actual":
        gap = statistics.mean((bills[m] - ep.bill(u)) / ep.bill(u) * 100 for m, u in rows if m in bills)
        if abs(gap) > 8:
            k = 1 + gap / 100
            F.append(f"Your actual bills run {abs(gap):.0f}% {'above' if gap > 0 else 'below'} the model tariff, so forecast bills were adjusted by the same gap.")
    if not T: T.append("Your pattern looks healthy. Keep adding monthly readings so the forecast stays accurate.")
    T.append("Open the Savings Simulator tab to test appliance-level changes before you make them.")
    score = max(0, 100 - pen)
    return dict(kind="monthly", period=f"{mlabel(ms[0])} - {mlabel(ms[-1])}", months=n, total_units=round(tu),
                total_bill=round(tb), bill_source=src, avg_units=round(avg, 1), rate=round(tb / tu, 2),
                yoy=yoy, slope=info["slope"], mape=info["mape"], score=score, grade=grade(score),
                fc=[dict(x, bill=round(x["bill"] * k, 2)) for x in fc],
                history=[dict(month=m, units=u) for m, u in rows[-24:]], findings=F, tips=T)


def report_daily():
    an, med = ep.daily()
    n = len(an)
    us = [a["units"] for a in an]
    tu, avg = sum(us), sum(us) / n
    fl = [a for a in an if a["flag"]]
    spikes = [a for a in fl if a["kind"] == "spike"]
    drops = [a for a in fl if a["kind"] == "drop"]
    pk = max(an, key=lambda a: a["units"])
    wk = [a["units"] for a in an if datetime.strptime(a["day"], "%Y-%m-%d").weekday() >= 5]
    wd = [a["units"] for a in an if datetime.strptime(a["day"], "%Y-%m-%d").weekday() < 5]
    pu = avg * 30
    pb = ep.bill(pu)
    F = [f"{n} days analysed ({an[0]['day']} to {an[-1]['day']}): average {avg:.1f} kWh/day, typical day {med} kWh.",
         f"Highest day was {pk['day']} with {pk['units']} kWh.",
         f"At this pace a 30-day month uses about {pu:.0f} kWh and costs about ₹{pb:,.0f}."]
    T, pen = [], 12 * len(spikes) + 5 * len(drops)
    if spikes:
        extra = sum(a["units"] - med for a in spikes)
        F.append(f"{len(spikes)} spike day(s) used {extra:.0f} kWh more than normal (about ₹{extra * pb / pu:,.0f} extra).")
        T.append("Check what ran on spike days: AC left on, geyser, a stuck pump or a faulty appliance.")
    if drops:
        F.append(f"{len(drops)} unusually low day(s): possible power cut, travel or meter issue.")
    if wk and wd and statistics.mean(wk) > statistics.mean(wd) * 1.15:
        d = (statistics.mean(wk) / statistics.mean(wd) - 1) * 100
        F.append(f"Weekends use {d:.0f}% more than weekdays.")
        T.append("Shift heavy loads (washing, ironing, pumping) to off-peak hours and avoid running them together.")
    if not T: T.append("No unusual days found. Your daily pattern is stable.")
    T.append("Upload 12+ months of monthly bills to also get a 6-month forecast.")
    score = max(0, min(100, 100 - pen))
    return dict(kind="daily", period=f"{an[0]['day']} to {an[-1]['day']}", days=n, total_units=round(tu), avg=round(avg, 1),
                proj_units=round(pu), proj_bill=round(pb), anoms=len(fl), score=score, grade=grade(score),
                series=[dict(day=a["day"], units=a["units"], flag=a["flag"]) for a in an], findings=F, tips=T)


def data_dump():
    c = ep.db()
    m = {r[0]: r[1] for r in c.execute("SELECT month,units FROM monthly")}
    d = {r[0]: r[1] for r in c.execute("SELECT day,units FROM daily")}
    c.close()
    return dict(monthly=m, daily=d, ap=ep.appliances())


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200, ct="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self): self.send({})

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.send(f.read(), 200, "text/html; charset=utf-8")
            except OSError:
                return self.send({"error": "index.html not found next to server.py"}, 404)
        if p == "/api/data": return self.send(data_dump())
        if p == "/api/report": return self.send(report_monthly())
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > 2_000_000: return self.send({"error": "File too large (max 2 MB)."}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/upload":
                kind, out = parse(str(body.get("content", "")))
                store(kind, out)
                rep = report_monthly() if kind == "monthly" else report_daily()
                rep["filename"] = os.path.basename(str(body.get("name", "upload.csv")))[:80]
                return self.send(dict(ok=1, kind=kind, report=rep))
            if self.path == "/api/reset":
                ep.seed(True)
                c = ep.db()
                c.execute("DROP TABLE IF EXISTS bills")
                c.commit()
                c.close()
                return self.send({"ok": 1})
            self.send({"error": "not found"}, 404)
        except ValueError as e:
            self.send({"error": str(e)}, 400)
        except Exception:
            self.send({"error": "Could not analyse this file."}, 500)


if __name__ == "__main__":
    ep.seed()
    port = 8000
    while True:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    print(f"EnergyAI backend running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
