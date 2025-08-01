# Extended Audience Profiles

A kodosumi-based AI service for generating extended audience profiles with citations using Exa.ai's web search capabilities.

> **Compatibility**: Optimized for kodosumi v0.9.3

## Features

- **Direct Answers**: Get specific answers to factual questions (e.g., "What was Deutsche Bahn's revenue in 2024?" → "26.2 Billion Euro")
- **Detailed Summaries**: Comprehensive responses for open-ended questions (e.g., "What are Millennials expecting from insurance products?")
- **Source Citations**: All answers include references to original sources
- **Markdown Format**: Clean, formatted responses ready for display

## Requirements

- Python 3.12+ (managed with pyenv recommended)
- kodosumi v0.9.3
- Ray cluster
- Exa.ai API key

## Quick Start

1. **Set up Python environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .
   ```

2. **Set your Exa.ai API key**:
   ```bash
   echo "EXA_API_KEY=your_api_key_here" > .env
   ```

3. **Configure kodosumi deployment** (copy and update with your API key):
   ```bash
   cp data/config/extended_audience_profiles.yaml.example data/config/extended_audience_profiles.yaml
   # Edit data/config/extended_audience_profiles.yaml and replace <-- your-exa-api-key --> with your actual key
   ```

4. **Start Ray and deploy the service**:
   ```bash
   source .venv/bin/activate
   ray start --head --disable-usage-stats
   koco deploy -r
   koco spool &  # REQUIRED: Starts the execution spooler
   koco serve --register http://localhost:8001/-/routes
   ```

5. **Access the admin panel**:
   Open `http://localhost:3370` in your browser to access the kodosumi admin interface.

6. **Test the service**:
   ```bash
   source .venv/bin/activate
   pytest tests/ -m "not integration"  # Run unit tests
   ```

The service will be deployed at `http://localhost:8001/extended_audience_profiles`

## Usage

### Via Kodosumi Admin Panel
Access the beautiful web interface at `http://localhost:3370` after running `just start`.

### Via API
Send a POST request to the service endpoint:

```bash
curl -X POST http://localhost:8001/extended_audience_profiles/ \
     -H "Content-Type: application/json" \
     -d '{"question": "What is the capital of France?"}'
```

## Architecture

- **Framework**: Kodosumi (Ray + FastAPI)
- **AI Provider**: Exa.ai /answer endpoint for web search and answer generation
- **Deployment**: Ray-based distributed processing
- **Response Format**: Markdown with embedded citations

### Exa.ai Integration

This service uses Exa.ai's new `/answer` endpoint which provides AI-generated answers with web search capabilities. The endpoint uses OpenAI's client interface pattern:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.exa.ai",
    api_key="your_exa_api_key"
)

completion = client.chat.completions.create(
    model="exa-pro",
    messages=[{"role": "user", "content": "Your question"}],
    extra_body={"text": True}
)
```

For more details, see the [Exa.ai /answer documentation](https://docs.exa.ai/reference/answer).

## Development

### Available Commands

- `just start` - Start full service (Ray + kodosumi deployment + spooler + admin panel)
- `just stop` - Stop all services
- `just test` - Run unit tests
- `just test-integration` - Run integration tests (requires API key)
- `just test-all` - Run all tests

### Manual Setup (Alternative)

If you prefer manual control over each step:

```bash
source .venv/bin/activate

# Start Ray cluster
ray start --head --disable-usage-stats

# Deploy the application
koco deploy -r

# Check deployment status
koco deploy -s

# Start execution spooler (REQUIRED for executions to run)
koco spool &

# Start admin panel
koco serve --register http://localhost:8001/-/routes

# Stop everything
ray stop
```

### Project Structure

```
extended-audience-profiles/
├── extended_audience_profiles/
│   ├── __init__.py
│   ├── agent.py              # Core AI agent logic  
│   └── query.py              # Kodosumi service wrapper
├── tests/
│   ├── __init__.py
│   └── test_agent.py         # Unit and integration tests
├── data/config/              # Kodosumi deployment configs
│   ├── config.yaml
│   ├── extended_audience_profiles.yaml.example
│   └── extended_audience_profiles.yaml (create from example)
├── .env                      # API keys
├── .env.example             # Environment template
├── justfile                  # Task runner
├── pytest.ini              # Test configuration
└── pyproject.toml           # Dependencies
```

## Testing

The project includes comprehensive test coverage:

- **Unit Tests**: Fast, mocked tests for core logic (`just test`)
- **Integration Tests**: Real API tests with Exa.ai (`just test-integration`)
- **All Tests**: Complete test suite (`just test-all`)

## Deployment

For production deployment with kodosumi:

1. Configure deployment in `data/config/config.yaml`
2. Deploy using the built-in Ray Serve integration
3. Monitor via Ray dashboard at `http://localhost:8265`

## License

This project follows the license terms of the kodosumi framework.