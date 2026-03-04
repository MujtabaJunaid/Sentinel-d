# Sentinel-D: Autonomous DevSecOps Pipeline

Sentinel-D is an autonomous DevSecOps pipeline that automatically detects, validates, and remediates security vulnerabilities in code repositories.

## Message Flow

```
GHAS alert → Azure Function → Service Bus → SRE Agent → Human Decision Gate
  → Historical DB lookup → NLP Pipeline (Dev A) → Patch Generator (Dev A)
  → Sandbox Validator → Safety Governor → PR/Issue/Escalation
  → Historical DB write
```

## Architecture

### Dev B Components (Infrastructure & Integration)
- **Azure Functions**: Webhook receiver for GHAS alerts with schema validation
- **SRE Agent**: KQL auto-generation, allowlist validation, telemetry classification (ACTIVE/DORMANT/DEFERRED)
- **Sandbox Validator**: Ephemeral Container Apps, test runner, SSIM visual regression
- **Safety Governor**: Four-tier action dispatch (Auto PR / Manual PR / Escalate / Reject)
- **Historical DB**: Cosmos DB write client, Azure AI Search indexing

### Dev A Components (ML/NLP)
- **NLP Pipeline**: spaCy NER, DistilBERT intent classifier, embedding generation.
  Link to models:
  1) https://huggingface.co/mojad121/spacy-classes-finetune/tree/main
  2) https://huggingface.co/mojad121/distill-bert-intent-classifer/tree/main 
- **Patch Generator**: FOUNDRY/RAG replay, four-section prompt design, composite scoring

### Shared Components
- **Historical DB**: Read client (Dev A), Write client (Dev B)
- **JSON Schemas**: Frozen interface contracts in `/shared/schemas/`

## Repository Structure

```
/shared/schemas/          # FROZEN - Interface contracts between Dev A and Dev B
/sre-agent/              # Dev B - Telemetry analysis and classification
/azure-functions/        # Dev B - Webhook receiver
/sandbox-validator/      # Dev B - Container App orchestration, SSIM visual regression
/safety-governor/        # Dev B - Action dispatch routing (Dev A owns scoring math)
/historical-db/          # Shared - Dev B owns write path, Dev A owns read path
/nlp-pipeline/           # Dev A - spaCy + DistilBERT pipeline
/patch-generator/        # Dev A - Patch generation and scoring
/demo/                   # Shared - Demo app with GHAS + App Insights
```

## Tech Stack

### Dev B Stack
- **Node.js**: Azure Functions, Service Bus consumers, GitHub Actions
- **Python**: SRE Agent, Sandbox Validator (scikit-image for SSIM)
- **Azure Services**: Functions, Service Bus, Container Apps, Cosmos DB, AI Search, Table Storage, Logic Apps, Application Insights
- **GitHub**: Issues, Labels, PR generation via Copilot Agent Mode
- **Puppeteer**: Visual regression screenshots
- **scikit-image**: SSIM computation
- **PIL**: Diff overlay generation

### Dev A Stack
- **Python**: spaCy, DistilBERT, PyTorch
- **Azure ML**: Managed endpoints for NLP pipeline
- **Azure AI**: FOUNDRY for patch generation

## Critical Rules

1. **KQL queries must pass allowlist validation** before execution
   - Allowed tables: `traces`, `requests`, `exceptions`, `dependencies`
   - Blocked operators: `externaldata`, `http_request`, `invoke`

2. **Historical DB write happens AFTER Safety Governor resolution** - never before

3. **Container Apps must tear down** after every sandbox run (no persistent compute cost)

4. **SSIM threshold must produce < 5% false positive rate**

5. **Audit log is append-only** (Azure Table Storage) - no updates, no deletes

6. **`sentinel/wont-fix` label writes ACCEPTED_RISK record** to Cosmos DB

## Getting Started

### Prerequisites
- Azure subscription with required services
- GitHub repository with GHAS enabled
- Node.js 18+ and Python 3.11+

### Installation

```bash
# Install dependencies for each component
cd azure-functions && pip install -r requirements.txt
cd ../sre-agent && pip install -r requirements.txt
cd ../sandbox-validator && pip install -r requirements.txt
cd ../safety-governor && pip install -r requirements.txt
cd ../historical-db && pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Azure credentials
AZURE_SUBSCRIPTION_ID=
SERVICE_BUS_NAMESPACE=
APP_INSIGHTS_WORKSPACE_ID=
COSMOS_DB_ENDPOINT=
AZURE_SEARCH_ENDPOINT=

# GitHub
GITHUB_TOKEN=
GITHUB_REPOSITORY=
```

### Deployment

```bash
# Deploy Azure Functions
az functionapp deploy --resource-group sentinel --name sentinel-webhook-receiver --src-path azure-functions

# Provision Cosmos DB
python historical-db/write_client.py provision_cosmos_db

# Provision Azure AI Search
python historical-db/write_client.py provision_azure_search
```

## Testing

```bash
# Run SRE Agent tests
cd sre-agent && pytest test_agent.py

# Run Sandbox Validator tests
cd sandbox-validator && pytest

# Run Safety Governor tests
cd safety-governor && pytest
```

## Interface Contracts

The interface between Dev A and Dev B is defined by JSON schemas in `/shared/schemas/`:

- **Dev A delivers**: `structured_context.json` (from NLP Pipeline)
- **Dev B delivers**: `validation_bundle.json` (from Sandbox Validator)

**These schemas are FROZEN** - changes require joint agreement.

## License

MIT
