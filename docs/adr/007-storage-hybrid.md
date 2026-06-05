# ADR-007: Storage híbrido SQLite + Cloudflare R2

**Estado:** Aceptado  
**Fecha:** 2026-05-14  
**Decisores:** Simon Chiabo

---

## Contexto

El pipeline necesita dos tipos de persistencia con propósitos distintos:
1. **Estado operacional**: ¿ya se publicó este evento? ¿está en progreso? ¿qué post_ids se generaron?
2. **Snapshots inmutables**: ¿con qué datos exactos se generó cada imagen? ¿cómo se veía el render?

Una base de datos relacional puede cubrir ambos, pero mezcla concerns distintos.

---

## Decisión

**Storage híbrido:**
- **SQLite local**: estado operacional durante y después de cada run. Tabla `published_events` con todos los metadatos de trazabilidad (data_source, post_ids, prompt_version, headline, validator_approved).
- **Cloudflare R2**: snapshots de imágenes PNG generadas, almacenados con el `event_id` como clave. Inmutables por convención (no se sobrescriben).

---

## Consecuencias

**Positivas:**
- SQLite es suficiente para el volumen actual (publicaciones semanales = decenas de registros/año).
- R2 free tier (10GB) cubre años de imágenes PNG a 1080×1080.
- Separación de concerns: la base operacional es local y fast; los snapshots son remotos e inmutables.
- R2 usa la API compatible con S3 (`boto3`): fácil de migrar a S3 real si fuera necesario.

**Negativas:**
- Dos sistemas en lugar de uno añade complejidad operacional.
- SQLite es local: si se cambia de máquina sin migrar el archivo, se pierde el estado. Mitigado por `STATE_DB_PATH` configurable.
- R2 requiere credenciales adicionales (gestionadas como optional: el pipeline funciona sin R2, solo sin snapshots remotos).

**Implicación de reproducibilidad:**  
La combinación SQLite (metadatos) + R2 (imagen generada) permite responder, dado un `event_id`, qué datos produjeron qué imagen. Para reproducir el headline, se necesita además `prompt_version` (en SQLite) y el código en el commit correspondiente.
