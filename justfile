# Extended Audience Profiles - Kodosumi App

# Start everything
start:
    source .venv/bin/activate && ray start --head --disable-usage-stats
    @sleep 2
    source .venv/bin/activate && koco deploy -r
    @sleep 5
    source .venv/bin/activate && koco spool &
    @sleep 5
    source .venv/bin/activate && koco serve --register http://localhost:8001/-/routes

# Stop everything  
stop:
    source .venv/bin/activate && ray stop
    @pkill -f koco || true

# Run unit tests (mocked, fast)
test:
    source .venv/bin/activate && pytest tests/ -m "not integration"

# Run integration tests (real API calls)
test-integration:
    source .venv/bin/activate && pytest tests/ -m integration

# Run all tests
test-all:
    source .venv/bin/activate && pytest tests/