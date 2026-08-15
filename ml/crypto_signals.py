"""Senales de CRIPTO para Kalshi: modelo propio vs precio de mercado.

Analogo a kalshi_signals.py del bot de tenis. Para cada mercado de cripto:
  1) el modelo (crypto_model) estima P(YES) de forma INDEPENDIENTE (spot + vol),
  2) se compara contra el precio NETO de Kalshi de comprar YES y de comprar NO,
  3) donde hay ventaja suficiente se emite una senal con su tamano (Kelly frac).

Honesto: los mercados de BTC/ETH diarios de Kalshi son MUY liquidos y afinados;
ahi casi nunca habra edge real. La apuesta es a que los mercados de cripto menos
mirados (rangos intradia, altcoins) esten a veces mal preciados. La banca en paper
es el juez.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config                          # noqa: E402
from ml import kalshi_crypto           # noqa: E402
from ml import prices                  # noqa: E402
from ml import crypto_model            # noqa: E402

COLS = ["ts", "asset", "series", "market_ticker", "title", "sub_title", "strike_type",
        "floor", "cap", "side", "action", "close_time", "hours_left", "spot",
        "sigma_ann", "p_model", "kalshi_prob", "odds", "edge_pct", "kelly_pct",
        "stake", "volume", "event_ticker"]


def _vol_for(product):
    return prices.realized_vol(product, window_hours=config.VOL_WINDOW_HOURS,
                               inflate=config.VOL_INFLATE)


def signals(min_edge=None, bankroll=None, min_volume=None):
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    bankroll = config.START if bankroll is None else bankroll
    ms = kalshi_crypto.markets(min_volume=min_volume)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    spot_cache, vol_cache = {}, {}
    out = []
    for m in ms:
        hl = m.get("hours_left")
        if hl is None or hl < config.MIN_HOURS or hl > config.MAX_HOURS:
            continue
        prod = m["product"]
        if prod not in spot_cache:
            spot_cache[prod] = prices.spot(prod)
            vol_cache[prod] = _vol_for(prod)
        S, sig = spot_cache[prod], vol_cache[prod]
        if S is None or sig is None:
            continue

        p_yes = crypto_model.prob_yes(m, S, sig)
        if p_yes is None:
            continue
        # la log-normal no es fiable en las colas -> solo operar cerca del dinero
        if p_yes < config.PROB_FLOOR or p_yes > config.PROB_CEIL:
            continue

        # evaluar comprar YES y comprar NO; quedarse con el mejor edge
        opts = []
        if m.get("odds_yes"):
            o = m["odds_yes"]
            opts.append(("yes", p_yes, o, p_yes * o - 1, m.get("yes_ask")))
        if m.get("odds_no"):
            o = m["odds_no"]
            p_no = 1 - p_yes
            opts.append(("no", p_no, o, p_no * o - 1, m.get("no_ask")))
        if not opts:
            continue
        side, p_side, o, edge, ask = max(opts, key=lambda x: x[3])
        if edge < min_edge or o > config.MAX_ODDS or o <= 1:
            continue
        f = min(p_side * o - 1, config.EDGE_CAP) / (o - 1)          # Kelly con edge TOPADO (edges enormes = modelo equivocado)
        if f <= 0:
            continue
        stake = round(bankroll * min(f * config.KELLY_FRAC, config.MAX_STAKE_FRAC), 2)

        out.append({
            "ts": now_iso, "asset": m["asset"], "series": m["series"],
            "market_ticker": m["market_ticker"], "title": m["title"],
            "sub_title": m["sub_title"], "strike_type": m["strike_type"],
            "floor": m["floor"], "cap": m["cap"],
            "side": side, "action": "buy", "close_time": m["close_time"],
            "hours_left": round(hl, 2), "spot": round(S, 4),
            "sigma_ann": prices.annualized(sig),
            "p_model": round(p_side, 3), "kalshi_prob": round(1 / o, 3),
            "odds": round(o, 3), "edge_pct": round(edge * 100, 1),
            "kelly_pct": round(f * 100, 1), "stake": stake,
            "volume": m["volume"], "event_ticker": m["event_ticker"],
        })
    out.sort(key=lambda x: x["edge_pct"], reverse=True)
    return out


def main():
    sigs = signals()
    print(f"Senales cripto (Kalshi): {len(sigs)} con ventaja >= {config.MIN_EDGE*100:.0f}% "
          f"(banca ${config.START:.0f}, {config.KELLY_FRAC:.3f} Kelly, t-dof {config.STUDENT_T_DOF:.0f})")
    for s in sigs[:20]:
        print(f"  +{s['edge_pct']:5.1f}%  {s['asset']:4s} {s['side'].upper():3s} "
              f"{str(s['sub_title'])[:26]:26s} @ {s['odds']:.2f}  "
              f"(modelo {s['p_model']*100:.0f}% vs Kalshi {s['kalshi_prob']*100:.0f}%)  "
              f"${s['stake']:.2f}  vol {s['volume']:.0f}  {s['hours_left']:.1f}h")
    if not sigs:
        print("  (sin ventaja ahora mismo — normal en mercados liquidos y afinados)")
    print("\nHonesto: modelo vs Kalshi. Usar como paper hasta que la banca confirme edge real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
