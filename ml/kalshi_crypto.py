"""Kalshi (mercado de predicciones regulado, CFTC) — lectura de mercados de CRIPTO.

Los datos de mercado son PUBLICOS: no hace falta auth ni firma RSA para leer
precios (eso solo se necesita para OPERAR por API -> ml/execution.py).

Cada serie de cripto (KXBTCD, KXETHD, KXBTC, ...) es una escalera de mercados
binarios sobre el precio de un activo a una hora concreta. Cada mercado trae:
  strike_type = greater  -> "el precio final es >= floor_strike"
  strike_type = less     -> "el precio final es <= cap_strike"
  strike_type = between  -> "floor_strike <= precio final <= cap_strike"
Precios en dolares 0-1 (= probabilidad) en los campos *_dollars. Cuota decimal
= 1 / precio. Kalshi cobra comision, asi que damos tambien la cuota NETA.
"""
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config   # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _series_map():
    """Parsea config.SERIES ('SERIE:ACTIVO:PRODUCTO,...') -> [(serie, activo, product)]."""
    out = []
    for chunk in str(config.SERIES).split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) == 3 and all(parts):
            out.append(tuple(parts))
    return out


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def net_odds(price):
    """price = precio en dolares (0-1). Cuota decimal NETA (comision descontada).
    fee de Kalshi = tasa * p * (1-p); costo = p + fee; cuota = 1/costo."""
    p = _f(price)
    if p is None or p <= 0 or p >= 1:
        return None
    fee = config.KALSHI_FEE_RATE * p * (1 - p)
    cost = p + fee
    return round(1.0 / cost, 4) if cost > 0 else None


def gross_from_net(net):
    """Invierte net_odds: de la cuota NETA saca precio de mercado y fee (% del desembolso)."""
    o = _f(net)
    if o is None or o <= 1:
        return None, None
    fr = config.KALSHI_FEE_RATE
    cost = 1.0 / o
    disc = (1 + fr) ** 2 - 4 * fr * cost
    if fr <= 0 or disc < 0:
        return round(o, 3), 0.0
    p = ((1 + fr) - disc ** 0.5) / (2 * fr)
    if not (0 < p < 1):
        return round(o, 3), 0.0
    return round(1.0 / p, 3), round(fr * p * (1 - p) * 100.0, 2)


def _hours_left(close_iso, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        c = datetime.fromisoformat(str(close_iso).replace("Z", "+00:00"))
        return (c - now).total_seconds() / 3600.0
    except Exception:
        return None


def markets(min_volume=None, series=None):
    """Lista de mercados de cripto abiertos con precio y strikes normalizados.

    Cada item:
      {market_ticker, event_ticker, series, asset, product, title, sub_title,
       strike_type, floor, cap, yes_bid, yes_ask, no_bid, no_ask, mid_yes,
       odds_yes, odds_no, volume, open_interest, close_time, hours_left}
    """
    if not config.KALSHI_ENABLED:
        return []
    mv = config.MIN_VOLUME if min_volume is None else min_volume
    smap = series or _series_map()
    now = datetime.now(timezone.utc)
    out = []
    for serie, asset, product in smap:
        cursor = ""
        for _ in range(25):
            url = f"{BASE}/markets?series_ticker={serie}&status=open&limit=200"
            if cursor:
                url += f"&cursor={urllib.parse.quote(cursor)}"
            try:
                d = _get(url)
            except Exception as e:
                print(f"  Kalshi {serie}: {e}")
                break
            for m in d.get("markets", []):
                yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
                nb, na = _f(m.get("no_bid_dollars")), _f(m.get("no_ask_dollars"))
                vol = _f(m.get("volume_24h_fp")) or 0.0
                oi = _f(m.get("open_interest_fp")) or 0.0
                st = m.get("strike_type")
                floor, cap = _f(m.get("floor_strike")), _f(m.get("cap_strike"))
                # requiere un libro con dos lados y strike valido
                if st not in ("greater", "less", "between"):
                    continue
                if st == "between" and (floor is None or cap is None):
                    continue
                if st == "greater" and floor is None:
                    continue
                if st == "less" and cap is None:
                    continue
                if vol < mv:
                    continue
                hl = _hours_left(m.get("close_time"), now)
                mid_yes = None
                if ya is not None and yb is not None and ya > 0 and yb > 0:
                    mid_yes = round((ya + yb) / 2, 4)
                out.append({
                    "market_ticker": m.get("ticker"),
                    "event_ticker": m.get("event_ticker"),
                    "series": serie, "asset": asset, "product": product,
                    "title": m.get("title", ""), "sub_title": m.get("yes_sub_title", ""),
                    "strike_type": st, "floor": floor, "cap": cap,
                    "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na,
                    "mid_yes": mid_yes,
                    "odds_yes": net_odds(ya),     # comprar YES paga a esta cuota neta
                    "odds_no": net_odds(na),      # comprar NO paga a esta cuota neta
                    "volume": round(vol, 2), "open_interest": round(oi, 2),
                    "close_time": m.get("close_time"), "hours_left": hl,
                })
            cursor = d.get("cursor") or ""
            if not cursor:
                break
    return out


if __name__ == "__main__":
    ms = markets()
    print(f"Kalshi cripto: {len(ms)} mercados con volumen >= {config.MIN_VOLUME:.0f}")
    by_asset = {}
    for m in ms:
        by_asset.setdefault(m["asset"], 0)
        by_asset[m["asset"]] += 1
    print("por activo:", by_asset)
    for m in sorted(ms, key=lambda x: -(x["volume"] or 0))[:15]:
        k = m["floor"] if m["strike_type"] == "greater" else m["cap"] if m["strike_type"] == "less" else f"{m['floor']}-{m['cap']}"
        print(f"  [{m['asset']}] {str(m['sub_title'])[:22]:22s} {m['strike_type']:7s} "
              f"yes_ask={m['yes_ask']}  vol={m['volume']:.0f}  {m['hours_left']:.1f}h")
