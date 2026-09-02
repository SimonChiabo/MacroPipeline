# ADR-008: Contract tests nightly separados del pipeline de PR

**Estado:** Aceptado e implementado (workflow + primeros contract tests de FRED, 2026-08-21)  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline depende de APIs externas (FRED, FMP, Alpha Vantage) que pueden cambiar su schema de respuesta, sus rate limits, o sus endpoints sin previo aviso. Si esto ocurre y no se detecta hasta el siguiente run de producción, el pipeline falla en producción con un error que podría haberse detectado antes.

La alternativa de incluir tests contra APIs reales en el pipeline de PR tiene dos problemas: (1) requiere credenciales reales en CI, (2) hace los PRs lentos y flaky por dependencias externas.

---

## Decisión

**Dos pipelines de test separados:**

1. **Pipeline de PR** (`ci.yml`): usa `unittest.mock` para simular todas las respuestas de API. Rápido, determinista, sin credenciales. Ejecuta en cada push.

2. **Contract tests nightly** (`contract-tests.yml`): ejecuta L-V a las 07:00 UTC contra las APIs reales con credenciales almacenadas en GitHub Secrets. Verifica que los schemas de respuesta siguen siendo los esperados. Si falla, notifica por Telegram.

---

## Consecuencias

**Positivas:**
- PRs rápidos y deterministas (mocks no tienen latencia de red).
- Cambios de API detectados automáticamente antes de afectar a producción.
- Las alertas de Telegram permiten reaccionar antes del run del viernes.

**Negativas:**
- Dos workflows que mantener en lugar de uno.
- Las credenciales deben estar en GitHub Secrets (no en `.env`).
- Los contract tests pueden ser flaky por rate limits de APIs gratuitas (mitigado con `--timeout=30` y reintentos manuales).

**Estado de implementación (2026-08-21):**

El workflow `contract-tests.yml` está creado y `tests/contract/test_fred_contract.py` cubre FRED con nueve casos: el hito propuesto (`GDP` devuelve `date` y `value`), las tres series que consume el cierre semanal (`CPIAUCNS`, `UNRATE`, `DGS10`) con rangos de plausibilidad y control de frescura, **la definición de esas mismas tres contra los metadatos de FRED** (ajuste estacional y frecuencia), la construcción completa del `MacroSnapshot` contra la API real, y el error esperado ante una serie inexistente.

> **Nota (2026-09-02).** Los tres casos de definición se agregaron ese día, y la serie del IPC pasó de `CPIAUCSL` a `CPIAUCNS`. Los rangos de plausibilidad no distinguían una de otra —las dos son índices de nivel con la misma base—, así que el pipeline publicó la serie equivocada durante toda su vida sin que ningún test se pusiera en rojo. Lo que separa a las dos es lo que FRED dice que son, y eso es lo que ahora se verifica. Ver `docs/data-dictionary.md`.

Cuatro decisiones que no estaban en la versión original de este ADR y conviene dejar escritas:

1. **Verificar el schema no alcanza.** `FREDClient.get_series_observations` construye `df[['date','value']]` por su cuenta y devuelve un DataFrame vacío —solo con un warning— cuando la respuesta no trae observaciones. Un test que compare nombres de columna pasaría con FRED devolviendo un payload vacío. Por eso cada caso exige además `len(df) > 0`, dtypes y un rango de valores plausible: eso es lo que detecta un cambio de unidad o una serie discontinuada.

2. **Faltar una credencial es un fallo, no un skip.** Un run enteramente saltado sale con código 0 y reportaría el nightly en verde sin haber verificado nada. `tests/contract/conftest.py` resuelve así: en local se salta si falta la key, pero si `CI` está definida en el entorno, falla ruidosamente.

3. **Los contract tests están fuera del run por defecto.** `pyproject.toml` define `addopts = "-m 'not contract'"` para que `pytest` local no pegue contra la red. La contracara es que el workflow **tiene que** pasar `-m contract` explícitamente: sin eso deselecciona todo y sale en verde sin ejecutar un solo test. `pytest-timeout` se agregó a las dependencias de desarrollo, que era lo que faltaba para que `--timeout=30` no fuera un error de argumentos.

4. **La alerta no puede ser el único canal.** El repo estuvo sin ningún secret configurado y el modo de fallo fue el peor posible: pytest moría por credencial faltante y el paso de notificación hacía `curl -s` contra `api.telegram.org/bot/sendMessage` con el token vacío. Telegram contestaba 404, `curl -s` devolvía 0, y el paso quedaba en verde. Un nightly roto se veía igual que un nightly con la alerta entregada. Ahora el workflow (a) comprueba los secrets antes de instalar nada y falla nombrando lo que falta, (b) escribe el fallo en `$GITHUB_STEP_SUMMARY`, que no depende de ninguna credencial, y (c) mira el status code de la llamada a Telegram y falla el paso si no es 200.

**Pendiente:** contract tests equivalentes para FMP y Alpha Vantage. El workflow ya les pasa las credenciales; sus keys se agregan a la comprobación previa cuando esos tests existan (hoy solo exige `FRED_API_KEY`, que es la única que algún test consume).
