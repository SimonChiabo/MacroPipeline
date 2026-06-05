# ADR-001: LLM fuera del path numérico

**Estado:** Aceptado  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

Un pipeline de datos financieros que usa LLMs enfrenta una contradicción estructural: los modelos de lenguaje son probabilísticos y pueden alucinar, pero los datos financieros requieren precisión determinista. La pregunta inicial era si Claude podía encargarse del ETL completo (cálculo de retornos, comparación de indicadores, etc.).

---

## Decisión

**El LLM no toca números.** Todo el procesamiento numérico (precios de cierre, cálculo de retornos semanales, comparación con históricos, sanity checks) se ejecuta en Python determinista con Pandas + Pydantic. Claude API solo recibe un resumen textual de los números ya calculados y tiene la tarea restringida de redactar un titular de máximo 120 caracteres.

Un segundo agente LLM (`ValidatorAgent`) actúa como segunda opinión: verifica que el titular no contenga números que no estaban en el input. Usa `tool_choice` forzado para garantizar output estructurado.

---

## Consecuencias

**Positivas:**
- Determinismo total en el path crítico de datos.
- Un error numérico en un post es imposible si el ETL es correcto (el LLM no puede inventar números que no recibió).
- Facilita testing: el ETL se puede testear sin llamadas a la API de Anthropic.

**Negativas:**
- Menos "magia": el sistema no puede inferir contexto adicional de los datos. Solo reformula lo que recibe.
- Dos llamadas a la API por run (generación + validación) en lugar de una.

**Implicaciones técnicas:**
- El `data_str` que se pasa al LLM nunca debe contener datos no validados previamente por Pydantic + `ValidationEngine`.
- Si se añaden nuevas fuentes de datos externas (ej. nombres de empresas, titulares de prensa), deben sanitizarse antes de entrar al prompt para evitar prompt injection.
