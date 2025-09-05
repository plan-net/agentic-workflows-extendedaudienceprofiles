#!/bin/bash

# Set Langfuse API credentials
export LANGFUSE_PUBLIC_KEY="pk-lf-566bee02-8026-41f6-b8dd-26af9e25a161" # Set your langfuse public key
export LANGFUSE_SECRET_KEY="sk-lf-004d4784-c429-4ade-a1f5-0b25c4afc293" # Set your langfuse secret key

# Set direct Langfuse environment variables
export LANGFUSE_HOST="http://172.211.242.223:3000" # Set your langfuse host address whether self-hosted or managed host 

# Create Basic Auth token with Python
LANGFUSE_AUTH=$(python3 -c "
import base64
public_key = '$LANGFUSE_PUBLIC_KEY'
secret_key = '$LANGFUSE_SECRET_KEY'
auth = base64.b64encode(f'{public_key}:{secret_key}'.encode()).decode()
print(auth)
")

# Set OpenTelemetry environment variables
export OTEL_EXPORTER_OTLP_ENDPOINT="${LANGFUSE_HOST}/api/public/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${LANGFUSE_AUTH}"

# Extended Audience Profiles specific settings
export USE_LANGFUSE_PROMPTS="true"  # Enable Langfuse prompt loading
export LANGFUSE_ENV="production"    # Environment label for prompts

echo "Langfuse environment variables have been set."
echo "To use them in your current shell, source this script with:"
echo "source set_langfuse.sh" 

# Print all environment variables
echo ""
echo "Current Langfuse environment variables:"
echo "========================================"
echo "LANGFUSE_PUBLIC_KEY: $LANGFUSE_PUBLIC_KEY"
echo "LANGFUSE_SECRET_KEY: $LANGFUSE_SECRET_KEY"
echo "LANGFUSE_HOST: $LANGFUSE_HOST"
echo "OTEL_EXPORTER_OTLP_ENDPOINT: $OTEL_EXPORTER_OTLP_ENDPOINT"
echo "OTEL_EXPORTER_OTLP_HEADERS: $OTEL_EXPORTER_OTLP_HEADERS"
echo "USE_LANGFUSE_PROMPTS: $USE_LANGFUSE_PROMPTS"
echo "LANGFUSE_ENV: $LANGFUSE_ENV"
