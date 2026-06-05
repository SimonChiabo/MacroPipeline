# ADR-008: Contract tests nightly separados del pipeline de PR

**Estado:** Aceptado (workflow pendiente de implementar — ver H-17 en revision_v2_resultado.md)  
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

**Estado de implementación:**  
El workflow `contract-tests.yml` está creado. El directorio `tests/contract/` existe pero los tests están pendientes de implementar (primer hito: un test que llame a `FREDClient.get_series_observations("GDP")` y verifique que la respuesta tiene las columnas `date` y `value`).
