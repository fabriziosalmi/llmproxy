import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from typing import Optional

logger = logging.getLogger(__name__)

class TraceManager:
    _tracer = None

    @classmethod
    def initialize(cls, service_name: str = "llmproxy", otlp_endpoint: Optional[str] = None):
        """Initializes OpenTelemetry tracing."""
        provider = TracerProvider()
        
        # Console Exporter (Always on for now)
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

def start_span(name: str):
    """Context manager decorator for spans."""
    return TraceManager.get_tracer().start_as_current_span(name)
