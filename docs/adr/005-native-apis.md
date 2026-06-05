# ADR-005: APIs nativas en lugar de herramientas de terceros (Buffer, Make, Zapier)

**Estado:** Aceptado  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

Existen herramientas SaaS (Buffer, Make, Zapier) que abstraen la publicación en redes sociales con UIs visuales. Son más rápidas de configurar pero añaden una capa de dependencia y coste mensual. La alternativa es integrar directamente con X API v2 y LinkedIn API.

---

## Decisión

Integración **directa con APIs nativas**: X API v2 (OAuth 1.0a via `requests-oauthlib`) y LinkedIn UGC Posts API (Bearer token). Sin intermediarios.

---

## Consecuencias

**Positivas:**
- Control total sobre el payload, headers, y manejo de errores.
- Mejor showcase técnico: demuestra conocimiento de OAuth, rate limits, y APIs REST reales.
- Sin coste adicional por herramienta SaaS.
- Los `post_id` reales se pueden persistir para reconciliación y auditoría.

**Negativas:**
- Setup más complejo: LinkedIn requiere Company Page y aprobación del Marketing Developer Program (o uso de UGC Posts como persona/página).
- Cambios en las APIs (X en particular) pueden romper el pipeline sin previo aviso.
- Requiere gestión manual de tokens (LinkedIn expira cada 60 días).

**Mitigación de riesgo:**  
Contract tests nightly (ADR-008) detectan cambios de schema en las respuestas de API antes de que afecten a una run de producción.
