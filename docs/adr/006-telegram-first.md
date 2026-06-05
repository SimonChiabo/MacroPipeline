# ADR-006: Telegram bot primero, Slack después

**Estado:** Aceptado (Telegram activo; migración a Slack planificada para mes 2-3)  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El HITL (Human-in-the-Loop) requiere un canal para enviar drafts al operador y recibir su respuesta. Las opciones evaluadas fueron: email, Slack (con Bolt SDK + endpoint propio), y Telegram bot.

---

## Decisión

**Empezar con Telegram** por las siguientes razones:
1. **Long polling nativo**: no requiere endpoint público (no hay servidor web que mantener).
2. **Setup en minutos**: `BotFather` + token + chat_id. Cero infraestructura adicional.
3. **Inline buttons**: la API soporta natively botones de aprobación/rechazo.
4. **Cero coste**: la API de Telegram es gratuita sin límites relevantes para este volumen.

La migración a Slack está planificada para mes 2-3 como una feature de portfolio adicional (documentada como ADR-009 cuando se ejecute).

---

## Consecuencias

**Positivas:**
- Primer HITL operativo en horas, no días.
- La migración a Slack es por sí misma un punto de portfolio (demuestra iteración y refactoring documentado).

**Negativas:**
- Estética de Slack es superior para un showcase profesional.
- Long polling es menos elegante que webhooks (Slack usa webhooks por defecto).
- Telegram requiere que el operador tenga la app instalada.

**Implicaciones de seguridad:**  
Ver ADR-004: `TELEGRAM_ALLOWED_USER_ID` es obligatorio para que el HITL no sea bypasseable por cualquier miembro del chat. El bot en modo long polling sin este check acepta aprobaciones de cualquier usuario que pueda enviar un mensaje al chat.
