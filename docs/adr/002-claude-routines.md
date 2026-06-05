# ADR-002: Claude Routines como orquestador principal

**Estado:** Aceptado (con plan B documentado)  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline necesita un trigger programado (cron) que ejecute el script Python cada viernes al cierre del mercado. Las opciones evaluadas fueron: servidor propio con cron, GitHub Actions scheduled workflow, o Claude Routines (feature de Anthropic en research preview).

---

## Decisión

Usar **Claude Routines** como orquestador principal. Routines clona el repositorio y ejecuta el script `src/macro_pipeline/orchestration/main.py` en un entorno gestionado por Anthropic. La llamada a Claude API se hace desde dentro del script Python, no desde Routines directamente.

---

## Consecuencias

**Positivas:**
- Cero infraestructura propia para el scheduler.
- Entorno gestionado (patches de seguridad, uptime) sin coste adicional.
- Encaja con la suscripción de Anthropic existente.

**Negativas:**
- Routines está en research preview: puede cambiar, degradarse o desaparecer.
- El daily cap de Routines (15 ejecuciones/día) es suficiente para el volumen actual pero debe monitorizarse.
- Dependencia de vendor para la función de scheduling.

**Plan B (documentado, no implementado):**  
Si Routines cambia en incompatible con el proyecto, migrar a GitHub Actions scheduled workflow:
```yaml
on:
  schedule:
    - cron: "30 21 * * 5"  # Viernes 21:30 UTC (cierre mercado USA)
```
El script Python no necesita cambios: acepta cualquier trigger externo.

**Implicación de resiliencia:**  
El script es idempotente: si se ejecuta dos veces el mismo día, la segunda ejecución detecta el `event_id` ya publicado en SQLite y termina sin publicar.
