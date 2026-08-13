"""Precios de cripto desde fuentes PUBLICAS (Coinbase) — sin API key.

Tres cosas para el bot:
  1) spot(activo)            -> precio ahora (para el modelo)
  2) realized_vol(activo)    -> volatilidad horaria realizada (para el modelo)
  3) price_at(activo, t)     -> precio historico en un instante (para LIQUIDAR)

Kalshi liquida BTC/ETH/etc. con el Real-Time Index de CF Benchmarks (promedio de
60 s antes del cierre). Nosotros aproximamos ese valor con la vela de 1 minuto de
Coinbase en el cierre. No es identico al indice oficial, pero para paper es una
aproximacion honesta (documentado en el README).
"""
import json
import math
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SPOT_URL = "https://api.coinbase.com/v2/prices/{product}/spot"
CANDLE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

_spot_cache = {}     # product -> (ts_epoch, price)
_vol_cache = {}      # product -> (ts_epoch, sigma_hora)
_CACHE_TTL = 120     # s


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def spot(product):
    """Precio spot actual (USD). Cachea 2 min. product = 'BTC-USD', etc."""
    now = time.time()
    c = _spot_cache.get(product)
    if c and now - c[0] < _CACHE_TTL:
        return c[1]
    try:
        d = _get(SPOT_URL.format(product=product))
        px = float(d["data"]["amount"])
        _spot_cache[product] = (now, px)
        return px
    except Exception as e:
        print(f"  spot({product}) error: {e}")
        return c[1] if c else None


def _candles(product, granularity, start=None, end=None):
    """Velas Coinbase: lista de [time, low, high, open, close, volume], reciente primero."""
    params = {"granularity": granularity}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    url = CANDLE_URL.format(product=product) + "?" + urllib.parse.urlencode(params)
    return _get(url)


def realized_vol(product, window_hours=720, inflate=1.0):
    """Sigma por HORA de los log-returns (velas 1h). Cachea 2 min.
    window_hours: cuantas horas de historia usar (~720 = 30 dias)."""
    now = time.time()
    c = _vol_cache.get(product)
    if c and now - c[0] < _CACHE_TTL:
        return c[1] * inflate
    try:
        rows = _candles(product, 3600)          # max ~300 velas por llamada
        closes = [r[4] for r in rows][::-1]     # cronologico
        closes = closes[-int(window_hours):] if window_hours else closes
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 20:
            return None
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        sig = math.sqrt(var)                    # sigma por hora
        _vol_cache[product] = (now, sig)
        return sig * inflate
    except Exception as e:
        print(f"  realized_vol({product}) error: {e}")
        return c[1] * inflate if c else None


def price_at(product, when, tol_min=10):
    """Precio en el instante 'when' (datetime aware) via vela de 1 min de Coinbase.
    Aproxima el valor de liquidacion de Kalshi. None si no hay dato cercano."""
    if when is None:
        return None
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    # una vela de 60 s no puede ser futura
    if when > datetime.now(timezone.utc):
        return None
    try:
        start = (when.timestamp() - tol_min * 60)
        end = (when.timestamp() + tol_min * 60)
        rows = _candles(product, 60,
                        start=datetime.fromtimestamp(start, timezone.utc).isoformat(),
                        end=datetime.fromtimestamp(end, timezone.utc).isoformat())
        if not rows:
            return None
        target = when.timestamp()
        # vela mas cercana al instante objetivo; usa su cierre
        best = min(rows, key=lambda r: abs(r[0] - target))
        if abs(best[0] - target) > tol_min * 60:
            return None
        return float(best[4])
    except Exception as e:
        print(f"  price_at({product}) error: {e}")
        return None


def annualized(sigma_hour):
    """Ayuda de lectura: pasa sigma horaria a % anualizado (para el dashboard)."""
    if not sigma_hour:
        return None
    return round(sigma_hour * math.sqrt(24 * 365) * 100, 1)


if __name__ == "__main__":
    for p in ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"):
        s = spot(p)
        v = realized_vol(p)
        print(f"  {p:9s} spot={s:>12,.4f}  sigma_hora={v*100 if v else 0:.3f}%  "
              f"anualizada={annualized(v)}%")
