# 📖 Enterprise HR Copilot: Development & Architecture Guide

This document provides a comprehensive end-to-end technical guide for developers working on the **Enterprise HR Policy & Employee Support Agentic RAG Copilot**.

---

## 1. High-Level Architectural Flow

![Development Architecture](development_architecture.jpg)

```text
User Question
    │
    ▼
1. FastAPI Entrypoint (`POST /api/chat`)
    │
    ▼
2. Enterprise RBAC Validation (Keycloak JWT RS256)
    ├── Validates employee / hr_admin identity
    │
    ▼
3. Input Guardrails (NeMo Guardrails & Microsoft Presidio)
    ├── NeMo Guardrails: Blocks prompt injection, jailbreaks, toxic language
    ├── Presidio PII Masking: Redacts SSN, phone numbers, emails, credit cards
    │
    ▼
4. Fast-Path Redis Cache Lookup (<5ms)
    ├── Hit  ──→ Returns cached answer immediately (bypasses LLM)
    └── Miss ──→ Continues to Agentic Workflow
    │
    ▼
5. LangGraph Agentic RAG Controller
    ├── [Node: Route Question]
    │     ├── Direct Answer (Greetings / chit-chat)
    │     └── Policy Question (Triggers private KB retrieval)
    │
    ├── [Node: Retrieve Private KB]
    │     └── Queries Pinecone vector database with embeddings
    │
    ├── [Node: Grade Evidence]
    │     ├── GOOD ──→ Proceeds directly to Answer Generation
    │     └── WEAK ──→ Triggers external fallback
    │
    ├── [Node: Tavily Web Search] (Fallback Only)
    │     └── Queries public web for regional/legal public updates
    │
    ├── [Node: Grade Web Evidence]
    │     ├── GOOD ──→ Generates grounded web-sourced answer
    │     └── WEAK ──→ Enters Rewrite Loop
    │
    └── [Node: Rewrite Query]
          └── Rewrites ambiguous user query and retries private KB (max 2 retries)
    │
    ▼
6. LiteLLM Model Gateway
    ├── Primary: Google Gemini 2.5/3.8 Flash (speed & low cost)
    ├── Secondary: Groq Llama 3.3 70B / 120B (deep reasoning)
    └── Fallback: OpenAI GPT-4o-mini
    │
    ▼
7. Long-Term Semantic Memory (LangMem)
    └── Extracts persistent employee facts (Department, Location, Role)
    │
    ▼
8. Response Dispatcher
    ├── Updates Redis Cache (TTL: 300s)
    ├── Logs audit trail to database
    └── Sends JSON response with citations & decision path to Web UI
```

---

## 2. Core Modules Breakdown

### A. Agentic Workflow Engine (`app/rag/`)
- **`state.py`**: Defines the shared state dataclass (`GraphState`) passing through LangGraph nodes:
  - `question`: Current processed question.
  - `documents`: Retrieved context chunks from Pinecone.
  - `web_results`: Retrieved search results from Tavily.
  - `generation`: Generated grounded text answer.
  - `route`: Identified intent (`kb`, `direct`).
  - `evidence_grade`: Quality grade (`good`, `weak`).
  - `retry_count`: Loop counter preventing infinite query rewrites.
  - `steps`: Audit decision breadcrumbs shown to end-users.
- **`workflow.py`**: Compiles the cyclical directed acyclic graph (DAG) using `StateGraph`. Defines routers, graders, web fallback nodes, and generation nodes.
- **`vectorstore.py`**: Manages semantic chunk storage in **Pinecone** using OpenAI `text-embedding-3-small`.

### B. Security & AI Guardrails (`app/core/`)
- **`guardrails/nemo_engine.py`**: Evaluates user prompts against Colang definitions (`rails.co`). Blocks prompt injection, jailbreaks, roleplaying attacks, and HR advice fabrication.
- **`security/pii.py`**: Integrates **Microsoft Presidio Analyzer & Anonymizer**. Replaces sensitive PII tokens (e.g. `<PHONE_NUMBER>`, `<US_SSN>`) before they leave the application perimeter.
- **`auth.py`**: Validates Keycloak JWT RS256 signatures, extracting roles:
  - `employee`: Standard query permissions.
  - `hr_admin`: Allowed to upload documents (`POST /api/ingest`) and view audit logs (`GET /api/audit`).

### C. Performance & Memory Services (`app/services/`)
- **`cache.py`**:
  - SHA-256 query caching with configurable TTL (default 300 seconds).
  - Sliding-window rate limiter (30 requests/minute per client).
  - Multi-turn conversation session history.
  - Auto-invalidation on knowledge ingestion (`invalidate_cache_pattern`).
- **`memory.py`**: Implements **LangMem** persistent memory. Scans user messages for profile metadata (department, office, tier) and persists facts across sessions.
- **`gateway.py`**: Unified LLM proxy using **LiteLLM**. Manages failover routing and calculates real-time cost in cents based on prompt and completion token counts.
- **`audit.py`**: Records query timestamp, user ID, decision path, model used, latency, and token cost into an audit database.

### D. DevOps & Cloud Infrastructure Layer (`terraform/`, `k8s/`, `.github/`)
- **`terraform/`**: Infrastructure as Code (IaC) that provisions:
  - Custom Multi-AZ VPC (`10.0.0.0/16`) with Public and Private Subnets across availability zones.
  - EKS Cluster v1.30 with CloudWatch audit logging.
  - EKS Managed Node Groups with auto-scaling (2 to 6 nodes) placed in private subnets.
  - OIDC Identity Provider & IRSA for AWS Load Balancer Controller and EBS CSI Driver.
  - Amazon ECR Private Repository with automated vulnerability scan and 30-image lifecycle cleanup.
- **`k8s/`**: Production Kubernetes manifests:
  - `deployment.yaml`: Enforces non-root least-privilege security (`runAsUser: 1001`), readiness/liveness probes, and rolling updates (`maxUnavailable: 0`).
  - `hpa.yaml`: Horizontal Pod Autoscaler scaling pods from 2 to 10 replicas based on CPU/RAM utilization.
  - `ingress.yaml`: Provisions internet-facing AWS Application Load Balancer (ALB).
  - `redis.yaml`: In-cluster Redis deployment, persistent volume, and service.
- **`Dockerfile`**: Hardened multi-stage build creating an unprivileged user `appuser:appgroup` (UID 1001:1001).
- **`.github/workflows/deploy-eks.yml`**: Fully automated CI/CD pipeline running Pytest, Aqua Security Trivy image scan, ECR push, and EKS rolling rollout.

---

## 3. Local Development Lifecycle

### Setting Up Development Environment
```bash
# 1. Clone repository & enter workspace
git clone <repo-url>
cd Enterprise-HR-Policy-Employee-Support-Agentic-RAG-Copilot

# 2. Create and activate Python 3.11+ venv
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Linux / macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment configuration
cp .env.example .env
# Edit .env with your OPENAI_API_KEY, GEMINI_API_KEY, PINECONE_API_KEY
```

### Ingesting Sample Knowledge Base
```bash
python ingest_sample_kb.py
```

### Running Test Suite
```bash
python -m pytest tests/ -v
```

### Running Server Locally
```bash
python run.py
```
Application UI will be accessible at `http://127.0.0.1:8080` and Swagger docs at `http://127.0.0.1:8080/docs`.

---

## 4. DevOps & Continuous Delivery Lifecycle

```text
Developer Git Push
       │
       ▼
GitHub Actions CI
  ├── 1. Automated Testing: 15 pytest suites (Ragas, PII, Guardrails, Redis)
  ├── 2. Docker Multi-Stage Build: Tagged with git commit SHA
  ├── 3. Trivy Security Scan: Vulnerability scan for CRITICAL & HIGH CVEs
  └── 4. ECR Push: Pushes verified image to Amazon ECR
       │
       ▼
GitHub Actions CD
  ├── 1. Authenticate to AWS EKS via IAM
  ├── 2. Dynamic Tag Replacement in deployment.yaml
  ├── 3. Zero-Downtime Rollout: kubectl apply -k k8s/
  └── 4. Rollout Verification: kubectl rollout status deployment/hr-copilot-deployment
```

