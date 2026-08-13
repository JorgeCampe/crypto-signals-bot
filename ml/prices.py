"""Precios de cripto desde fuentes PUBLICAS, con RESPALDO automatico.

Problema real detectado: Coinbase suele BLOQUEAR las IPs de GitHub Actions (error
403/401), y entonces el bot se queda sin precios y no genera ninguna senal. Para
que eso no vuelva a pasar, este modulo intenta VARIAS fuentes en orden y usa la
primera que responda:  Coinbase  ->  Kraken.

Interfaz publica (sin cambios para el resto del bot):
  spot(product)            -> precio ahora (para el modelo)
  realized_vol(product...) -> volatilidad horaria realizada (para el modelo)
  price_at(product, t)     -> precio historico en un instante (para LIQUIDAR)
  annualized(sigma_hour)   -> % anualizado (para el dashboard)

'product' llega como el ticker de Coinbase ('BTC-USD', 'ETH-USD', ...). De ahi se
saca el activo (BTC) y se traduce al simbolo de cada fuente.
"""
import os
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

# fuentes a intentar, en orden. Editable con CRYPTO_PRICE_SOURCES="kraken,coinbase".
SOURCES = [s.strip().lower() for s in
           os.getenv("CRYPTO_PRICE_SOURCES", "coinbase,kraken").split(",") if s.strip()]

# activo -> par de Kraken (Kraken usa XBT por BTC y XDG por DOGE)
_KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD",
           "DOGE": "XDGUSD", "LTC": "LTCUSD", "LINK": "LINKUSD", "XLM": "XLMUSD",
           "AVAX": "AVAXUSD", "DOT": "DOTUSD", "ADA": "ADAUSD"}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_spot_cache = {}     # product -> (ts, price)
_vol_cache = {}      # product -> (ts, sigma_h)
_down = set()        # fuentes ya reportadas como caidas (para no spamear el log)
_CACHE_TTL = 120


def _asset(product):
    return str(product).split("-")[0].upper()


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------- fuente: COINBASE ------------------------------------------
def _cb_spot(asset):
    d = _get(f"https://api.coinbase.com/v2/prices/{asset}-USD/spot")
    return float(d["data"]["amount"])


def _cb_candles(asset, gran_sec, start=None, end=None):
    url = f"https://api.exchange.coinbase.com/products/{asset}-USD/candles?granularity={gran_sec}"
    if start:
        url += f"&start={start}&end={end}"
    rows = _get(url)                      # [time, low, high, open, close, vol], reciente primero
    return [(int(r[0]), float(r[4])) for r in rows][::-1]   # -> cronologico (ts, close)


# ---------------- fuente: KRAKEN --------------------------------------------
def _kr_pair(asset):
    return _KRAKEN.get(asset)


def _kr_spot(asset):
    pair = _kr_pair(asset)
    if not pair:
        raise ValueError(f"Kraken no cubre {asset}")
    d = _get(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
    res = d["result"]
    key = next(iter(res))
    return float(res[key]["c"][0])


def _kr_candles(asset, gran_sec, start=None, end=None):
    pair = _kr_pair(asset)
    if not pair:
        raise ValueError(f"Kraken no cubre {asset}")
    interval = max(1, int(gran_sec // 60))       # Kraken usa MINUTOS
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    if start:
        url += f"&since={int(float(start) if str(start).replace('.','').isdigit() else _to_epoch(start)) - 60}"
    d = _get(url)
    res = d["result"]
    key = next(k for k in res if k != "last")
    return [(int(x[0]), float(x[4])) for x in res[key]]      # (ts, close) cronologico


def _to_epoch(s):
    if isinstance(s, (int, float)):
        return float(s)
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_SPOT_FN = {"coinbase": _cb_spot, "kraken": _kr_spot}
_CANDLE_FN = {"coinbase": _cb_candles, "kraken": _kr_candles}


def _first_ok(kind, fns, asset, *args):
    """Prueba las fuentes en orden; devuelve (valor, fuente) del primero que responda."""
    last = None
    for src in SOURCES:
        fn = fns.get(src)
        if fn is None:
            continue
        try:
            val = fn(asset, *args)
            if src in _down:
                print(f"  precios: {src} volvio a responder")
                _down.discard(src)
            return val, src
        except Exception as e:
            last = e
            if src not in _down:
                print(f"  precios: {src} no disponible para {kind} ({asset}): {e}  -> probando respaldo")
                _down.add(src)
    if last:
        print(f"  precios: ninguna fuente respondio {kind} para {asset}: {last}")
    return None, None


# ---------------- interfaz publica ------------------------------------------
def spot(product):
    now = time.time()
    c = _spot_cache.get(product)
    if c and now - c[0] < _CACHE_TTL:
        return c[1]
    val, _src = _first_ok("spot", _SPOT_FN, _asset(product))
    if val is not None:
        _spot_cache[product] = (now, val)
        return val
    return c[1] if c else None


def _candles(product, gran_sec, start=None, end=None):
    rows, _src = _first_ok("velas", _CANDLE_FN, _asset(product), gran_sec, start, end)
    return rows or []


def realized_vol(product, window_hours=720, inflate=1.0):
    now = time.time()
    c = _vol_cache.get(product)
    if c and now - c[0] < _CACHE_TTL:
        return c[1] * inflate
    rows = _candles(product, 3600)              # (ts, close) cronologico
    closes = [r[1] for r in rows]
    closes = closes[-int(window_hours):] if window_hours else closes
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 20:
        return c[1] * inflate if c else None
    mu = sum(rets) / len(rets)
    sig = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    _vol_cache[product] = (now, sig)
    return sig * inflate


def price_at(product, when, tol_min=15):
    """Precio en el instante 'when' (para liquidar), via velas de 1 min con respaldo."""
    if when is None:
        return None
    target = _to_epoch(when)
    if target > time.time():
        return None
    start = datetime.fromtimestamp(target - tol_min * 60, timezone.utc).isoformat()
    end = datetime.fromtimestamp(target + tol_min * 60, timezone.utc).isoformat()
    rows = _candles(product, 60, start=start, end=end)
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(r[0] - target))
    if abs(best[0] - target) > tol_min * 60:
        return None
    return best[1]


def annualized(sigma_hour):
    if not sigma_hour:
        return None
    return round(sigma_hour * math.sqrt(24 * 365) * 100, 1)


if __name__ == "__main__":
    print("fuentes en orden:", SOURCES)
    for p in ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"):
        s = spot(p)
        v = realized_vol(p, window_hours=720, inflate=1.15)
        print(f"  {p:9s} spot={s if s else 0:>12.4f}  sigma_h={v*100 if v else 0:.3f}%  "
              f"anual={annualized(v)}%")
