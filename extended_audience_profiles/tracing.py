"""
Langfuse tracing configuration for Extended Audience Profiles
"""
import os
import logfire
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)


def scrubbing_callback(match: logfire.ScrubMatch):
    """Preserve Langfuse and execution attributes from scrubbing."""
    # Preserve session ID
    if (
        match.path == ("attributes", "langfuse.session.id")
        and match.pattern_match.group(0) == "session"
    ):
        return match.value
    
    # Preserve trace output
    if match.path == ("attributes", "langfuse.trace.output"):
        return match.value
    
    # Preserve other langfuse trace attributes
    if match.path[0] == "attributes" and match.path[1].startswith("langfuse."):
        return match.value
    
    # Preserve execution IDs
    if match.path[0] == "attributes" and "execution_id" in match.path[1]:
        return match.value


def setup_langfuse_tracing():
    """Configure Langfuse tracing via OpenTelemetry"""
    
    print(f"[TRACE_SETUP] setup_langfuse_tracing() called")
    print(f"[TRACE_SETUP] Process ID: {os.getpid()}")
    
    # Check if tracer provider is already set
    try:
        from opentelemetry import trace as otel_trace
        current_provider = otel_trace.get_tracer_provider()
        print(f"[TRACE_SETUP] Current tracer provider: {type(current_provider)}")
    except Exception as e:
        print(f"[TRACE_SETUP] Error checking tracer provider: {e}")

    # CRITICAL: Since logfire with send_to_logfire=False doesn't set up exporters,
    # we need to manually configure OTLP export to Langfuse
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
    headers = os.getenv('OTEL_EXPORTER_OTLP_HEADERS')
    
    print(f"[TRACE_SETUP] OTLP_ENDPOINT: {'SET' if endpoint else 'NOT SET'}")
    print(f"[TRACE_SETUP] OTLP_HEADERS: {'SET' if headers else 'NOT SET'}")
    
    if endpoint and headers:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        
        print(f"[TRACE] Configuring OTLP export to: {endpoint}")
        
        # Parse headers
        header_dict = {}
        if headers.startswith("Authorization="):
            header_dict["Authorization"] = headers.replace("Authorization=", "")
        
        # Create OTLP exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=endpoint + "/v1/traces" if not endpoint.endswith("/v1/traces") else endpoint,
            headers=header_dict
        )
        
        # Create provider with resource
        resource = Resource.create({
            "service.name": "extended_audience_profiles",
            "service.version": "0.6.0",
        })
        
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        
        # Set as global provider BEFORE logfire configuration
        otel_trace.set_tracer_provider(provider)
        print("[TRACE] OTLP exporter configured successfully")
    else:
        print("[TRACE_SETUP] Skipping OTLP setup - endpoint or headers not set")
    
    # Configure logfire instrumentation with custom scrubbing
    logfire.configure(
        service_name='extended_audience_profiles',
        send_to_logfire=False,  # Only send to Langfuse
        scrubbing=logfire.ScrubbingOptions(callback=scrubbing_callback),
    )
    
    # Instrument OpenAI Agents SDK
    logfire.instrument_openai_agents()
    
    print("✅ Langfuse tracing configured successfully")
    return True
