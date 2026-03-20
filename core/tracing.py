"""
LLMPROXY — OpenTelemetry Tracing & Observability (Session 7 Enhanced)

Unified OTEL setup: traces + metrics + logs via OTLP.
Supports Sentry integration for exception tracking.
"""

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from typing import Optional

logger = logging.getLogger(__name__)


class TraceManager:
    _tracer = None
    _sentry_initialized = False

    @classmethod
    def initialize(
        cls,
        service_name: str = "llmproxy",
        otlp_endpoint: Optional[str] = None,
        console_export: bool = True,
        sentry_dsn: Optional[str] = None,
    ):
        """Initializes OpenTelemetry tracing with optional Sentry."""
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # Console Exporter
        if console_export:
            console_exporter = ConsoleSpanExporter()
            provider.add_span_processor(BatchSpanProcessor(console_exporter))

        # OTLP Exporter (If endpoint provided)
        if otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"OTLP Exporter initialized for endpoint: {otlp_endpoint}")

        trace.set_tracer_provider(provider)
        cls._tracer = trace.get_tracer(service_name)
        logger.info(f"OpenTelemetry Tracing initialized for service: {service_name}")

        # 7.3: Sentry Integration
        if sentry_dsn:
            cls._init_sentry(sentry_dsn, service_name)

    @classmethod
    def _init_sentry(cls, dsn: str, service_name: str):
        """Initialize Sentry for exception tracking and performance monitoring."""
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.aiohttp import AioHttpIntegration

            sentry_sdk.init(
                dsn=dsn,
                traces_sample_rate=0.1,  # Sample 10% of transactions
                profiles_sample_rate=0.05,
                environment="production",
                release=f"llmproxy@{service_name}",
                integrations=[
                    FastApiIntegration(transaction_style="endpoint"),
                    AioHttpIntegration(),
                ],
                # Don't send PII
                send_default_pii=False,
                # Drop high-volume events
                before_send=cls._sentry_before_send,
            )
            cls._sentry_initialized = True
            logger.info("Sentry initialized for exception tracking")
        except ImportError:
            logger.warning("sentry-sdk not installed, Sentry integration disabled")
        except Exception as e:
            logger.error(f"Sentry initialization failed: {e}")

    @staticmethod
    def _sentry_before_send(event, hint):
        """Filter noisy events before sending to Sentry."""
        # Drop expected errors (rate limits, auth failures)
        if 'exc_info' in hint:
            exc_type = hint['exc_info'][0]
            if exc_type and exc_type.__name__ in ('HTTPException',):
                return None
        return event

    @classmethod
    def get_tracer(cls):
        if cls._tracer is None:
            cls._tracer = trace.get_tracer("llmproxy-fallback")
        return cls._tracer

    @classmethod
    def instrument_app(cls, app):
        """Instruments a FastAPI app."""
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI application instrumented with OpenTelemetry")

    @classmethod
    def capture_exception(cls, exc: Exception):
        """Forward exception to Sentry if initialized."""
        if cls._sentry_initialized:
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass


def start_span(name: str):
    """Context manager decorator for spans."""
    return TraceManager.get_tracer().start_as_current_span(name)
