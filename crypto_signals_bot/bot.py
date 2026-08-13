#!/usr/bin/env python3
"""Bot de senales de CRIPTO (Kalshi) — banca en paper + modo LIVE opcional.

Hermano del bot de tenis, mismo esqueleto:
  * lleva una banca simulada que arranca en $250,
  * opera las senales del modelo contra el precio NETO de Kalshi,
  * LIQUIDA cada posicion con el precio real del activo al cierre (velas 1-min de
    Coinbase, que aproximan el indice de liquidacion de Kalshi),
  * guarda su historial y genera un dashboard HTML autonomo (dashboard.html).

Novedad frente al de tenis: si KALSHI_LIVE=1 y hay credenciales, ademas coloca la
ORDEN REAL en Kalshi (con topes de riesgo estrictos). Por defecto es 100% paper.

  python crypto_signals_bot/bot.py           # corre y regenera el dashboard
  python crypto_signals_bot/bot.py --open    # ademas lo abre en el navegador

Honesto: los mercados de BTC/ETH diarios de Kalshi son liquidos y afinados; ahi
casi nunca hay edge. La banca en paper es el juez: si no crece, no hay edge real.
"""
import os
import sys
import json
import random
import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import config                                    # noqa: E402
from ml import kalshi_crypto                     # noqa: E402
from ml import prices                            # noqa: E402
from ml import crypto_signals                    # noqa: E402
from ml import execution                         # noqa: E402

DATA = HERE / "data"
POS = DATA / "positions.csv"
EQ = DATA / "equity.csv"
SIG = DATA / "signals.csv"
DASH = HERE / "dashboard.html"

POS_COLS = ["id", "opened_ts", "asset", "series", "market_ticker", "event_ticker",
            "title", "sub_title", "strike_type", "floor", "cap", "side", "action",
            "close_time", "spot_at_open", "p_model", "kalshi_prob", "odds",
            "edge_pct", "kelly_pct", "stake", "count", "live", "order_id",
            "status", "settle_price", "settled_ts", "pnl"]

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------- utilidades ------------------------------------------------------
def _safe_write_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        df.to_csv(f, index=False)
        f.flush()
        os.fsync(f.fileno())


def _load_positions():
    if POS.exists():
        try:
            return pd.read_csv(POS).where(lambda d: pd.notna(d), None).to_dict("records")
        except Exception:
            pass
    return []


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _resolved_yes(strike_type, floor, cap, price):
    """Dado el precio de liquidacion, decide si el contrato YES resolvio a favor."""
    if price is None:
        return None
    f, c = _fnum(floor), _fnum(cap)
    if strike_type == "greater":
        return None if f is None else price >= f
    if strike_type == "less":
        return None if c is None else price <= c
    if strike_type == "between":
        return None if (f is None or c is None) else (f <= price <= c)
    return None


def _hours_to(close_iso, now):
    try:
        c = datetime.fromisoformat(str(close_iso).replace("Z", "+00:00"))
        return (c - now).total_seconds() / 3600.0
    except Exception:
        return None


def _montecarlo(open_pos, equity, n=5000):
    """Distribucion de la banca si las posiciones abiertas resuelven segun el MODELO.
    Es una PROYECCION (asume que la prob del modelo es correcta), no resultado real."""
    if not open_pos:
        return None
    bets = []
    for p in open_pos:
        pm = _fnum(p.get("p_model")) or 0.0
        o = _fnum(p.get("odds")) or 1.0
        st = _fnum(p.get("stake")) or 0.0
        bets.append((pm, st * (o - 1.0), -st))
    ends = []
    for _ in range(n):
        tot = 0.0
        for pm, win_amt, lose_amt in bets:
            tot += win_amt if random.random() < pm else lose_amt
        ends.append(equity + tot)
    ends.sort()

    def pct(q):
        i = min(len(ends) - 1, max(0, int(q * len(ends))))
        return round(ends[i], 2)

    return {
        "expected": round(sum(ends) / len(ends), 2),
        "p5": pct(0.05), "p50": pct(0.50), "p95": pct(0.95),
        "prob_profit": round(100.0 * sum(1 for e in ends if e > equity) / len(ends), 1),
        "n_bets": len(bets),
        "stake_total": round(sum(-b[2] for b in bets), 2),
    }


# ---------- motor -----------------------------------------------------------
def run(open_browser=False):
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    positions = _load_positions()

    # 1) LIQUIDAR posiciones abiertas cuyo mercado ya cerro (precio real al cierre)
    price_cache = {}
    for p in positions:
        if str(p.get("status")) != "open":
            continue
        hleft = _hours_to(p.get("close_time"), now)
        if hleft is None or hleft > 0:
            continue                                    # aun no cierra
        prod = p.get("product") or _product_for(p.get("asset"))
        key = (prod, str(p.get("close_time")))
        if key not in price_cache:
            price_cache[key] = prices.price_at(prod, p.get("close_time"))
        settle = price_cache[key]
        ry = _resolved_yes(p.get("strike_type"), p.get("floor"), p.get("cap"), settle)
        if ry is None:
            # sin precio de liquidacion todavia; anular si ya paso mucho
            if hleft is not None and hleft < -72:       # >3 dias sin dato -> void
                p["status"] = "void"; p["pnl"] = 0.0; p["settled_ts"] = now_iso
            continue
        won = (ry and p.get("side") == "yes") or ((not ry) and p.get("side") == "no")
        p["settle_price"] = round(settle, 6)
        p["settled_ts"] = now_iso
        if won:
            p["status"] = "won"
            p["pnl"] = round((_fnum(p.get("stake")) or 0) * ((_fnum(p.get("odds")) or 1) - 1), 2)
        else:
            p["status"] = "lost"
            p["pnl"] = -round(_fnum(p.get("stake")) or 0, 2)

    # banca = 250 + P&L realizado ; cash = banca - lo que esta en juego
    settled = [p for p in positions if str(p.get("status")) in ("won", "lost", "void")]
    realized = sum(_fnum(p.get("pnl")) or 0 for p in settled)
    equity = round(config.START + realized, 2)
    open_pos = [p for p in positions if str(p.get("status")) == "open"]
    at_risk = round(sum(_fnum(p.get("stake")) or 0 for p in open_pos), 2)
    cash = round(equity - at_risk, 2)

    # perdida del dia (para el corta-fuegos del modo live)
    today = now.date().isoformat()
    daily_loss = -sum(min(0.0, _fnum(p.get("pnl")) or 0) for p in settled
                      if str(p.get("settled_ts", ""))[:10] == today)

    # 2) senales actuales, dimensionadas a la banca de hoy
    try:
        sigs = crypto_signals.signals(min_edge=config.MIN_EDGE, bankroll=equity,
                                      min_volume=config.MIN_VOLUME)
    except Exception as e:
        print(f"  (no se pudieron leer senales: {e})")
        sigs = []

    # 3) cliente LIVE (opcional)
    live_cli, live_why = execution.guarded_client()
    live_note = "ordenes REALES activas" if live_cli else f"paper — {live_why}"
    live_balance = None
    if live_cli:
        try:
            live_balance = live_cli.balance()
        except Exception as e:
            print(f"  (saldo live no disponible: {e})")

    # 4) colocar nuevas posiciones (paper siempre; real si LIVE y pasa el riesgo)
    open_tickers = {p.get("market_ticker") for p in open_pos}
    nid = max([int(_fnum(p.get("id")) or 0) for p in positions], default=0)
    cap_expo = round(equity * config.MAX_EXPOSURE_FRAC, 2)
    exposure, avail, placed, placed_live = at_risk, cash, 0, 0

    for s in sigs:
        tk = s["market_ticker"]
        if tk in open_tickers:
            continue
        stake = _fnum(s["stake"]) or 0.0
        if stake < config.MIN_STAKE:
            continue
        room = min(avail, round(cap_expo - exposure, 2))
        if stake > room:
            stake = round(room, 2)
        if stake < config.MIN_STAKE:
            break                                       # sin cash / sin cupo

        # precio del lado y contratos (para live y para registrar).
        # precio implícito neto = 1/odds ; el ask real del lado ronda ese valor.
        side = s["side"]
        price_side = round(1.0 / (_fnum(s["odds"]) or 2.0), 4)
        count = max(1, int(stake / max(price_side, 0.01)))

        order_id, live_flag = "", 0
        if live_cli:
            price_cents = max(1, min(99, int(round(price_side * 100))))
            ok, why = execution.risk_ok(stake, min(count, config.LIVE_MAX_CONTRACTS),
                                        placed_live + len(open_pos), daily_loss)
            if ok:
                try:
                    o = live_cli.place_order(
                        ticker=tk, side=side, action="buy",
                        count=min(count, config.LIVE_MAX_CONTRACTS),
                        price_cents=price_cents, order_type="limit")
                    order_id = str(o.get("order_id") or o.get("id") or "sent")
                    live_flag, placed_live = 1, placed_live + 1
                except Exception as e:
                    print(f"  orden live rechazada ({tk}): {e}")
            else:
                print(f"  riesgo: no se envia orden real ({tk}): {why}")

        nid += 1
        positions.append({
            "id": nid, "opened_ts": now_iso, "asset": s["asset"], "series": s["series"],
            "market_ticker": tk, "event_ticker": s["event_ticker"],
            "title": s["title"], "sub_title": s["sub_title"], "strike_type": s["strike_type"],
            "floor": s["floor"], "cap": s["cap"], "side": side, "action": "buy",
            "close_time": s["close_time"], "spot_at_open": s["spot"],
            "p_model": s["p_model"], "kalshi_prob": s["kalshi_prob"], "odds": s["odds"],
            "edge_pct": s["edge_pct"], "kelly_pct": s["kelly_pct"], "stake": stake,
            "count": min(count, config.LIVE_MAX_CONTRACTS) if live_flag else count,
            "live": live_flag, "order_id": order_id, "status": "open",
            "settle_price": "", "settled_ts": "", "pnl": "",
        })
        # guardar product para liquidar despues
        positions[-1]["product"] = s.get("product") or _product_for(s["asset"])
        open_tickers.add(tk)
        exposure = round(exposure + stake, 2)
        avail = round(avail - stake, 2)
        placed += 1

    # recomputar tras colocar
    open_pos = [p for p in positions if str(p.get("status")) == "open"]
    at_risk = round(sum(_fnum(p.get("stake")) or 0 for p in open_pos), 2)
    cash = round(equity - at_risk, 2)
    wins = sum(1 for p in settled if p["status"] == "won")
    losses = sum(1 for p in settled if p["status"] == "lost")

    # 5) persistir estado
    _safe_write_csv(pd.DataFrame(positions, columns=POS_COLS + ["product"]), POS)
    _safe_write_csv(pd.DataFrame(sigs, columns=crypto_signals.COLS), SIG)
    eq_hist = []
    if EQ.exists():
        try:
            eq_hist = pd.read_csv(EQ).to_dict("records")
        except Exception:
            eq_hist = []
    eq_hist.append({"ts": now_iso, "equity": equity, "cash": cash, "at_risk": at_risk,
                    "realized_cum": round(realized, 2), "wins": wins, "losses": losses,
                    "n_open": len(open_pos)})
    _safe_write_csv(pd.DataFrame(eq_hist), EQ)

    # 6) proyeccion + dashboard
    mc = _montecarlo(open_pos, equity)
    _write_dashboard(equity, cash, at_risk, realized, wins, losses, open_pos, settled,
                     sigs, eq_hist, mc, now_iso, placed, live_note, live_balance, placed_live)

    roi = (equity / config.START - 1) * 100
    print(f"Banca ${equity:.2f} (inicio ${config.START:.0f}, ROI {roi:+.1f}%) | "
          f"cash ${cash:.2f} | en juego ${at_risk:.2f} ({len(open_pos)} abiertas) | "
          f"record {wins}-{losses} | {placed} nuevas ({placed_live} reales) | "
          f"{len(sigs)} senales | {live_note}")
    print(f"Dashboard: {DASH}")
    if open_browser:
        import webbrowser
        webbrowser.open(DASH.as_uri())
    return 0


def _product_for(asset):
    for _, a, prod in [tuple(x.split(":")) for x in config.SERIES.split(",") if x.count(":") == 2]:
        if a == asset:
            return prod
    return f"{asset}-USD"


# ---------- dashboard HTML autonomo ----------------------------------------
def _sig_view(s):
    g, fp = kalshi_crypto.gross_from_net(s["odds"])
    return {"asset": s["asset"], "sub_title": s["sub_title"], "side": s["side"],
            "odds": s["odds"], "gross": g, "fee_pct": fp, "model": round(s["p_model"] * 100, 1),
            "kalshi": round(s["kalshi_prob"] * 100, 1), "edge": s["edge_pct"],
            "stake": s["stake"], "volume": s.get("volume"), "close": s.get("close_time"),
            "hours": s.get("hours_left"), "sigma": s.get("sigma_ann")}


def _pos_view(p):
    return {"asset": p.get("asset"), "sub_title": p.get("sub_title"), "side": p.get("side"),
            "odds": _fnum(p.get("odds")), "model": round((_fnum(p.get("p_model")) or 0) * 100, 1),
            "edge": p.get("edge_pct"), "stake": _fnum(p.get("stake")), "status": p.get("status"),
            "pnl": _fnum(p.get("pnl")), "live": int(_fnum(p.get("live")) or 0),
            "close": p.get("close_time"), "settle": _fnum(p.get("settle_price"))}


def _write_dashboard(equity, cash, at_risk, realized, wins, losses, open_pos, settled,
                     sigs, eq_hist, mc, now_iso, placed, live_note, live_balance, placed_live):
    payload = {
        "start": config.START, "equity": equity, "cash": cash, "at_risk": at_risk,
        "realized": round(realized, 2), "wins": wins, "losses": losses,
        "roi": round((equity / config.START - 1) * 100, 1),
        "min_edge": round(config.MIN_EDGE * 100), "kelly_frac": config.KELLY_FRAC,
        "tdof": config.STUDENT_T_DOF, "updated": now_iso, "placed": placed,
        "live_note": live_note, "live_balance": live_balance, "placed_live": placed_live,
        "equity_curve": [{"ts": r["ts"], "equity": r["equity"]} for r in eq_hist],
        "open": [_pos_view(p) for p in sorted(open_pos, key=lambda x: -(_fnum(x.get("edge_pct")) or 0))],
        "closed": [_pos_view(p) for p in sorted(settled, key=lambda x: str(x.get("settled_ts", "")), reverse=True)],
        "signals": [_sig_view(s) for s in sigs],
        "mc": mc,
    }
    html = _DASHBOARD_TEMPLATE.replace("/*DATA*/", json.dumps(payload, ensure_ascii=False))
    with open(DASH, "w", encoding="utf-8", newline="") as f:
        f.write(html)
        f.flush()
        os.fsync(f.fileno())


_DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot de Senales — Cripto (Kalshi)</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0b0f16;--card:#141b26;--card2:#1b2432;--line:#243040;--txt:#e6edf5;
  --dim:#8b97a8;--up:#22c55e;--down:#ef4444;--accent:#f7931a;--accent2:#3b82f6;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1120px;margin:0 auto;padding:22px 18px 60px;}
.top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:18px;}
.brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:19px;}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent);}
.fresh{color:var(--dim);font-size:13px;}
.hero{display:grid;grid-template-columns:1.15fr 1fr;gap:16px;margin-bottom:16px;}
@media(max-width:820px){.hero{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;}
.bankroll{font-size:44px;font-weight:800;letter-spacing:-1px;line-height:1;}
.sub{color:var(--dim);font-size:13px;margin-top:6px;}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:700;font-size:13px;}
.up{color:var(--up)}.down{color:var(--down)}
.pill.up{background:rgba(34,197,94,.13)}.pill.down{background:rgba(239,68,68,.13)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:4px 0 18px;}
@media(max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;}
.kpi .l{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.4px;}
.kpi .v{font-size:23px;font-weight:800;margin-top:6px;}
.livebar{border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-bottom:14px;font-size:13.5px;}
.livebar.on{background:rgba(247,147,26,.10);border-color:rgba(247,147,26,.4)}
.livebar.off{background:var(--card2)}
h2{font-size:15px;margin:26px 0 10px;color:var(--txt);}
table{width:100%;border-collapse:collapse;font-size:13.5px;}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.3px;}
tr:last-child td{border-bottom:none}
.tag{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--card2);color:var(--dim);font-weight:700;}
.tag.BTC{color:#f7931a}.tag.ETH{color:#8aa0ff}.tag.SOL{color:#14f195}.tag.XRP{color:#cbd5e1}.tag.DOGE{color:#e2c74e}
.side{font-weight:700;padding:2px 8px;border-radius:6px;font-size:12px;}
.side.yes{background:rgba(34,197,94,.15);color:var(--up)}
.side.no{background:rgba(239,68,68,.15);color:var(--down)}
.badge{font-weight:700;padding:2px 8px;border-radius:6px;font-size:12px;}
.badge.win{background:rgba(34,197,94,.15);color:var(--up)}
.badge.lose{background:rgba(239,68,68,.15);color:var(--down)}
.badge.open{background:rgba(59,130,246,.15);color:#93c5fd}
.badge.void{background:rgba(139,151,168,.15);color:var(--dim)}
.real{font-size:10px;padding:1px 5px;border-radius:5px;background:rgba(247,147,26,.2);color:var(--accent);margin-left:5px}
.mc{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:6px}
@media(max-width:820px){.mc{grid-template-columns:repeat(2,1fr)}}
.mc .v{font-size:20px;font-weight:800;margin-top:4px}
.note{color:var(--dim);font-size:12.5px;line-height:1.5;margin-top:10px;border-left:3px solid var(--line);padding-left:10px;}
.right{text-align:right}.empty{color:var(--dim);padding:16px 4px;font-size:14px;}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:14px;}
canvas{max-height:230px}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand"><span class="dot"></span>Bot de Senales · Cripto <span style="color:var(--dim);font-weight:500">Kalshi</span></div>
    <div class="fresh" id="fresh"></div>
  </div>

  <div class="livebar" id="livebar"></div>

  <div class="hero">
    <div class="card">
      <div class="sub">Banca simulada (paper)</div>
      <div class="bankroll" id="bankroll">—</div>
      <div class="sub" id="roiline"></div>
      <canvas id="curve" style="margin-top:14px"></canvas>
    </div>
    <div class="card">
      <div class="sub" style="margin-bottom:6px">Proyeccion Monte Carlo · posiciones abiertas</div>
      <div id="mcbox"></div>
      <div class="note">Simula 5.000 escenarios asumiendo que la probabilidad del <b>modelo</b> es correcta.
        Es una proyeccion, no resultado real. La banca de arriba solo se mueve con mercados ya liquidados.</div>
    </div>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="sub" style="margin:2px 0 -8px">Cuota = precio de mercado (Kalshi) · Fee = comisión · Neta = cuota tras el fee (a la que opera el bot). YES = a favor del strike · NO = en contra.</div>

  <h2>Senales de hoy <span id="sigcount" style="color:var(--dim);font-weight:500;font-size:13px"></span></h2>
  <div class="scroll"><table id="sigtab">
    <thead><tr><th>Activo</th><th>Mercado</th><th>Cierre (Perú)</th><th>Lado</th><th class="right">Cuota</th><th class="right">Fee</th><th class="right">Neta</th>
      <th class="right">Modelo</th><th class="right">Kalshi</th><th class="right">Ventaja</th><th class="right">Vol σ</th><th class="right">Stake</th></tr></thead>
    <tbody></tbody></table></div>

  <h2>Posiciones abiertas <span id="opencount" style="color:var(--dim);font-weight:500;font-size:13px"></span></h2>
  <div class="scroll"><table id="opentab">
    <thead><tr><th>Activo</th><th>Mercado</th><th>Cierre (Perú)</th><th>Lado</th><th class="right">Neta</th><th class="right">Modelo</th><th class="right">Ventaja</th><th class="right">Stake</th></tr></thead>
    <tbody></tbody></table></div>

  <h2>Historial liquidado <span id="histcount" style="color:var(--dim);font-weight:500;font-size:13px"></span></h2>
  <div class="scroll"><table id="histtab">
    <thead><tr><th>Resultado</th><th>Activo</th><th>Mercado</th><th>Lado</th><th class="right">Liquidó a</th><th class="right">Stake</th><th class="right">P&L</th></tr></thead>
    <tbody></tbody></table></div>
</div>

<script>
const D = /*DATA*/;
const money = v => (v==null?'—':(v<0?'-$':'$')+Math.abs(v).toFixed(2));
const peru = iso => { if(!iso) return '—'; const d=new Date(iso); return isNaN(d)?'—':new Intl.DateTimeFormat('es-PE',{timeZone:'America/Lima',weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(d); };
const el = id => document.getElementById(id);
const px = v => v==null?'—':(v>=100?('$'+(+v).toLocaleString('en-US',{maximumFractionDigits:0})):('$'+(+v).toFixed(4)));

(function(){const t=new Date(D.updated),s=Math.max(0,(Date.now()-t)/60000);
  const ago=s<1?'recien':(s<60?Math.round(s)+' min':Math.round(s/60)+' h');
  el('fresh').textContent='Actualizado hace '+ago+'  ·  edge min '+D.min_edge+'%  ·  ⅛ Kelly · t-dof '+D.tdof;})();

// barra live / paper
(function(){const on=/REALES/.test(D.live_note);const b=el('livebar');b.className='livebar '+(on?'on':'off');
  b.innerHTML=(on?'🟠 <b>Modo LIVE</b> — ':'⚪ <b>Modo paper</b> — ')+D.live_note
    +(D.live_balance!=null?(' · saldo real Kalshi: <b>'+money(D.live_balance)+'</b>'):'')
    +(D.placed_live?(' · '+D.placed_live+' órdenes reales esta corrida'):'');})();

el('bankroll').textContent=money(D.equity);
const up=D.roi>=0;
el('roiline').innerHTML='<span class="pill '+(up?'up':'down')+'">'+(up?'▲ ':'▼ ')+D.roi+'%</span> &nbsp;vs inicio $'+D.start.toFixed(0)
  +' &nbsp;·&nbsp; realizado <span class="'+(D.realized>=0?'up':'down')+'">'+money(D.realized)+'</span>';

const total=D.wins+D.losses, wr=total?(100*D.wins/total).toFixed(0)+'%':'—';
el('kpis').innerHTML=[['En juego',money(D.at_risk),D.open.length+' abiertas'],
  ['Cash libre',money(D.cash),''],['Record',D.wins+'–'+D.losses,'aciertos '+wr],
  ['Senales hoy',String(D.signals.length),D.placed+' operadas']]
  .map(k=>`<div class="kpi"><div class="l">${k[0]}</div><div class="v">${k[1]}</div><div class="sub">${k[2]}</div></div>`).join('');

if(D.mc){const m=D.mc,pu=m.prob_profit>=50;
  el('mcbox').innerHTML=`<div class="mc">
    <div><div class="l" style="color:var(--dim);font-size:12px">Esperada</div><div class="v">${money(m.expected)}</div></div>
    <div><div class="l" style="color:var(--dim);font-size:12px">Prob. ganar</div><div class="v ${pu?'up':'down'}">${m.prob_profit}%</div></div>
    <div><div class="l" style="color:var(--dim);font-size:12px">Malo (P5)</div><div class="v down">${money(m.p5)}</div></div>
    <div><div class="l" style="color:var(--dim);font-size:12px">Bueno (P95)</div><div class="v up">${money(m.p95)}</div></div>
  </div><div class="sub" style="margin-top:8px">${m.n_bets} abiertas · $${m.stake_total.toFixed(2)} en juego</div>`;
}else{el('mcbox').innerHTML='<div class="empty">Sin posiciones abiertas para proyectar.</div>';}

(function(){try{
  if(typeof Chart==='undefined'){el('curve').style.display='none';return;}   // CDN caido: no rompas el resto
  const c=D.equity_curve;
  const labels=c.map(p=>{const d=new Date(p.ts);return (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');});
  const data=c.map(p=>p.equity); if(data.length===1){labels.unshift('inicio');data.unshift(D.start);}
  new Chart(el('curve'),{type:'line',data:{labels,datasets:[{data,borderColor:'#f7931a',backgroundColor:'rgba(247,147,26,.10)',fill:true,tension:.25,pointRadius:data.length>30?0:2,borderWidth:2}]},
    options:{plugins:{legend:{display:false}},scales:{x:{grid:{color:'#1b2432'},ticks:{color:'#8b97a8',maxTicksLimit:8}},y:{grid:{color:'#1b2432'},ticks:{color:'#8b97a8',callback:v=>'$'+v}}}}});
}catch(e){try{el('curve').style.display='none';}catch(_){}}})();

function assetTag(a){return `<span class="tag ${a}">${a}</span>`;}
function sideTag(s){return `<span class="side ${s}">${s.toUpperCase()}</span>`;}
function fill(id,rows,cols){const tb=el(id).querySelector('tbody');
  tb.innerHTML=rows.length?rows.join(''):`<tr><td colspan="${cols}" class="empty">Nada por ahora.</td></tr>`;}

fill('sigtab',D.signals.map(s=>`<tr>
  <td>${assetTag(s.asset)}</td><td>${s.sub_title}</td><td style="color:var(--dim)">${peru(s.close)}</td><td>${sideTag(s.side)}</td>
  <td class="right">${(s.gross!=null?s.gross:s.odds).toFixed(2)}</td><td class="right" style="color:var(--dim)">${s.fee_pct!=null?s.fee_pct+'%':'—'}</td><td class="right">${s.odds.toFixed(2)}</td>
  <td class="right">${s.model}%</td><td class="right">${s.kalshi}%</td><td class="right up">+${s.edge}%</td>
  <td class="right" style="color:var(--dim)">${s.sigma!=null?s.sigma+'%':'—'}</td><td class="right">${money(s.stake)}</td></tr>`),12);
el('sigcount').textContent=D.signals.length?`(${D.signals.length})`:'(sin ventaja ahora — normal en mercados afinados)';

fill('opentab',D.open.map(p=>`<tr>
  <td>${assetTag(p.asset)}</td><td>${p.sub_title}${p.live?'<span class="real">REAL</span>':''}</td><td style="color:var(--dim)">${peru(p.close)}</td><td>${sideTag(p.side)}</td>
  <td class="right">${(p.odds||0).toFixed(2)}</td><td class="right">${p.model}%</td><td class="right up">+${p.edge}%</td><td class="right">${money(p.stake)}</td></tr>`),8);
el('opencount').textContent=D.open.length?`(${D.open.length})`:'';

fill('histtab',D.closed.map(p=>{const st=p.status,pnl=p.pnl==null?0:+p.pnl;
  const badge=st==='won'?'<span class="badge win">GANO</span>':st==='lost'?'<span class="badge lose">PERDIO</span>':'<span class="badge void">ANUL.</span>';
  return `<tr><td>${badge}</td><td>${assetTag(p.asset)}</td><td>${p.sub_title}${p.live?'<span class="real">REAL</span>':''}</td><td>${sideTag(p.side)}</td>
    <td class="right" style="color:var(--dim)">${px(p.settle)}</td><td class="right">${money(p.stake)}</td>
    <td class="right ${pnl>=0?'up':'down'}">${money(pnl)}</td></tr>`;}),7);
el('histcount').textContent=D.closed.length?`(${D.wins}–${D.losses})`:'';
</script>
</body>
</html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Bot de senales de cripto (Kalshi) con banca en paper y modo live opcional.")
    ap.add_argument("--open", action="store_true", help="abre dashboard.html al terminar")
    args = ap.parse_args()
    sys.exit(run(open_browser=args.open))
