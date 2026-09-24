# Project Proposal: LLM Security Interceptor & Analytics Pipeline

## 1. Executive Summary
This project aims to deploy a low-latency, intermediary Machine Learning service within our ERP product. This service will sanitize incoming user prompts for security (specifically prompt injection detection) and asynchronously categorize usage data for product analytics before interacting with our AWS Bedrock Anthropic LLM.

## 2. Technology Stack
*   **Core LLM Engine:** AWS Bedrock (Anthropic models)
*   **Security Model:** `protectai/deberta-v3-base-prompt-injection` (Apache 2.0 License - Commercial use approved)
*   **Analytics Models:** Hugging Face `transformers` (Zero-shot classification via `facebook/bart-large-mnli`)
*   **Application Framework:** Python / FastAPI
*   **Local Infrastructure:** Docker (Containerization) & Floci (Local AWS emulation)
*   **Production Infrastructure:** AWS ECS (Fargate), ECR, SQS, Bedrock

## 3. Project Phases

### Phase 1: The ML Security Layer (Local Development)
*   **Goal:** Build the core Python API to evaluate prompts for malicious intent before they reach the LLM.
*   **Implementation:** 
    *   Create a lightweight FastAPI Python application to serve as the interceptor.
    *   Integrate the Apache 2.0 licensed `deberta-v3-base-prompt-injection` model using the Hugging Face `transformers` pipeline.
    *   Establish a strict latency budget (<100ms) for the synchronous security check to maintain a seamless user experience in the ERP.
*   **Outcome:** A REST endpoint that accepts a prompt and returns a boolean `is_safe` flag.

### Phase 2: Local AWS Infrastructure & Containerization
*   **Goal:** Replicate the production AWS architecture locally and package the application for consistent deployment.
*   **Implementation:** 
    *   **Docker:** Write a `Dockerfile` for the FastAPI application to handle the installation of dependencies like PyTorch and Transformers. Use Docker Compose to orchestrate the service.
    *   **Floci:** Spin up Floci alongside the Docker container to emulate AWS infrastructure.
    *   Configure a local SQS (Simple Queue Service) queue in Floci. Upon processing a prompt, the interceptor will push the prompt, the LLM's response, and the generated GraphQL query to this SQS queue for asynchronous processing.
*   **Outcome:** A fully containerized local environment interacting with emulated AWS services, requiring zero cloud spend.

### Phase 3: The Async Analytics Pipeline
*   **Goal:** Process historical and ongoing prompts in the background to understand user behavior and intent.
*   **Implementation:**
    *   Build a secondary Python background worker that polls the Floci SQS queue.
    *   Implement zero-shot classification to tag prompts into specific ERP categories (e.g., HR, Finance, Inventory).
    *   Parse the LLM-generated GraphQL queries using `graphql-core` to extract and log the specific database entities the user interacted with.
    *   Store these structured logs in a local PostgreSQL database or a Floci-emulated DynamoDB table.
*   **Outcome:** A robust dataset mapping raw user prompts to established ERP domains and specific database schemas.

### Phase 4: Production Deployment to AWS
*   **Goal:** Migrate from the local Docker/Floci environment to live AWS infrastructure.
*   **Implementation:**
    *   Push the finalized Docker image to AWS ECR (Elastic Container Registry).
    *   Deploy the container using AWS ECS (Elastic Container Service) with AWS Fargate for serverless compute, removing the need to manage underlying EC2 instances.
    *   Swap the local Floci SQS endpoints in the environment variables for the live AWS SQS production endpoints.
    *   Connect the live interceptor securely to the AWS Bedrock Anthropic stream.
*   **Outcome:** A highly available, secure LLM integration live in the production ERP environment, complete with observability and analytics.