# Política de privacidad — MacroPipeline

**Última actualización:** 21 de agosto de 2026

MacroPipeline es un proyecto personal, sin fines comerciales, que publica un
resumen semanal del cierre de los mercados en las cuentas propias de su autor
en X y LinkedIn.

## Qué datos se procesan

MacroPipeline **no recolecta, almacena ni procesa datos personales de terceros**.
No tiene usuarios, no ofrece registro y no expone ninguna interfaz pública.

Los datos que el sistema maneja son:

- **Datos de mercado y macroeconómicos** obtenidos de fuentes públicas de
  terceros: Financial Modeling Prep, Alpha Vantage y FRED (Federal Reserve Bank
  of St. Louis). Son series de precios e indicadores agregados; no contienen
  información personal.
- **Identificadores de las publicaciones propias.** Tras publicar, el sistema
  guarda el ID del post generado en su propia cuenta, únicamente para no
  publicar dos veces el mismo cierre semanal si el proceso se reintenta.
- **Credenciales de acceso a las APIs**, almacenadas localmente en la máquina
  del autor mediante variables de entorno. No se transmiten a ningún tercero
  fuera de las APIs oficiales a las que corresponden, y se redactan de los
  registros de diagnóstico antes de que estos salgan del proceso.

## Qué NO se hace

- No se leen, recolectan ni analizan perfiles, publicaciones ni actividad de
  otros usuarios de LinkedIn o X.
- No se comparten, revenden ni redistribuyen datos obtenidos de las APIs de
  LinkedIn o X con terceros.
- No se muestra contenido de esas plataformas fuera de ellas.
- No se utilizan cookies, píxeles de seguimiento ni analítica de terceros.
- No se hace publicidad ni se generan ingresos a partir de las APIs.

## Uso de las APIs de LinkedIn y X

El acceso se limita a publicar contenido propio en las cuentas del autor:
`POST /v2/ugcPosts` en LinkedIn y `POST /2/tweets` en X. Cada publicación
requiere aprobación humana explícita antes de enviarse.

## Retención y eliminación

El estado del sistema se guarda en una base SQLite local en la máquina del
autor. No hay servidores ni bases de datos accesibles públicamente. Los datos
pueden eliminarse en cualquier momento borrando ese archivo local.

## Contacto

Para cualquier consulta sobre esta política, abrir un issue en
<https://github.com/SimonChiabo/MacroPipeline/issues>.
