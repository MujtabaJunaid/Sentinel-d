# Sentinel-D — Copilot Instructions

## WHO I AM
I am **Dev B** on this project. My domain is **Infrastructure & Integration**.

**Never suggest work that belongs to Dev A.** Dev A owns:
- spaCy NER model training and evaluation
- DistilBERT intent classifier training and evaluation
- NLP Pipeline orchestrator and Azure ML endpoint
- Historical DB read client and embedding generation logic
- RAG replay path logic and structural mismatch detection
- Patch Generator prompt architecture and four-section prompt design
- Composite confidence scoring formula and signal computation
- Safety Governor scoring function (the math — I own the routing that acts on the score)

**My domain — what I build, own, and am responsible for:**
- Azure Function webhook receiver + schema validation
- Azure Service Bus namespace, queue, dead-letter handler
- SRE Agent: KQL auto-generation, allowlist validation, App Insights query, three-way classification (ACTIVE / DORMANT / DEFERRED)
- Human-in-the-Loop Decision Gate: GitHub Issue template, label-event GitHub Actions handlers (fix-now / defer / wont-fix)
- 72-hour auto-escalation Logic App
- Deferred backlog queue (Azure Table Storage) + daily re-scan Logic App + weekly digest
- Azure Cosmos DB provisioning, container setup, partitioning, indexes, write client
- Azure AI Search vector index schema, embedding field config, query API wiring
- Historical DB write path (called from Safety Governor after resolution)
- Azure Container App GitHub Action: spin-up, dependency install, test runner, teardown
- SSIM visual regression module: Puppeteer screenshots, scikit-image SSIM, PIL diff overlays
- Test runner integration: Jest/Pytest output → structured test_results.json
- Sandbox Validator orchestration: candidate_patch.json → validation_bundle.json
- Safety Governor routing: four-tier action dispatch, PR generation via GitHub Copilot Agent Mode, GitHub Issue creation, PagerDuty escalation, audit log write
- Azure Table Storage audit log (append-only, all Safety Governor decisions)
- Historical DB write after resolution (Cosmos DB record creation)
- Demo app GHAS configuration, Application Insights telemetry seeding
- Retry logic and dead-letter handling across all Azure service calls
- Stress testing and infrastructure integration tests

---

## THE INTERFACE CONTRACT WITH DEV A
The ONLY formal handoff between me and Dev A is at two JSON schema boundaries.
These schemas live in `/shared/schemas/` and are **frozen** — I must not change them
without explicit joint agreement with Dev A.

**Dev A delivers to me:**
- `structured_context.json` from the NLP Pipeline endpoint (Azure ML managed endpoint)
  I consume this in the Patch Generator and Safety Governor.

**I deliver to Dev A:**
- `validation_bundle.json` from the Sandbox Validator
  Dev A's Safety Governor scoring function consumes this.

If I need a schema change, I flag it at the daily sync. I do NOT make unilateral changes.

---

## PROJECT OVERVIEW
Sentinel-D is an autonomous DevSecOps pipeline:
GHAS alert → telemetry validation → Historical DB lookup → NLP pipeline →
patch generation → sandbox validation → Safety Governor → PR

**Full message flow:**
```
webhook_payload.json
  → [Azure Function validates + writes to Service Bus]
telemetry_classification.json
  → [SRE Agent: ACTIVE continues | DORMANT → Human Decision Gate]
historical_match.json
  → [Historical DB lookup: EXACT/SEMANTIC/NO_MATCH]
structured_context.json       ← DEV A DELIVERS THIS
  → [Patch Generator: FOUNDRY call or RAG replay]
candidate_patch.json
  → [Sandbox Validator: Container App + tests + SSIM]
validation_bundle.json        ← I DELIVER THIS TO DEV A
  → [Safety Governor: score → tier → action]
  → [Historical DB write: Cosmos DB record]
```

---

## REPO STRUCTURE
```
/shared/schemas/          ← FROZEN — never modify without joint agreement
  webhook_payload.json
  telemetry_classification.json
  historical_match.json
  human_decision.json
  structured_context.json
  candidate_patch.json
  validation_bundle.json
  historical_db_record.json

/sre-agent/               ← MY COMPONENT
/azure-functions/         ← MY COMPONENT
/sandbox-validator/       ← MY COMPONENT
/safety-governor/         ← MY COMPONENT (routing only — Dev A owns scoring math)
/historical-db/           ← SHARED (I own write path + Azure setup, Dev A owns read client)
/demo/                    ← SHARED

/nlp-pipeline/            ← DEV A ONLY
/patch-generator/         ← DEV A ONLY
```

---

## TECH STACK (my side)
- **Node.js**: Azure Functions, Service Bus consumers, GitHub Actions scripts
- **Python**: Sandbox Validator (SSIM module uses scikit-image), SRE Agent
- **Azure Functions** (Consumption Plan): webhook receiver
- **Azure Service Bus**: queue + dead-letter + multiple subscription topics
- **Azure Application Insights + KQL**: telemetry queries via SRE Agent
- **Azure Cosmos DB** (Core API, serverless): Historical DB primary store
- **Azure AI Search**: vector index for semantic CVE similarity search
- **Azure Container Apps**: ephemeral sandbox environments
- **Azure Table Storage**: deferred backlog queue + audit log (append-only)
- **Azure Logic Apps**: 72-hour auto-escalation timer + daily backlog re-scan
- **GitHub Actions**: Container App spin-up workflow + label-event handlers
- **GitHub API**: Issue creation, label listener, PR generation via Copilot Agent Mode
- **Puppeteer**: headless Chrome screenshot capture for visual regression
- **scikit-image**: SSIM computation
- **PIL**: diff overlay image generation

---

## CRITICAL RULES I MUST FOLLOW
1. KQL strings generated by the SRE Agent must ALWAYS pass allowlist validation before
   execution. Permitted tables: traces, requests, exceptions, dependencies.
   Blocked operators: externaldata, http_request, invoke.
2. The Historical DB write happens AFTER every Safety Governor resolution — never before.
3. Azure Service Bus dead-letter queue must be configured on Day 1. No silent message drops.
4. Container App must tear down after every sandbox run. No persistent compute cost.
5. SSIM threshold must produce < 5% false positive rate on clean patches.
6. The audit log is append-only. No updates, no deletes, ever.
7. The `sentinel/wont-fix` label handler must write an ACCEPTED_RISK record to Cosmos DB
   so the pipeline never re-alerts on that CVE+file combination.
8. Azure subscription spend must not exceed $20 total across the full 14-day build.
   Use Consumption/serverless tiers, tear down ephemeral resources immediately,
   avoid long-running compute, and prefer local testing over deployed calls when possible.

---

## CODING STYLE PREFERENCES
- Node.js: async/await, no callbacks, explicit error handling with try/catch
- Python: type hints on all function signatures, docstrings on public functions
- Azure SDK: always use DefaultAzureCredential in production code paths
- Tests: Jest for Node.js, pytest for Python, async fixtures for Azure SDK mocking
- No console.log in production paths — use structured logging to App Insights
- All Azure resource names use environment variables, never hardcoded strings
---

## GIT WORKFLOW — MANDATORY
- NEVER run git commands on my behalf
- NEVER run: git add, git commit, git push, git merge, git rebase, or any git command
- When changes are ready to commit, STOP and tell me:
  1. Which files to stage (exact paths)
  2. The commit message to use
  3. Wait for me to confirm the commit is done before continuing
- Format the instruction clearly, like this:

  📝 Ready to commit. Please run:
      git add <file1> <file2>
      git commit -m "type: description"
  Let me know when done and I'll continue.

## BRANCH WORKFLOW
- I always work on feature branches, never directly on main
- Branch naming: feat/day{N}-{component}, fix/{description}, infra/{component}
- My fork remote is: origin (BilalAsifB/sentinel-d)
- Dev A's upstream repo is: upstream (MujtabaJunaid/Sentinel-d)
- PRs go from my fork branch → upstream main
- I never merge my own PRs — Dev A reviews and merges
