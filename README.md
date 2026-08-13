# Bot de Señales de Cripto (Kalshi) — corre solo en la nube

Hermano del bot de tenis, pero para los mercados de **cripto** de Kalshi (BTC, ETH,
SOL, XRP, DOGE…). Corre en **GitHub Actions** cada 30 min, **sin tu PC**. Lleva una
banca simulada que arranca en **$250**, opera las señales de un modelo de precio
contra el precio neto de Kalshi y **liquida** cada posición con el precio real del
activo al cierre. Publica un **dashboard** que ves desde el celular vía GitHub Pages.

El modo **paper** (simulación) no usa ninguna key: Kalshi y Coinbase son públicos.
El modo **live** (órdenes reales) es opcional y viene **apagado**; se activa con tus
credenciales de Kalshi y tiene topes de riesgo estrictos. Ver más abajo.

## Qué cambia respecto al bot de tenis

| | Tenis | Cripto |
|---|---|---|
| Cerebro (probabilidad) | Elo v2 + ML de jugadores | **Modelo de precio** log-normal con colas gordas (t-Student) sobre el spot y la volatilidad |
| Mercados de Kalshi | KXATPMATCH / KXWTAMATCH | KXBTCD, KXETHD, KXBTC, KXETH, KXSOLE, KXXRP, KXDOGED |
| Liquidación | resultados de ESPN | **precio real del activo** al cierre (velas 1-min de Coinbase) |
| Órdenes reales | no (solo paper) | **opcional** (modo live con firma RSA) |

## Cómo funciona el "cerebro"

Para cada mercado ("¿el precio de BTC estará ≥ $X a las 5 PM?") el bot:

1. Toma el **precio spot** actual y la **volatilidad realizada** (Coinbase, público).
2. Con un modelo de precio log-normal calcula la probabilidad **independiente** de
   que el precio final caiga por encima / por debajo / dentro del strike. Usa una
   distribución **t-Student** (colas gordas) porque las criptos **saltan**: eso evita
   el "edge falso" que una campana de Gauss vería en strikes lejanos.
3. Compara esa probabilidad contra el precio **neto** de Kalshi (comisión descontada).
   Donde hay ventaja ≥ 6 % opera el lado (YES o NO) con su tamaño (⅛ de Kelly).

## Puesta en marcha (una sola vez, ~5 min) — modo paper

1. En GitHub crea un repo **nuevo y PÚBLICO** llamado `kalshi-crypto-bot`
   (sin README, sin .gitignore).
2. Desde esta carpeta, en una terminal:

   ```bash
   git init
   git add .
   git commit -m "cryptobot inicial"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/kalshi-crypto-bot.git
   git push -u origin main
   ```

3. En el repo: **Settings → Pages → Source: Deploy from a branch → Branch: `main` /
   carpeta `/docs` → Save**. En 1 min tendrás el dashboard en
   `https://TU_USUARIO.github.io/kalshi-crypto-bot/`.
4. Pestaña **Actions**: si pide habilitar workflows, acepta. Entra a *Crypto Signals
   Bot* y toca **Run workflow** para correrlo ya (o espera al próximo bloque de 30 min).

Listo. De ahí en adelante corre solo, guarda el historial y actualiza el dashboard.

## Modo LIVE — órdenes reales (opcional, con cuidado)

> ⚠️ **Empieza siempre en paper.** Deja que la banca simulada corra varios días. Si
> **no** crece de forma consistente, **no hay edge** y el modo live solo perderá
> dinero (comisiones incluidas). Actívalo solo si el tracker muestra ventaja real.

Cuando quieras operar de verdad:

1. En Kalshi (con tu cuenta con fondos): **Account → API Keys → Create**. Guarda el
   **API Key ID** y descarga la **clave privada** (`.pem`). La clave privada solo se
   muestra una vez.
2. En tu repo de GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**, y crea:
   - `KALSHI_API_KEY_ID` = tu API Key ID
   - `KALSHI_PRIVATE_KEY` = el contenido completo del `.pem` (incluyendo las líneas
     `-----BEGIN PRIVATE KEY-----` … `-----END PRIVATE KEY-----`)
   - `KALSHI_LIVE` = `1`
3. En la próxima corrida el bot colocará **órdenes reales** de las señales, respetando
   los topes de riesgo. El dashboard mostrará una barra naranja "Modo LIVE" con tu
   saldo real y marcará las posiciones reales con la etiqueta `REAL`.

Para volver a paper: borra el secret `KALSHI_LIVE` (o ponlo en `0`).

### Topes de riesgo del modo live (editables en `config.py` o por variables)

| Variable | Default | Qué limita |
|---|---|---|
| `KALSHI_LIVE_MAX_CONTRACTS` | 5 | contratos máx por orden |
| `KALSHI_LIVE_MAX_ORDER_USD` | 10 | desembolso máx por orden ($) |
| `KALSHI_LIVE_MAX_DAILY_LOSS` | 25 | corta el día si pierde esto ($) |
| `KALSHI_LIVE_MAX_OPEN_ORDERS` | 8 | posiciones reales simultáneas máx |

**Seguridad:** este repo nunca contiene tu clave. El `.gitignore` bloquea `*.pem` y
`.env` para que no la subas por error. Las credenciales viven solo en GitHub Secrets
(enmascarados en los logs). Nunca las pegues en `config.py`.

## Correrlo en tu PC

```bash
pip install -r requirements.txt
python crypto_signals_bot/bot.py --open     # corre y abre el dashboard
python ml/crypto_signals.py                  # solo ver las señales de ahora
python ml/kalshi_crypto.py                    # ver los mercados que lee
python ml/prices.py                           # ver spot y volatilidad
```

El estado en la nube y el local son independientes.

## Cambiar la frecuencia o los activos

- **Frecuencia:** edita el `cron` en `.github/workflows/crypto-bot.yml`.
  Ej. `*/15 * * * *` (cada 15 min), `0 * * * *` (cada hora).
- **Activos:** variable `CRYPTO_SERIES` (o edita `config.py`). Formato
  `SERIE:ACTIVO:PRODUCTO_COINBASE`, separadas por coma.

## Parámetros principales (en `config.py`, todos sobre-escribibles por entorno)

| Parámetro | Default | Qué es |
|---|---|---|
| `CRYPTO_BANKROLL` | 250 | banca inicial |
| `CRYPTO_MIN_EDGE` | 0.06 | ventaja mínima modelo vs Kalshi para operar |
| `CRYPTO_KELLY_FRAC` | 0.125 | fracción de Kelly (⅛; cripto es volátil) |
| `CRYPTO_MAX_STAKE_FRAC` | 0.10 | tope por operación (10 % de la banca) |
| `CRYPTO_MIN_VOLUME` | 2000 | contratos/24 h mínimos (evita mercados finos) |
| `CRYPTO_MIN_HOURS` / `MAX_HOURS` | 0.5 / 36 | ventana de tiempo al cierre |
| `CRYPTO_T_DOF` | 4 | grados de libertad t-Student (colas gordas; 0 = normal) |
| `CRYPTO_VOL_INFLATE` | 1.15 | margen de seguridad sobre la volatilidad estimada |

## Honestidad (léelo)

Los mercados de **BTC/ETH diarios** de Kalshi son **muy líquidos y afinados**: ahí
casi nunca habrá ventaja, y el modelo lo confirmará estando de acuerdo con el precio.
La apuesta es que los mercados **menos mirados** (rangos intradía, altcoins con menos
volumen) estén a veces mal preciados. La banca en paper es el juez.

Además, la liquidación de Kalshi usa el índice **CF Benchmarks (BRTI)**; el bot lo
**aproxima** con la vela de 1 minuto de Coinbase en el cierre. No es idéntico, así que
en paper puede haber pequeñas diferencias en casos muy ajustados. Un modelo de precio
tampoco predice la dirección del mercado: solo mide dispersión. Ventajas enormes
(+30/+50 %) casi siempre significan que el modelo se equivoca, no que hay oro.

**Usar como paper hasta comprobar edge real. Esto no es consejo financiero.**
