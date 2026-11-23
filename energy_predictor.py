#!/usr/bin/env python3
"""
Smart Electricity Bill & Consumption Predictor  (offline demo, single file)
Run:  python energy_predictor.py      -> opens http://127.0.0.1:8000
Needs only Python 3.8+ (standard library). Data lives in energy_demo.db (SQLite).
"""
import sqlite3, json, random, statistics, os, threading, webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "energy_demo.db")
CO2 = 0.82  # kg per kWh


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def addm(y, m, n):
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def seed(reset=False):
    c = db()
    if reset:
        c.executescript("DROP TABLE IF EXISTS monthly;DROP TABLE IF EXISTS daily;DROP TABLE IF EXISTS appliances;")
    c.executescript("""CREATE TABLE IF NOT EXISTS monthly(month TEXT PRIMARY KEY, units REAL);
    CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, units REAL);
    CREATE TABLE IF NOT EXISTS appliances(id INTEGER PRIMARY KEY, name TEXT, icon TEXT, watts REAL, qty INTEGER, hours REAL);""")
    if c.execute("SELECT COUNT(*) FROM monthly").fetchone()[0] == 0:
        rnd = random.Random(7)
        t = date.today()
        season = [-70, -60, -10, 70, 140, 120, 50, 30, 40, 10, -45, -70]
        for k in range(36):
            y, m = addm(t.year, t.month, k - 36)
            u = 300 + 1.6 * k + season[m - 1] + rnd.gauss(0, 12)
            c.execute("INSERT INTO monthly VALUES(?,?)", (f"{y}-{m:02d}", round(u, 1)))
        for i in range(90, 0, -1):
            d = t - timedelta(days=i)
            u = 15 + (1.5 if d.weekday() >= 5 else 0) + rnd.gauss(0, 0.8)
            if i == 70: u *= 1.9
            if i == 43: u *= 0.2
            if i == 19: u *= 2.3
            c.execute("INSERT INTO daily VALUES(?,?)", (d.isoformat(), round(u, 1)))
        for a in [("Air Conditioner", "❄️", 1400, 1, 5), ("Refrigerator", "🧊", 70, 1, 24),
                  ("Ceiling Fans", "🌀", 75, 4, 10), ("LED Lights", "💡", 10, 8, 6),
                  ("Water Heater", "🚿", 2000, 1, 1), ("Television", "📺", 100, 1, 4),
                  ("Washing Machine", "🧺", 500, 1, 1), ("Laptop / PC", "💻", 80, 1, 6),
                  ("Water Pump", "🚰", 750, 1, 1)]:
            c.execute("INSERT INTO appliances(name,icon,watts,qty,hours) VALUES(?,?,?,?,?)", a)
    c.commit()
    c.close()


def bill(u):
    """Slab tariff: 0-100 @3.5, 101-300 @5.0, 300+ @7.0, +Rs50 fixed, +5% duty."""
    e, left = 0.0, u
    for cap, rate in [(100, 3.5), (200, 5.0), (1e9, 7.0)]:
        t = min(left, cap)
        e += t * rate
        left -= t
        if left <= 0: break
    return round((e + 50) * 1.05, 2)


def forecast(n=6):
    c = db()
    rows = [(r["month"], r["units"]) for r in c.execute("SELECT * FROM monthly ORDER BY month")]
    c.close()
    N = len(rows)
    ys = [r[1] for r in rows]
    xs = list(range(N))
    mx, my = sum(xs) / N, sum(ys) / N
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    seas = {}
    for (mo, _), x, y in zip(rows, xs, ys):
        seas.setdefault(int(mo[5:]), []).append(y - (a + b * x))
    seas = {k: sum(v) / len(v) for k, v in seas.items()}
    fit = [a + b * x + seas[int(mo[5:])] for x, (mo, _) in zip(xs, rows)]
    sd = statistics.pstdev([y - f for y, f in zip(ys, fit)])
    mape = sum(abs(f - y) / y for f, y in zip(fit, ys)) / N * 100
    y0, m0 = int(rows[-1][0][:4]), int(rows[-1][0][5:])
    out = []
    for i in range(1, n + 1):
        yy, mm = addm(y0, m0, i)
        u = max(a + b * (N - 1 + i) + seas.get(mm, 0), 0)
        out.append(dict(month=f"{yy}-{mm:02d}", units=round(u, 1), lo=round(max(u - 1.28 * sd, 0), 1),
                        hi=round(u + 1.28 * sd, 1), bill=bill(u)))
    return rows, out, dict(slope=round(b, 2), mape=round(mape, 1), sd=round(sd, 1))


def daily():
    c = db()
    rows = [(r["day"], r["units"]) for r in c.execute("SELECT * FROM daily ORDER BY day")]
    c.close()
    v = [r[1] for r in rows]
    med = statistics.median(v)
    mad = statistics.median([abs(x - med) for x in v]) or 1
    out = []
    for d, u in rows:
        z = (u - med) / (1.4826 * mad)
        out.append(dict(day=d, units=round(u, 1), z=round(z, 1), flag=abs(z) > 3.5, kind="spike" if z > 0 else "drop"))
    return out, round(med, 1)


def appliances():
    c = db()
    r = [dict(x) for x in c.execute("SELECT * FROM appliances")]
    c.close()
    for a in r:
        a["kwh"] = round(a["watts"] * a["qty"] * a["hours"] * 30 / 1000, 1)
    return r


def simulate(h):
    ap = appliances()
    F = forecast(1)[1][0]
    before = after = 0
    per = []
    for a in ap:
        hr = float(h.get(str(a["id"]), a["hours"]))
        k1 = a["watts"] * a["qty"] * hr * 30 / 1000
        before += a["kwh"]
        after += k1
        per.append(dict(id=a["id"], after=round(k1, 1)))
    saved = before - after
    nu = max(F["units"] - saved, 0)
    sb = F["bill"] - bill(nu)
    return dict(base_units=F["units"], base_bill=F["bill"], new_units=round(nu, 1), new_bill=bill(nu),
                saved_units=round(saved, 1), saved_bill=round(sb, 2), year=round(sb * 12),
                co2=round(saved * CO2 * 12), per=per)


def summary():
    rows, fc, info = forecast()
    an, med = daily()
    return dict(history=[dict(month=m, units=u, bill=bill(u)) for m, u in rows], forecast=fc, info=info,
                anomalies=sum(a["flag"] for a in an), median=med,
                co2=round(rows[-1][1] * CO2))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200, ct="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/": return self.send(PAGE.encode(), 200, "text/html; charset=utf-8")
        if p == "/api/summary": return self.send(summary())
        if p == "/api/daily":
            a, m = daily()
            return self.send(dict(days=a, median=m))
        if p == "/api/appliances": return self.send(appliances())
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        c = db()
        try:
            if p == "/api/simulate": return self.send(simulate(d.get("hours", {})))
            if p == "/api/apply":
                for k, v in d.get("hours", {}).items():
                    c.execute("UPDATE appliances SET hours=? WHERE id=?", (float(v), int(k)))
            elif p == "/api/month":
                u = float(d["units"])
                if not (0 < u < 100000) or len(d["month"]) != 7: return self.send({"error": "bad input"}, 400)
                c.execute("INSERT OR REPLACE INTO monthly VALUES(?,?)", (d["month"], u))
            elif p == "/api/spike":
                last = c.execute("SELECT MAX(day) FROM daily").fetchone()[0]
                nd = date.fromisoformat(last) + timedelta(days=1)
                med = daily()[1]
                c.execute("INSERT INTO daily VALUES(?,?)", (nd.isoformat(), round(med * random.choice([2.2, 2.6]), 1)))
            elif p == "/api/bill": return self.send(dict(bill=bill(float(d["units"]))))
            elif p == "/api/reset":
                c.close()
                seed(True)
                return self.send({"ok": 1})
            c.commit()
            self.send({"ok": 1})
        finally:
            try: c.close()
            except Exception: pass


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EnergyAI – Bill & Consumption Predictor</title>
<style>
:root{--bg:#f6f8fc;--card:#fff;--ink:#1e293b;--mute:#64748b;--line:#e8edf5;--blue:#2563eb;--amber:#f59e0b;--green:#10b981;--red:#ef4444}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);min-height:100vh;overflow-x:hidden}
.blob{position:fixed;border-radius:50%;filter:blur(70px);opacity:.45;z-index:0;animation:float 14s ease-in-out infinite}
.b1{width:340px;height:340px;background:#fde68a;top:-80px;left:-60px}.b2{width:380px;height:380px;background:#bfdbfe;bottom:-100px;right:-80px;animation-delay:-6s}
@keyframes float{50%{transform:translate(40px,30px) scale(1.1)}}
header,main{position:relative;z-index:1;max-width:1100px;margin:auto;padding:0 18px}
header{padding-top:22px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:22px}.logo i{font-style:normal;background:linear-gradient(135deg,#fbbf24,#f59e0b);width:38px;height:38px;border-radius:11px;display:grid;place-items:center;animation:pulse 2.4s infinite}
@keyframes pulse{50%{box-shadow:0 0 0 10px rgba(245,158,11,.15)}}
.logo small{display:block;font-weight:500;color:var(--mute);font-size:12px}
nav{display:flex;flex-wrap:wrap;gap:8px}
nav button{border:1px solid var(--line);background:#fff;padding:10px 16px;border-radius:999px;font-weight:600;color:var(--mute);cursor:pointer;transition:.25s}
nav button:hover{transform:translateY(-2px);box-shadow:0 6px 14px rgba(15,23,42,.08)}
nav button.on{background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#fff;border-color:transparent;box-shadow:0 6px 16px rgba(245,158,11,.4)}
main{padding-bottom:50px}.tab{animation:up .5s both}@keyframes up{from{opacity:0;transform:translateY(16px)}}
h2{margin:26px 0 4px}.sub{color:var(--mute);margin:0 0 16px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 4px 18px rgba(15,23,42,.04);transition:.25s;animation:up .6s both;margin-bottom:14px}
.grid .card{margin:0}.card:hover{transform:translateY(-3px);box-shadow:0 12px 28px rgba(15,23,42,.09)}
.grid .card:nth-child(2){animation-delay:.08s}.grid .card:nth-child(3){animation-delay:.16s}.grid .card:nth-child(4){animation-delay:.24s}
.kpi small{color:var(--mute);font-weight:600}.kpi b{display:block;font-size:30px;margin:6px 0 2px}.kpi span{font-size:12px;color:var(--mute)}
h3{margin:0 0 10px}canvas{width:100%;height:290px;display:block}
.lg{display:flex;gap:16px;font-size:12px;color:var(--mute);margin-top:8px;flex-wrap:wrap}.lg i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-2px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);font-size:14px}th{color:var(--mute);font-size:12px;text-transform:uppercase}
tr{animation:up .4s both}
.btn{border:0;background:var(--blue);color:#fff;padding:10px 18px;border-radius:12px;font-weight:600;cursor:pointer;transition:.2s}.btn:hover{transform:translateY(-2px);filter:brightness(1.08)}.btn.g{background:var(--green)}.btn.r{background:var(--red)}.btn.o{background:#fff;color:var(--ink);border:1px solid var(--line)}
input[type=number],input[type=month]{padding:10px;border:1px solid var(--line);border-radius:10px;font-size:14px;width:100%}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}.row>div{flex:1;min-width:140px}label{font-size:12px;color:var(--mute);font-weight:600}
.chip{display:inline-block;padding:5px 11px;border-radius:999px;background:#fef3c7;color:#92400e;font-weight:600;font-size:12px;margin:0 6px 6px 0}.chip.b{background:#dbeafe;color:#1e40af}.chip.g{background:#d1fae5;color:#065f46}.chip.r{background:#fee2e2;color:#991b1b}
.sim{display:grid;grid-template-columns:1.6fr 1fr;gap:14px}@media(max-width:800px){.sim{grid-template-columns:1fr}}
.ap{display:grid;grid-template-columns:34px 1fr 90px;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}.ap em{font-style:normal;font-size:24px}
.ap input{width:100%;accent-color:var(--amber)}.ap small{color:var(--mute);display:block}.ap b{text-align:right;font-size:13px}
.big{font-size:34px;font-weight:800;color:var(--green)}.stick{position:sticky;top:12px;align-self:start}
.bar{height:8px;background:var(--line);border-radius:9px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:linear-gradient(90deg,#fbbf24,#f59e0b);width:0;transition:width 1s}
#toast{position:fixed;bottom:22px;left:50%;transform:translate(-50%,90px);background:#1e293b;color:#fff;padding:12px 20px;border-radius:12px;transition:.4s;z-index:9}#toast.show{transform:translate(-50%,0)}
.badge{padding:3px 9px;border-radius:8px;font-size:12px;font-weight:700}.sp{background:#fee2e2;color:#b91c1c}.dr{background:#dbeafe;color:#1d4ed8}
</style></head><body>
<div class="blob b1"></div><div class="blob b2"></div>
<header><div class="logo"><i>⚡</i><div>EnergyAI<small>Smart Electricity Bill & Consumption Predictor · offline demo</small></div></div>
<nav id="nav"><button data-t="dash" class="on">📊 Dashboard</button><button data-t="fc">🔮 Forecast</button><button data-t="sim">🧪 Savings Simulator</button><button data-t="an">🚨 Anomalies</button><button data-t="data">🗄️ Data</button></nav></header>
<main id="app"></main><div id="toast"></div>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const api=(p,b)=>fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{}).then(r=>r.json());
const inr=v=>'₹'+Math.round(v).toLocaleString('en-IN');
const mon=s=>{const[y,m]=s.split('-');return['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m-1]+" '"+y.slice(2)};
const toast=t=>{const e=$('#toast');e.textContent=t;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),2200)};
function anim(d,ms=1000){const t0=performance.now();(function f(t){const p=Math.min((t-t0)/ms,1);d(1-Math.pow(1-p,3));if(p<1)requestAnimationFrame(f)})(t0)}
function count(){$$('[data-c]').forEach(el=>{const to=+el.dataset.c,r=el.dataset.t=='r';anim(p=>el.textContent=r?inr(to*p):Math.round(to*p*10)/10+' kWh'.slice(0,0)+(el.dataset.t=='u'?' kWh':''),900)})}
function setup(cv){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;const x=cv.getContext('2d');x.scale(d,d);return[x,r.width,r.height]}
function grid(x,W,H,pl,pr,pt,pb,mx){x.font='11px sans-serif';x.fillStyle='#94a3b8';x.strokeStyle='#e8edf5';x.lineWidth=1;x.textAlign='right';for(let i=0;i<=4;i++){const y=H-pb-(H-pt-pb)*i/4;x.beginPath();x.moveTo(pl,y);x.lineTo(W-pr,y);x.stroke();x.fillText(Math.round(mx*i/4),pl-6,y+4)}}
function line(cv,L,Hs,F,lo,hi){const[x,W,H]=setup(cv),pl=46,pr=14,pt=14,pb=28,n=L.length,h=Hs.length,mx=Math.max(...Hs,...hi)*1.1;
const X=i=>pl+i*(W-pl-pr)/(n-1),Y=v=>H-pb-v/mx*(H-pt-pb);
anim(p=>{x.clearRect(0,0,W,H);grid(x,W,H,pl,pr,pt,pb,mx);x.textAlign='center';x.fillStyle='#94a3b8';for(let i=0;i<n;i+=Math.ceil(n/8))x.fillText(mon(L[i]),X(i),H-8);
x.save();x.beginPath();x.rect(0,0,pl+(W-pl)*p,H);x.clip();
x.beginPath();x.moveTo(X(h-1),Y(Hs[h-1]));F.forEach((_,i)=>x.lineTo(X(h+i),Y(hi[i])));for(let i=F.length-1;i>=0;i--)x.lineTo(X(h+i),Y(lo[i]));x.closePath();x.fillStyle='rgba(245,158,11,.16)';x.fill();
const g=x.createLinearGradient(0,pt,0,H-pb);g.addColorStop(0,'rgba(37,99,235,.28)');g.addColorStop(1,'rgba(37,99,235,0)');
const path=()=>{x.beginPath();Hs.forEach((v,i)=>i?x.lineTo(X(i),Y(v)):x.moveTo(X(i),Y(v)))};
path();x.lineTo(X(h-1),H-pb);x.lineTo(X(0),H-pb);x.fillStyle=g;x.fill();path();x.strokeStyle='#2563eb';x.lineWidth=2.5;x.stroke();
x.beginPath();x.setLineDash([6,5]);x.moveTo(X(h-1),Y(Hs[h-1]));F.forEach((v,i)=>x.lineTo(X(h+i),Y(v)));x.strokeStyle='#f59e0b';x.stroke();x.setLineDash([]);
F.forEach((v,i)=>{x.beginPath();x.arc(X(h+i),Y(v),4.5,0,7);x.fillStyle='#f59e0b';x.fill()});x.restore()})}
function bars(cv,L,V,fl,col,lab){const[x,W,H]=setup(cv),pl=40,pr=10,pt=14,pb=26,n=V.length,mx=Math.max(...V)*1.12,bw=(W-pl-pr)/n;
anim(p=>{x.clearRect(0,0,W,H);grid(x,W,H,pl,pr,pt,pb,mx);V.forEach((v,i)=>{const h=v/mx*(H-pt-pb)*p;x.fillStyle=fl&&fl[i]?'#ef4444':col;x.beginPath();x.roundRect(pl+i*bw+bw*.15,H-pb-h,bw*.7,h,3);x.fill()});
x.fillStyle='#94a3b8';x.textAlign='center';for(let i=0;i<n;i+=Math.ceil(n/8))x.fillText(lab(L[i]),pl+i*bw+bw/2,H-8)})}
const kpi=(t,v,s,ty)=>`<div class="card kpi"><small>${t}</small><b data-c="${v}" data-t="${ty}">0</b><span>${s}</span></div>`;
let cur='dash',S;
const T={
async dash(){S=await api('/api/summary');const h=S.history,l=h[h.length-1],f=S.forecast[0],d=((f.units-l.units)/l.units*100).toFixed(1);
$('#app').innerHTML=`<div class="tab"><h2>Home Energy Overview</h2><p class="sub">AI-powered view of your consumption, bills and next month's prediction.</p>
<div class="grid">${kpi('Last month ('+mon(l.month)+')',l.units,'units consumed','u')}${kpi('Last bill',l.bill,'slab-tariff estimate','r')}${kpi('Next month forecast',f.bill,f.units+' kWh expected','r')}${kpi('Anomalies found',S.anomalies,'in last 90 days','n')}</div>
<div class="card" style="margin-top:14px"><h3>Consumption history & 6-month forecast</h3><canvas id="c1"></canvas><div class="lg"><span><i style="background:#2563eb"></i>Actual (kWh)</span><span><i style="background:#f59e0b"></i>Forecast</span><span><i style="background:#fde7b0"></i>80% confidence band</span></div></div>
<div class="card"><h3>💡 Insight</h3><span class="chip ${d>0?'r':'g'}">${d>0?'▲':'▼'} ${Math.abs(d)}% vs last month</span><span class="chip b">Trend +${S.info.slope} kWh/month</span><span class="chip g">≈ ${S.co2} kg CO₂ last month</span>
<p>Your usage is expected to ${d>0?'rise':'fall'} to <b>${f.units} kWh</b> (≈ <b>${inr(f.bill)}</b>) in ${mon(f.month)}. Open the <b>Savings Simulator</b> to see which appliances can bring it down.</p></div></div>`;
count();const hh=h.slice(-24);line($('#c1'),[...hh.map(x=>x.month),...S.forecast.map(x=>x.month)],hh.map(x=>x.units),S.forecast.map(x=>x.units),S.forecast.map(x=>x.lo),S.forecast.map(x=>x.hi))},
async fc(){S=await api('/api/summary');const mxb=Math.max(...S.forecast.map(x=>x.bill));
$('#app').innerHTML=`<div class="tab"><h2>Bill Forecast</h2><p class="sub">Python model: linear trend + monthly seasonality, trained on ${S.history.length} months stored in SQLite.</p>
<div class="card"><span class="chip b">Model error (MAPE): ${S.info.mape}%</span><span class="chip">Std. deviation ±${S.info.sd} kWh</span><span class="chip g">Trend +${S.info.slope} kWh/mo</span><canvas id="c2"></canvas></div>
<div class="card"><h3>Next 6 months</h3><table><tr><th>Month</th><th>Units</th><th>Range</th><th>Bill</th><th></th></tr>${S.forecast.map((x,i)=>`<tr style="animation-delay:${i*.07}s"><td>${mon(x.month)}</td><td>${x.units} kWh</td><td>${x.lo}–${x.hi}</td><td><b>${inr(x.bill)}</b></td><td style="width:30%"><div class="bar"><i style="width:${x.bill/mxb*100}%"></i></div></td></tr>`).join('')}</table></div>
<div class="card"><h3>🧮 Quick bill calculator</h3><div class="row"><div><label>Units (kWh)</label><input type="number" id="cu" value="250"></div><button class="btn" id="cb">Calculate</button></div><p id="cr" class="big" style="color:var(--blue)"></p>
<p class="sub">Tariff: first 100 units ₹3.5 · next 200 ₹5.0 · above 300 ₹7.0 · ₹50 fixed + 5% duty (editable in Python: bill()).</p></div></div>`;
bars($('#c2'),S.forecast.map(x=>x.month),S.forecast.map(x=>x.units),null,'#f59e0b',mon);
$('#cb').onclick=async()=>{const r=await api('/api/bill',{units:$('#cu').value});$('#cr').textContent=inr(r.bill)};$('#cb').click()},
async sim(){const ap=await api('/api/appliances');window.AP=ap;
$('#app').innerHTML=`<div class="tab"><h2>Appliance Savings Simulator</h2><p class="sub">Drag daily-hours sliders and see the impact on your next bill instantly.</p><div class="sim"><div class="card"><div class="row" style="margin-bottom:6px"><button class="btn g" id="eco">🌱 Eco preset (-25%)</button><button class="btn o" id="rs">↺ Reset</button></div>
${ap.map(a=>`<div class="ap"><em>${a.icon}</em><div><b style="font-size:14px">${a.name}</b> <small>${a.qty}× ${a.watts} W · <span id="h${a.id}">${a.hours}</span> h/day</small><input type="range" min="0" max="24" step="0.5" value="${a.hours}" id="s${a.id}" data-id="${a.id}"></div><b id="k${a.id}">${a.kwh} kWh</b></div>`).join('')}</div>
<div class="card stick"><h3>Estimated impact</h3><p class="sub">Next-month bill</p><div class="big" id="nb">–</div><p id="sv" class="sub"></p><span class="chip g" id="ys"></span><span class="chip b" id="co"></span><br><br><button class="btn" id="ap">Save as my new routine</button></div></div></div>`;
const get=()=>Object.fromEntries($$('input[type=range]').map(e=>[e.dataset.id,+e.value]));let tm;
const run=async()=>{const r=await api('/api/simulate',{hours:get()});$('#nb').textContent=inr(r.new_bill);$('#sv').innerHTML=`from <s>${inr(r.base_bill)}</s> · saves <b>${inr(r.saved_bill)}</b>/month (${r.saved_units} kWh)`;$('#ys').textContent='≈ '+inr(r.year)+' saved / year';$('#co').textContent='🌍 '+r.co2+' kg CO₂ / year';r.per.forEach(p=>$('#k'+p.id).textContent=p.after+' kWh')};
$$('input[type=range]').forEach(e=>e.oninput=()=>{$('#h'+e.dataset.id).textContent=e.value;clearTimeout(tm);tm=setTimeout(run,120)});
$('#eco').onclick=()=>{ap.forEach(a=>{if(a.name!='Refrigerator'){const e=$('#s'+a.id);e.value=Math.round(a.hours*.75*2)/2;$('#h'+a.id).textContent=e.value}});run()};
$('#rs').onclick=()=>{ap.forEach(a=>{$('#s'+a.id).value=a.hours;$('#h'+a.id).textContent=a.hours});run()};
$('#ap').onclick=async()=>{await api('/api/apply',{hours:get()});toast('Routine saved to database ✅');T.sim()};run()},
async an(){const r=await api('/api/daily'),d=r.days,fl=d.filter(x=>x.flag);
$('#app').innerHTML=`<div class="tab"><h2>Unusual Consumption Detection</h2><p class="sub">Robust z-score (median + MAD) on 90 days of daily readings. Typical day ≈ ${r.median} kWh.</p>
<div class="card"><div class="row" style="margin-bottom:10px"><button class="btn r" id="sp">⚡ Inject a spike</button><button class="btn o" id="rn">🔍 Re-run detection</button></div><canvas id="c3"></canvas><div class="lg"><span><i style="background:#60a5fa"></i>Normal</span><span><i style="background:#ef4444"></i>Anomaly</span></div></div>
<div class="card"><h3>${fl.length?'⚠️ '+fl.length+' unusual days':'✅ No anomalies'}</h3><table>${fl.map((x,i)=>`<tr style="animation-delay:${i*.08}s"><td>${x.day}</td><td>${x.units} kWh</td><td><span class="badge ${x.kind=='spike'?'sp':'dr'}">${x.kind=='spike'?'SPIKE':'DROP'}</span></td><td>z = ${x.z}</td></tr>`).join('')}</table><p class="sub">Spikes may mean a faulty appliance or leakage; drops may be an outage or meter issue.</p></div></div>`;
bars($('#c3'),d.map(x=>x.day),d.map(x=>x.units),d.map(x=>x.flag),'#60a5fa',s=>s.slice(5));
$('#sp').onclick=async()=>{await api('/api/spike',{});toast('Spike injected');T.an()};$('#rn').onclick=()=>{toast('Detection re-run');T.an()}},
async data(){S=await api('/api/summary');const h=[...S.history].reverse().slice(0,12),now=new Date().toISOString().slice(0,7);
$('#app').innerHTML=`<div class="tab"><h2>Offline Database</h2><p class="sub">Everything is stored in a local SQLite file (energy_demo.db) – no internet needed.</p>
<div class="card"><h3>➕ Add monthly reading</h3><div class="row"><div><label>Month</label><input type="month" id="dm" value="${now}"></div><div><label>Units (kWh)</label><input type="number" id="du" placeholder="e.g. 320"></div><button class="btn" id="da">Save</button></div></div>
<div class="card"><h3>Recent months <span class="chip">${S.history.length} records</span></h3><table><tr><th>Month</th><th>Units</th><th>Bill</th></tr>${h.map((x,i)=>`<tr style="animation-delay:${i*.04}s"><td>${mon(x.month)}</td><td>${x.units}</td><td>${inr(x.bill)}</td></tr>`).join('')}</table><br><button class="btn r" id="dr">Reset demo data</button></div></div>`;
$('#da').onclick=async()=>{const r=await api('/api/month',{month:$('#dm').value,units:$('#du').value});if(r.error)return toast('Enter valid month and units');toast('Saved – forecast updated ✅');T.data()};
$('#dr').onclick=async()=>{await api('/api/reset',{});toast('Demo data restored');T.data()}}};
$('#nav').onclick=e=>{const b=e.target.closest('button');if(!b)return;$$('#nav button').forEach(x=>x.classList.toggle('on',x==b));cur=b.dataset.t;T[cur]()};
let rt;onresize=()=>{clearTimeout(rt);rt=setTimeout(()=>{if(['dash','fc','an'].includes(cur))T[cur]()},300)};
T.dash();
</script></body></html>"""


if __name__ == "__main__":
    seed()
    port = 8000
    while True:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            port += 1
    url = f"http://127.0.0.1:{port}"
    print(f"EnergyAI demo running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
