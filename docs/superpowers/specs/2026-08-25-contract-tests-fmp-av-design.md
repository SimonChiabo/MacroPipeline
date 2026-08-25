# Contract tests de FMP y Alpha Vantage

**Fecha:** 2026-08-25
**Estado:** Aprobado, sin implementar
**Cierra:** el pendiente que ADR-008 dejo anotado desde el 2026-08-21.

---

## Lo que se verifico antes de disenar

Sonda contra las dos APIs reales, con las credenciales del `.env`:

```
FMP ^GSPC    filas= 1254 ultimo=2026-08-24 close=7,657.71
FMP ^IXIC    filas= 1254 ultimo=2026-08-24 close=26,029.15
AV  SPY      filas=  100 ultimo=2026-08-21 close=765.72
AV  QQQ      ERROR: rate limit excedido
```

Cuatro cosas que salieron de ahi y que fijan el diseno:

1. **El rate limit de AV es un throttle por minuto que se limpia solo.** La
   llamada a QQQ volvio a funcionar minutos despues y devolvio `Meta Data` +
   `Time Series (Daily)`, sin clave de error. Una cuota diaria agotada no se
   recupera en minutos. El cliente lo manejo bien: la clave `Information`
   existe y `av_client.py` la caza.
2. **AV va una sesion atrasada respecto de FMP.** `3. Last Refreshed` daba
   `2026-08-21` cuando FMP ya traia el cierre del `2026-08-24`. El test de
   frescura de AV necesita margen ancho; el de FMP puede ser mas estricto.
3. **`FMP_API_KEY` y `ALPHA_VANTAGE_API_KEY` no estan en GitHub Secrets.**
   Solo hay cuatro (`FRED_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`). El workflow ya las pasa como env, pero llegan vacias.
4. **El nivel publicado por la ruta de fallback esta mal etiquetado.** Ver
   abajo: es el hallazgo grande y no es un cambio de API.

## El hallazgo de escala

El pipeline pide a FMP `^GSPC` (el indice, 7.657,71) y cae a AV con `SPY` (el
ETF, 765,72). Los retornos salen bien porque son invariantes de escala, pero el
nivel no: `main.py:183` guarda `sp500_close=float(sp_last["close"])` venga de
donde venga, y `playwright_engine.py:94` lo renderiza rotulado
**"SP500: Cierre"**. Por la ruta de AV se publicaria `765,72` como cierre del
S&P 500: un numero real, de un instrumento real, bajo la etiqueta equivocada.

Es la misma clase de fallo que el reetiquetado del LLM del 2026-08-24 —**una
cifra conserva el indicador del que salio**, la invariante anotada en ADR-001—
esta vez en el ETL en lugar de en el prompt. No lo caza nadie: `schemas.py:37`
solo exige `gt=0`.

Un contract test **no puede arreglarlo**: AV devolviendo SPY a 765 esta
cumpliendo su contrato al pie de la letra. Por eso el arreglo va en el
validador y no en los tests.

## Decisiones

**1. Rangos de nivel en `rules.yaml`, no en el `Field` de Pydantic.**
`validate_weekly_close` ya lee minimos y maximos declarativos para los retornos
(`sp500_return_min`, etc.) y `validate_macro_snapshot` hace lo mismo para el
bloque macro. Los niveles siguen ese patron: `sp500_close_min: 2000`,
`sp500_close_max: 30000`, `nasdaq_close_min: 5000`, `nasdaq_close_max: 100000`.
Rangos deliberadamente anchos: detectan un cambio de unidad o de instrumento,
no la fluctuacion normal del mercado.

**Consecuencia aceptada:** con esto la ruta de fallback de AV **deja de poder
publicar**. SPY a 765,72 no pasa el minimo de 2000, `validate_weekly_close`
levanta `ValidationError`, y eso cae en el `except Exception` de `main.py:380`,
que aborta la run sin publicar. Es la direccion correcta —mejor no publicar que
publicar 765,72 rotulado "SP500"— pero significa que si FMP se cae un viernes,
no sale nada en vez de salir algo mal. La alternativa que lo conservaria
(publicar solo el retorno cuando `data_source == "av"`) quedo descartada
explicitamente y anotada como trabajo futuro.

**2. Un fichero por API**, siguiendo el patron de FRED: `test_fmp_contract.py`
y `test_av_contract.py`. Los dos clientes tienen tipos de error distintos y
presupuestos de llamadas distintos; parametrizar sobre las dos fuentes
terminaria en una funcion llena de `if`.

**3. El presupuesto de AV se resuelve con una fixture de sesion.** Una sola
llamada a SPY por nightly, reutilizada por todos los asserts. No se testea QQQ:
el contrato es del endpoint, no del simbolo, y cada llamada cuesta cuota.

**4. Un rate limit de AV pone el nightly en rojo, pero se distingue en la
alerta.** Chocan dos principios del proyecto: *un nightly verde que no verifico
nada es peor que uno rojo* y *un chequeo ruidoso se termina desactivando*. Se
resuelve fallando —no puede decir verde algo que no verifico— pero nombrando la
causa, para que leyendo la alerta de Telegram se sepa en un segundo si hay que
tocar algo o alcanza con relanzar el workflow.

## Cambios

1. `src/macro_pipeline/validators/rules.yaml`: cuatro claves nuevas en
   `weekly_close` con su comentario.
2. `src/macro_pipeline/validators/engine.py`: dos chequeos mas en
   `validate_weekly_close`, con el mismo `rules.get(...)` y `ValidationError`
   que ya usan los retornos.
3. `tests/unit/test_validators.py`: casos para los dos rangos nuevos, incluido
   el caso que motivo todo (un nivel a escala de ETF se rechaza).
4. `tests/contract/conftest.py`: fixtures `fmp_client` y `av_client` via
   `require_api_key`, y `av_spy_daily` de sesion (una llamada).
5. `tests/contract/test_fmp_contract.py`: `^GSPC` y `^IXIC` no vacios, `close`
   numerico, **`len(df) >= 6`** (el requisito duro de `main.py:152`, el
   analogo del test de lookback de FRED), rangos de plausibilidad, frescura
   dentro de ~5 dias naturales, y simbolo inexistente -> `FMPClientError`.
6. `tests/contract/test_av_contract.py`: `len(df) > 0` (el cliente devuelve
   DataFrame vacio con solo un warning si falta `Time Series (Daily)`),
   `notna()` sobre las columnas numericas (AV devuelve strings y el cliente
   hace `pd.to_numeric(errors="coerce")`, asi que un cambio de formato se
   vuelve NaN silenciosos y no excepcion), frescura con margen ancho, y rango
   de plausibilidad para SPY.
7. `.github/workflows/contract-tests.yml`: las dos keys al bloque `missing` del
   pre-chequeo, la salida de pytest a un fichero con `pipefail`, y el paso de
   Telegram eligiendo el texto segun el marcador `AV_RATE_LIMIT`.
8. Cargar `FMP_API_KEY` y `ALPHA_VANTAGE_API_KEY` con `gh secret set` pipeando
   el valor por stdin (a secas abre un prompt interactivo y se cuelga).

El orden importa: el punto 8 va antes del 7. Agregar las keys al pre-chequeo
sin haberlas cargado pone el nightly en rojo.

## Verificacion

- `pytest tests/unit/ tests/integration/ -q` en verde, mas ruff y mypy.
- `pytest tests/contract/ -m contract -v` en local, con las dos keys del `.env`.
  El `-m contract` es obligatorio: `pyproject.toml` define
  `addopts = "-m 'not contract'"` y sin sobrescribirlo se deselecciona todo y
  sale verde sin ejecutar nada.
- Un run manual de `contract-tests.yml` por `workflow_dispatch`, ya con los
  secrets cargados.
- El marcador de rate limit se verifica sin esperar a que AV lo devuelva:
  forzando el `AlphaVantageClientError` con el mensaje de rate limit y
  comprobando que el test falla con `AV_RATE_LIMIT` en la salida.

## Fuera de alcance

- **Publicar solo el retorno en la ruta de AV.** Es lo que conservaria el
  fallback en lugar de bloquearlo, y toca render y mensaje. Queda anotado.
- **El ADR de politica de degradacion.** El pipeline degrada y publica en
  cuatro sitios y aborta en uno, decidido en commits distintos y sin ningun
  documento que lo diga junto. Es su propio trabajo.
- **Test cruzado FMP vs AV** (que `^GSPC` sea ~10x `SPY`). Es una propiedad del
  pipeline, no un contrato de ninguna de las dos APIs, y duplicaria el consumo
  de cuota de AV. El arreglo real vive en el validador.
- **Reintentos o backoff en los tests.** ADR-008 ya acepto la flakiness como
  costo conocido, con relanzamiento manual.
