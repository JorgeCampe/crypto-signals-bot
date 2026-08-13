"""Ejecucion de ordenes REALES en Kalshi (modo LIVE). OPT-IN, apagado por defecto.

El bot de tenis solo hace paper. Este modulo agrega la parte que faltaba: colocar
ordenes de verdad via la API autenticada de Kalshi. Se usa SOLO si config.LIVE=1
y hay credenciales. Firma cada request con RSA-PSS (SHA-256) como pide Kalshi:

  mensaje  = str(timestamp_ms) + METHOD + path        (path SIN query string)
  headers  = KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE

La libreria 'cryptography' solo se importa aqui y solo en modo LIVE, para que el
modo paper no necesite ninguna dependencia extra.
"""
import json
import sys
import time
import base64
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config   # noqa: E402


class KalshiError(Exception):
    pass


def _load_private_key():
    """Carga la clave privada RSA desde env (PEM) o archivo. Lazy import de cryptography."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    pem = None
    if config.KALSHI_PRIVATE_KEY.strip():
        pem = config.KALSHI_PRIVATE_KEY.encode()
    elif config.KALSHI_PRIVATE_KEY_PATH.strip():
        pem = Path(config.KALSHI_PRIVATE_KEY_PATH).read_bytes()
    if not pem:
        raise KalshiError("Falta KALSHI_PRIVATE_KEY o KALSHI_PRIVATE_KEY_PATH")
    return load_pem_private_key(pem, password=None)


class KalshiClient:
    """Cliente autenticado minimo para leer saldo/posiciones y colocar ordenes."""

    def __init__(self):
        self.base = config.KALSHI_API_BASE.rstrip("/")
        self.key_id = config.KALSHI_API_KEY_ID
        if not self.key_id:
            raise KalshiError("Falta KALSHI_API_KEY_ID")
        self._pk = _load_private_key()

    # ---- firma ----
    def _sign(self, ts_ms, method, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        msg = f"{ts_ms}{method}{path}".encode()
        sig = self._pk.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _headers(self, method, path):
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method, rel_path, body=None):
        # path para firmar = todo lo despues del host, SIN query
        from urllib.parse import urlsplit
        full = f"{self.base}{rel_path}"
        sign_path = urlsplit(full).path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(full, data=data, method=method,
                                     headers=self._headers(method, sign_path))
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise KalshiError(f"{e.code} {e.reason}: {e.read().decode()[:300]}")

    # ---- lectura ----
    def balance(self):
        """Saldo en dolares (Kalshi devuelve centavos)."""
        d = self._request("GET", "/portfolio/balance")
        cents = d.get("balance")
        return round(cents / 100.0, 2) if cents is not None else None

    def positions(self):
        return self._request("GET", "/portfolio/positions")

    def resting_orders(self):
        try:
            d = self._request("GET", "/portfolio/orders?status=resting")
            return d.get("orders", [])
        except KalshiError:
            return []

    # ---- escritura ----
    def place_order(self, ticker, side, action, count, price_cents=None,
                    order_type="limit", client_order_id=None):
        """Coloca una orden. price_cents en 1..99 para limit (precio del lado 'side').
        Devuelve el dict de la orden de Kalshi."""
        body = {
            "ticker": ticker, "action": action, "side": side,
            "count": int(count), "type": order_type,
            "client_order_id": client_order_id or f"cryptobot-{int(time.time()*1000)}",
        }
        if order_type == "limit":
            if price_cents is None:
                raise KalshiError("limit order requiere price_cents")
            body["yes_price" if side == "yes" else "no_price"] = int(price_cents)
        d = self._request("POST", "/portfolio/orders", body)
        return d.get("order", d)


# ---------- capa de riesgo (envuelve al cliente) ----------------------------
def guarded_client():
    """Devuelve (cliente, None) si LIVE y credenciales OK; (None, motivo) si no."""
    if not config.LIVE:
        return None, "modo paper (KALSHI_LIVE!=1)"
    try:
        return KalshiClient(), None
    except Exception as e:
        return None, f"no se pudo iniciar cliente live: {e}"


def risk_ok(stake_usd, count, open_orders, daily_loss):
    """Chequeos de seguridad antes de mandar una orden real. (ok, motivo)."""
    if count > config.LIVE_MAX_CONTRACTS:
        return False, f"count {count} > LIVE_MAX_CONTRACTS {config.LIVE_MAX_CONTRACTS}"
    if stake_usd > config.LIVE_MAX_ORDER_USD:
        return False, f"stake ${stake_usd:.2f} > LIVE_MAX_ORDER_USD ${config.LIVE_MAX_ORDER_USD:.2f}"
    if open_orders >= config.LIVE_MAX_OPEN_ORDERS:
        return False, f"posiciones abiertas {open_orders} >= tope {config.LIVE_MAX_OPEN_ORDERS}"
    if daily_loss >= config.LIVE_MAX_DAILY_LOSS:
        return False, f"perdida del dia ${daily_loss:.2f} >= tope ${config.LIVE_MAX_DAILY_LOSS:.2f}"
    return True, ""


if __name__ == "__main__":
    cli, why = guarded_client()
    if cli is None:
        print(f"LIVE no activo: {why}")
    else:
        print("Saldo Kalshi:", cli.balance())
        print("Ordenes en reposo:", len(cli.resting_orders()))
