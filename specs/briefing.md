
## Purpose

This project will become a proof of concept to implement the Masumi Agent-2-Agent Protocol.

We first will focus implementing one simple example, where we will call the "Avanced Web Research" Agent with a simple prompt we give as input in the kodosumi form over the Masumi Protokol and only return 1:1 the markdown result we get from this job, with some enrichment of meta-data from the A2A interaction.

## Masumi A2A Implementation

I want to develop a custom class and tool for this entire Masumi A2A interaction, which uses the Masumi Python SDK (https://github.com/masumi-network/pip-masumi - can be installed via 'pip install masumi') in order to faciliate the purchasing of the the other agent.

The tool shoud sit in a file called masumi.py next to query.py and agent.py.
It should be so generic, that it will be easy to build custom tools for different ai agents frameworks around this. We will start with the openai agent sdk for this poc for now. but building the openai agent sdk tool should sit in agent.py and the entiere Masumi A2A business logic sits in masumi.py - completely framework agnostic.

## I want the following features to be part of this:

- loading configuration from a masumi.yaml file, which list available agents, their endpoint url, descriptions and gives a budget to spent on those agents. We need to comeup with a good idea on how to structure this masumi.yaml file

- the masumi a2a tool should make it very easy to check the availabiilty of an agent on the Masumi Network, start a job, makek the purchase payment, check the status and in the end return the result.

- after the job has been started we need to pull constantly for status update, to finally get the result. we need to find a good non-blocking way to impelemnt this, leveraging our underlying ray infrastructure, which comes with kodosumi as part of this project

Please see and understand this agentic service api documentation:
https://docs.masumi.network/documentation/technical-documentation/agentic-service-api

## Masumi Pyton SDK

Please understanding this quickstart guide for the masumi python skd:

from masumi import Config, Agent, Payment, Purchase
import asyncio

# Configure API credentials
config = Config(
    registry_service_url="https://registry.masumi.network/api/v1",
    registry_api_key="your_registry_api_key",
    payment_service_url="https://payment.masumi.network/api/v1",
    payment_api_key="your_payment_api_key"
)

# 1. Register agent (one-time setup)
agent = Agent(
    name="My AI Service",
    config=config,
    description="AI agent for processing tasks",
    # ... other parameters
)
await agent.register()

# 2. Create payment request (seller)
payment = Payment(
    agent_identifier="your_agent_id",
    config=config,
    identifier_from_purchaser="buyer_hex_id"
)
result = await payment.create_payment_request()

# 3. Create purchase (buyer)
purchase = Purchase(
    config=config,
    blockchain_identifier=result["data"]["blockchainIdentifier"],
    # ... payment details
)
await purchase.create_purchase_request()

## General Adivse

Let's start small.
Don't overengineer this.
We will add more and more features, error handling, exception etc. later
Follow a test-driven development

