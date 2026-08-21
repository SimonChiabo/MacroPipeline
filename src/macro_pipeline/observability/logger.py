import os

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from macro_pipeline.observability.redaction import install_redaction_filter


def setup_observability() -> trace.Tracer:
    """
    Configura OpenTelemetry y Structlog para inyectar IDs de traza y opcionalmente
    enviar telemetría a Grafana Cloud (o cualquier endpoint OTLP).
    Devuelve un tracer que puede usarse para agrupar operaciones.
    """
    # 1. Definir los metadatos de la aplicación
    resource = Resource(
        attributes={"service.name": "macro_pipeline", "service.version": "0.1.0"}
    )

    # 2. Iniciar el proveedor de trazas
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    # 3. Leer configuración OTLP del entorno (.env)
    # Por defecto Grafana Cloud usa /v1/traces para HTTP OTLP
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers_str = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")

    # Si hay un endpoint configurado, activamos la exportación
    if endpoint:
        headers = {}
        if headers_str:
            # Parsear "Authorization=Basic xxx,Header2=yyy"
            for pair in headers_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    headers[k.strip()] = v.strip()

        # Usar el exportador HTTP (Grafana Cloud lo soporta nativamente)
        exporter = OTLPSpanExporter(
            endpoint=endpoint
            if endpoint.endswith("/v1/traces")
            else f"{endpoint}/v1/traces",
            headers=headers,
        )
        # Procesamiento por lotes (recomendado para producción)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

    # 4. Procesador para Structlog: Inyecta `trace_id` y `span_id` en cada log
    def add_opentelemetry_spans(logger, method_name, event_dict):
        span = trace.get_current_span()
        if span.is_recording():
            ctx = span.get_span_context()
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
        return event_dict

    # 5. Aplicar la configuración global de Structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            add_opentelemetry_spans,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Si hay exportación, usamos JSON puro para que Loki/Grafana lo
            # parsee fácil. Si no, usamos ConsoleRenderer para que el
            # desarrollador lea los colores en la terminal.
            structlog.processors.JSONRenderer()
            if endpoint
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # 6. Redacción de credenciales en todo lo que salga por el logging estándar.
    # FRED y FMP exigen la key como query param, así que urllib3 la imprime
    # entera al reintentar. Sin esto, las keys llegan a Grafana.
    install_redaction_filter()

    return trace.get_tracer("macro_pipeline.tracer")
