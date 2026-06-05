# ADR-004: HITL los primeros 30 días, auto-publicación después

**Estado:** Aceptado (en ejecución — HITL activo)  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline publica contenido con framing financiero en cuentas públicas. Un error en los datos o una alucinación del LLM tiene impacto reputacional directo. La tensión es entre "minimizar intervención humana" (objetivo de automatización) y "garantizar que no se publique algo incorrecto" (objetivo de calidad).

---

## Decisión

Los **primeros 30 días** de operación, cada draft generado pasa por **aprobación humana vía Telegram bot** antes de publicarse. El operador recibe:
- La imagen renderizada (preview visual)
- El titular generado por el LLM
- Dos botones: ✅ Aprobar / ❌ Rechazar

Si no hay respuesta en 3600 segundos, el pipeline expira (`status=expired`) y no publica. El operador puede re-ejecutar manualmente si lo desea.

Tras 30 días de operación estable (definición: 0 rechazos manuales por error de datos, 0 alucinaciones detectadas), se evalúa migrar a auto-publicación con Slack como canal de notificación.

---

## Consecuencias

**Positivas:**
- Margen de seguridad real durante el período de rodaje.
- El bot de Telegram es por sí mismo una feature técnica demostrable.
- El operador puede rechazar posts en cualquier momento: kill switch natural.
- Los logs de aprobación (`approved_by`, `rejected_by`) quedan en SQLite para auditoría.

**Negativas:**
- Requiere atención del operador cada viernes (~5 minutos).
- Si el operador no está disponible, el pipeline expira sin publicar ("mejor nada que algo no revisado").

**Implicaciones de seguridad:**  
`TELEGRAM_ALLOWED_USER_ID` debe configurarse para que solo el operador autorizado pueda aprobar. Sin esta variable, cualquier persona con acceso al chat puede tomar control de las publicaciones.
