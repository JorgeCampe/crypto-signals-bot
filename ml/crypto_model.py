"""El "cerebro" del bot: probabilidad INDEPENDIENTE de cada mercado de cripto.

En el bot de tenis el cerebro era Elo+ML. Aqui es un modelo de PRECIO: dado el
spot S, la volatilidad realizada y el tiempo T hasta el cierre, calcula la
probabilidad de que el precio final caiga por encima / por debajo / dentro del
strike del mercado. Esa probabilidad se compara luego contra el precio de Kalshi
(crypto_signals.py) para buscar ventaja.

Modelo:  ln(S_T / S_0) = (mu - 0.5*sigma^2) * T  +  sigma * sqrt(T) * X
  - unidades en HORAS (sigma es horaria, T en horas)
  - mu = deriva (0 por defecto: no predecimos direccion, solo dispersion)
  - X estandarizada. Normal, o t-Student con colas GORDAS (cripto salta): con
    pocos grados de libertad las colas pesan mas, lo que evita el "edge falso"
    que una log-normal pura ve en strikes lejanos.

Sin dependencias: la CDF normal usa math.erf; la CDF t-Student usa la funcion
beta incompleta regularizada (fraccion continua de Lentz, Numerical Recipes).
"""
import math

_HOURS_PER_YEAR = 24 * 365


# ---------- funciones de distribucion (sin scipy) ---------------------------
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _betacf(a, b, x, itmax=200, eps=3e-12):
    """Fraccion continua para la beta incompleta (Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    """Beta incompleta regularizada I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_cdf(t, dof):
    """CDF de la t-Student con 'dof' grados de libertad."""
    if dof <= 0:
        return _norm_cdf(t)
    x = dof / (dof + t * t)
    ib = 0.5 * _betai(dof / 2.0, 0.5, x)
    return 1.0 - ib if t > 0 else ib


def _tail_above(d, dof):
    """P(X >= d) para X estandarizada (varianza 1). Normal si dof<=0, si no t-Student
    reescalada a varianza unitaria."""
    if dof and dof > 2:
        scale = math.sqrt(dof / (dof - 2.0))      # t_dof tiene var dof/(dof-2); estandarizamos
        return 1.0 - _t_cdf(d * scale, dof)
    return 1.0 - _norm_cdf(d)


# ---------- probabilidades de precio ----------------------------------------
def prob_above(S, K, sigma_hour, hours, dof=0.0, drift_annual=0.0):
    """P(precio final S_T >= K)."""
    if S is None or K is None or sigma_hour is None or S <= 0 or K <= 0:
        return None
    if hours is None or hours <= 0:
        return 1.0 if S >= K else 0.0
    vol = sigma_hour * math.sqrt(hours)
    if vol <= 0:
        return 1.0 if S >= K else 0.0
    mu_h = drift_annual / _HOURS_PER_YEAR
    drift_term = (mu_h - 0.5 * sigma_hour ** 2) * hours
    d = (math.log(K / S) - drift_term) / vol
    return max(0.0, min(1.0, _tail_above(d, dof)))


def prob_below(S, K, sigma_hour, hours, dof=0.0, drift_annual=0.0):
    """P(precio final S_T <= K)."""
    pa = prob_above(S, K, sigma_hour, hours, dof, drift_annual)
    return None if pa is None else 1.0 - pa


def prob_between(S, lo, hi, sigma_hour, hours, dof=0.0, drift_annual=0.0):
    """P(lo <= S_T <= hi) = P(>=lo) - P(>=hi)."""
    pl = prob_above(S, lo, sigma_hour, hours, dof, drift_annual)
    ph = prob_above(S, hi, sigma_hour, hours, dof, drift_annual)
    if pl is None or ph is None:
        return None
    return max(0.0, pl - ph)


def prob_yes(market, spot, sigma_hour, dof=None, drift_annual=None):
    """Probabilidad de que el lado YES del mercado resuelva a favor.
    market: dict de kalshi_crypto.markets(). Usa hours_left del propio mercado."""
    import config
    dof = config.STUDENT_T_DOF if dof is None else dof
    drift_annual = config.DRIFT if drift_annual is None else drift_annual
    st = market.get("strike_type")
    h = market.get("hours_left")
    if st == "greater":
        return prob_above(spot, market.get("floor"), sigma_hour, h, dof, drift_annual)
    if st == "less":
        return prob_below(spot, market.get("cap"), sigma_hour, h, dof, drift_annual)
    if st == "between":
        return prob_between(spot, market.get("floor"), market.get("cap"),
                            sigma_hour, h, dof, drift_annual)
    return None


if __name__ == "__main__":
    # sanity: P(>=spot) ~ 0.5, colas monotonas, t-Student mas gorda que normal
    S, sig, h = 63000.0, 0.002, 18.0
    for dof in (0.0, 4.0):
        tag = "normal" if dof == 0 else f"t(dof={dof:.0f})"
        print(f"[{tag}]  P(>=63000)={prob_above(S,63000,sig,h,dof):.3f}  "
              f"P(>=66000)={prob_above(S,66000,sig,h,dof):.3f}  "
              f"P(>=70000)={prob_above(S,70000,sig,h,dof):.4f}  "
              f"P(58000..66000)={prob_between(S,58000,66000,sig,h,dof):.3f}")
