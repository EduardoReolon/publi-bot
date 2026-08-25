# 🤖 PubliBot: AI-Driven SEO Content Orchestrator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django](https://img.shields.io/badge/Django-Multi--Tenant-092E20?logo=django)](https://www.djangoproject.com/)
[![Celery](https://img.shields.io/badge/Celery-Distributed_Task_Queue-37814A?logo=celery)](https://docs.celeryproject.org/)
[![Ollama](https://img.shields.io/badge/Local_LLM-Ollama-white?logo=ollama)](https://ollama.com/)

> **A multi-tenant SaaS architecture that bridges the gap between raw scientific data and SEO-optimized web content using advanced Vision-RAG and decoupled local LLM inference.**

## 🎯 The Problem It Solves

Most AI content generators produce generic, hallucinated text that penalizes websites in modern search engines (Google's E-E-A-T guidelines). When trying to ground AI with real documents (RAG), standard PDF parsers fail at complex scientific layouts, and injecting random chunks of text into an LLM causes the **"Frankenstein Effect"** (paragraphs contradicting each other).

## 💡 The PubliBot Solution

PubliBot is engineered to generate highly authoritative **Pillar Articles** and **Q&A responses**. It mitigates hallucinations and API costs through a meticulously designed distributed architecture:

1. **Decoupled Compute:** The lightweight SaaS core runs in the cloud, while heavy ML inference (LLMs, Vision Parsers, Stable Diffusion) runs cost-free on a Local GPU Worker connected via a secure Tailscale tunnel.
2. **Vision-Based Data Ingestion:** Bypasses flawed PDF text extractors. It uses Vision Models (`Docling`/`MinerU`) to "see" scientific papers and convert dual-column, messy PDFs into perfectly structured Markdown.
3. **Summary Indexing RAG:** Instead of blind chunking, the system isolates high-value sections (Abstracts and Conclusions) into "Super Chunks." 
4. **Anti-Frankenstein Filter:** Before drafting, the LLM reads multiple retrieved summaries to build a logical "Consensus Thesis." The final article is written based on this thesis, injecting a single, highly authoritative canonical SEO backlink.

## 🏗️ System Architecture

The project is split into three main operational domains:

```mermaid
graph TD
    subgraph Cloud SaaS (Django / PostgreSQL)
        A[Web Dashboard] --> B(Tenant Router)
        B --> C[(PostgreSQL + pgvector)]
        B --> D[Celery Task Queue]
    end

    subgraph Local GPU Worker (Private Network)
        D -- Async Tasks --> E[Local Celery Worker]
        E --> F[Docling / MinerU Vision]
        E --> G[Ollama: Llama 3 / Qwen]
        F -- Structured Markdown --> D
        G -- Drafted Content --> D
    end

    subgraph End-Nodes (Client Websites)
        H[WordPress / Django Sites]
        D -- Push via Cron --> H
        H -- Pull Q&A Context --> A
    end

```

## ✨ Core Engineering Features

* **Stateful & Resilient Queuing:** Long-running LLM chains are broken down into stateful Celery tasks. If the Local GPU worker loses connection or shuts down, the workflow gracefully pauses and resumes from the exact stopped node when the hardware is back online.
* **Human-in-the-Loop (HITL):** Enforces mandatory human approval for both Content Generation and Data Ingestion, ensuring YMYL (Your Money or Your Life) compliance.
* **Smart Storage:** Retains full, GZIP-compressed Markdown versions of parsed documents in the relational database, while pushing semantic Super Chunks to `pgvector`.
* **Idempotent Ingestion:** Prevents vector database pollution by generating unique composite hashes `[Title + Author + Year]` for every uploaded source.
* **Dynamic Cron Engine:** Manages independent publication schedules (crawl-budget optimization) for each tenant.

## 🛠️ Tech Stack

* **Backend:** Python, Django, Django-Tenants, Django REST Framework.
* **Asynchronous Engine:** Celery, Redis.
* **Database:** PostgreSQL, `pgvector` (Vector similarity search).
* **AI & Inference:** Ollama (Local LLMs), Stable Diffusion, Docling (Document Layout Analysis).
* **Infrastructure:** Nginx, Gunicorn, Systemd (Unix Sockets).

## 🚀 Getting Started

**1. Clone and setup the environment:**

```bash
git clone [https://github.com/yourusername/publi_bot.git](https://github.com/yourusername/publi_bot.git)
cd publi_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

```

**2. Environment Variables:**
Create a `.env` file in the root directory. Configure your Postgres credentials, Redis URL, and Node API Keys.

**3. Run the Cloud Services (Terminal 1):**

```bash
python manage.py migrate_schemas
python manage.py runserver

```

**4. Start the Local GPU Worker (Terminal 2):**
Ensure Ollama is running (`ollama serve`), then start the Celery worker:

```bash
celery -A core worker -l INFO --concurrency=1

```

## 📂 Project Structure

* `/core` - Django root, configurations, and tenant routing.
* `/docs` - Deep-dive architectural documentation (`ARCHITECTURE.md`).
* `/deploy` - Production-ready configuration files (Nginx, Systemd daemons, Unix Sockets).

---

*Conceptualized and Built as an exploration of scalable AI workflows, SEO engineering, and decoupled machine learning architectures.*