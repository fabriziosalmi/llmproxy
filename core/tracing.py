from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
import logging

logger = logging.getLogger(__name__)

def setup_tracing(service_name: str = "llm-proxy"):
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)

tracer = setup_tracing()

class TraceManager:
    @staticmethod
    def start_span(name: str):
        return tracer.start_as_current_span(name)
