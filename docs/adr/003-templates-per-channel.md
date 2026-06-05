# ADR-003: Plantillas por canal con data layer compartido

**Estado:** Aceptado  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

X (Twitter) y LinkedIn tienen formatos radicalmente distintos: X limita a 280 caracteres con imagen cuadrada, LinkedIn favorece texto largo con imágenes rectangulares o carruseles PDF. Un adaptador genérico que intente cubrir ambos produce contenido mediocre en los dos.

---

## Decisión

Un mismo **data layer** (los schemas Pydantic validados) alimenta **renderers especializados por plataforma**:

- `PlaywrightEngine` renderiza templates HTML/CSS complejos (cierre semanal) → PNG 1080×1080
- `PillowEngine` renderiza templates simples (earnings calendar) → PNG 1080×1080
- En mes 2-3: template específico para Instagram con dimensiones distintas

El headline generado por el LLM es el mismo para todos los canales (ya que es corto por diseño, ≤120 chars). Si en el futuro se necesitan headlines distintos por canal, se instancian múltiples `LLMClient` con prompts específicos.

---

## Consecuencias

**Positivas:**
- Cada canal puede evolucionar visualmente de forma independiente sin afectar a los otros.
- Cero drift entre los datos mostrados en distintas plataformas (mismo data layer).
- Determinismo total: mismos inputs → mismo output visual en cualquier entorno.

**Negativas:**
- N templates que mantener (actualmente 2: `weekly_close.html` + Pillow earnings).
- Si el template cambia, hay que verificar que el render sigue siendo correcto en todos los tamaños.

**Implicación de versionado:**  
Los templates son parte del sistema de reproducibilidad. Un cambio de template sin cambio de `HEADLINE_PROMPT_VERSION` puede producir renders distintos para el mismo `event_id`. Considerar versionar los templates junto a los prompts.
