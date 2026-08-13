"""Config del bot de senales de CRIPTO (Kalshi).

Los datos de MERCADO son PUBLICOS (Kalshi) y el precio spot/velas tambien
(Coinbase). Por eso el modo PAPER no necesita ninguna key y es seguro tener este
repo publico.

El modo LIVE (ordenes reales) SI necesita credenciales de Kalshi. Esas NUNCA se
escriben aqui: se pasan por variables de entorno / GitHub Secrets. Ver README.
"""
import os


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name, default):
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


# ----- economia del mercado -------------------------------------------------
KALSHI_FEE_RATE = _f("KALSHI_FEE_RATE", 0.07)   # comision Kalshi: fee = tasa*p*(1-p)
KALSHI_ENABLED = os.getenv("KALSHI_ENABLED", "1") == "1"

# ----- banca y dimensionamiento (paper) -------------------------------------
START = _f("CRYPTO_BANKROLL", 250.0)            # banca inicial simulada
MIN_EDGE = _f("CRYPTO_MIN_EDGE", 0.03)          # ventaja minima modelo vs Kalshi para operar
KELLY_FRAC = _f("CRYPTO_KELLY_FRAC", 0.125)     # 1/8 de Kelly (cripto es volatil: prudente)
MAX_STAKE_FRAC = _f("CRYPTO_MAX_STAKE_FRAC", 0.10)   # tope por operacion (10% de la banca)
MAX_EXPOSURE_FRAC = _f("CRYPTO_MAX_EXPOSURE", 0.60)  # tope de banca total en juego a la vez
MIN_STAKE = _f("CRYPTO_MIN_STAKE", 1.0)         # operacion minima en $
EDGE_CAP = _f("CRYPTO_EDGE_CAP", 0.15)          # topa el edge para dimensionar (edges enormes = el modelo se equivoca)

# ----- filtros de calidad de senal ------------------------------------------
MIN_VOLUME = _f("CRYPTO_MIN_VOLUME", 300)      # contratos 24h minimos (mercados finos = precios falsos)
MIN_HOURS = _f("CRYPTO_MIN_HOURS", 0.5)         # no operar a menos de 30 min del cierre (settlement ruidoso)
MAX_HOURS = _f("CRYPTO_MAX_HOURS", 48.0)        # ni mercados a mas de 36 h (el modelo pierde validez)
MAX_ODDS = _f("CRYPTO_MAX_ODDS", 3.0)           # tope de cuota: evita longshots que sangran
# la log-normal falla en las colas -> solo operar donde la prob del modelo es "de mercado"
PROB_FLOOR = _f("CRYPTO_PROB_FLOOR", 0.10)      # ignora si el modelo dice < 10%
PROB_CEIL = _f("CRYPTO_PROB_CEIL", 0.90)        # ignora si el modelo dice > 90%

# ----- el "cerebro": modelo de precios --------------------------------------
VOL_WINDOW_HOURS = _i("CRYPTO_VOL_WINDOW_HOURS", 720)   # velas 1h para la volatilidad realizada (~30 dias)
STUDENT_T_DOF = _f("CRYPTO_T_DOF", 4.0)         # grados de libertad t-Student (colas gordas de cripto; <=0 = normal)
VOL_INFLATE = _f("CRYPTO_VOL_INFLATE", 1.15)    # margen de seguridad sobre la vol estimada
DRIFT = _f("CRYPTO_DRIFT", 0.0)                 # deriva anual asumida (0 = neutral; no predecimos direccion)

# ----- activos habilitados (series de Kalshi) -------------------------------
# formato: "SERIE:ACTIVO:PRODUCTO_COINBASE". Editable por env CRYPTO_SERIES.
_DEFAULT_SERIES = (
    "KXBTCD:BTC:BTC-USD,"     # Bitcoin diario >=/<= (el mas liquido, ~775k/dia)
    "KXETHD:ETH:ETH-USD,"     # Ethereum diario
    "KXBTC:BTC:BTC-USD,"      # Bitcoin rango horario
    "KXETH:ETH:ETH-USD,"      # Ethereum rango horario
    "KXSOLE:SOL:SOL-USD,"     # Solana rango
    "KXXRP:XRP:XRP-USD,"      # XRP rango
    "KXDOGED:DOGE:DOGE-USD"   # Dogecoin diario
)
SERIES = os.getenv("CRYPTO_SERIES", _DEFAULT_SERIES)

# ----- modo LIVE (ordenes reales) — OPT-IN, apagado por defecto --------------
LIVE = os.getenv("KALSHI_LIVE", "0") == "1"     # 1 = coloca ordenes REALES en Kalshi
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")        # UUID del API key
KALSHI_PRIVATE_KEY = os.getenv("KALSHI_PRIVATE_KEY", "")      # clave privada RSA (PEM, multilinea)
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")  # o ruta a un .pem
KALSHI_API_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")

# topes de riesgo del modo LIVE (ademas de los de banca de arriba)
LIVE_MAX_CONTRACTS = _i("KALSHI_LIVE_MAX_CONTRACTS", 5)       # contratos maximos por orden
LIVE_MAX_ORDER_USD = _f("KALSHI_LIVE_MAX_ORDER_USD", 10.0)    # desembolso maximo por orden ($)
LIVE_MAX_DAILY_LOSS = _f("KALSHI_LIVE_MAX_DAILY_LOSS", 25.0)  # corta el dia si pierde esto ($)
LIVE_MAX_OPEN_ORDERS = _i("KALSHI_LIVE_MAX_OPEN_ORDERS", 8)   # posiciones reales simultaneas maximas
