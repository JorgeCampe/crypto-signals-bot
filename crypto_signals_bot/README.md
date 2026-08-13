# Bot de Señales — Cripto (Kalshi)

App autónoma con **banca simulada que arranca en $250**. Opera las señales de un
modelo de precio contra el precio **neto** de Kalshi (comisión descontada) y
**liquida** cada posición con el precio real del activo al cierre (velas 1-min de
Coinbase). Genera su propio `dashboard.html`.

Por defecto es **paper** (no coloca órdenes reales). El juez es la banca: si con el
tiempo no crece, no hay edge real. El modo **live** (órdenes reales) es opcional; ver
el README de la raíz.

## Cómo correrlo

```bash
python bot.py            # corre la simulación y regenera dashboard.html
python bot.py --open     # además abre el dashboard en el navegador
```

## Qué hace en cada corrida

1. **Liquida** las posiciones abiertas cuyo mercado ya cerró: toma el precio real del
   activo en el instante del cierre (Coinbase, que aproxima el índice de Kalshi) y
   decide gana/pierde comparándolo con el strike. Si nunca aparece dato en 3 días,
   la anula.
2. Lee las **señales** de ahora: modelo de precio (log-normal + colas t-Student) vs
   precio neto de Kalshi. Opera el lado (YES o NO) con ventaja ≥ 6 % (⅛ Kelly, tope
   10 % de la banca por operación, cuota máx 3.0).
3. **Coloca** una posición por mercado dentro del cash disponible (y, si el modo live
   está activo y pasa los topes de riesgo, la **orden real** correspondiente).
4. Actualiza `data/positions.csv`, `data/equity.csv`, `data/signals.csv` y regenera
   `dashboard.html` (curva de banca, KPIs, Monte Carlo, tablas).

## Estructura del proyecto

```
config.py                     parámetros (banca, edge, Kelly, modelo, topes live)
ml/prices.py                  spot + velas + volatilidad + precio histórico (Coinbase)
ml/kalshi_crypto.py           lee los mercados de cripto de Kalshi (público)
ml/crypto_model.py            el "cerebro": P(precio ≥/≤/entre strike)  [sin dependencias]
ml/crypto_signals.py          modelo vs Kalshi → señales con edge y Kelly
ml/execution.py               órdenes REALES (firma RSA-PSS) — solo modo live
crypto_signals_bot/bot.py     motor: banca paper + live opcional + dashboard
```

## Parámetros (editables al inicio de `config.py`)

Ver la tabla del README de la raíz. Los más usados: `CRYPTO_MIN_EDGE` (ventaja mínima),
`CRYPTO_KELLY_FRAC` (tamaño), `CRYPTO_T_DOF` (cuán gordas son las colas del modelo),
`CRYPTO_MIN_VOLUME` (filtro de liquidez).

## Honestidad

Los mercados de BTC/ETH diarios de Kalshi son líquidos y afinados; ahí casi nunca hay
edge. La apuesta es que rangos intradía y altcoins menos mirados estén a veces mal
preciados. La banca en paper lo dirá. **Usar como paper hasta comprobar edge real. No
es consejo financiero.**
