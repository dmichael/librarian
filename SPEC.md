# Librarian: Personal Knowledge Base for Agent Intelligence

## Executive Summary

Librarian is a system for transforming a personal book library into a structured knowledge base that can bootstrap domain-specific AI agents. It treats your reading collection as an **epistemology made infrastructure**—agents don't just search documents, they inherit facets of your worldview.

---

## 1. Intent & Vision

### 1.1 Core Problem

Personal libraries contain decades of curated knowledge across domains, but this knowledge is:
- Locked in physical books or DRM-protected ebooks
- Unstructured and unsearchable
- Inaccessible to AI agents that could leverage it

### 1.2 Solution

A compositional system that:
1. **Ingests** books from multiple sources (physical, ebook, PDF)
2. **Normalizes** content into structured, searchable text
3. **Classifies** by domain/subject with human-in-the-loop curation
4. **Indexes** into vector stores with faceted access patterns
5. **Exposes** knowledge to agents through domain-specific views

### 1.3 Key Insight: Facets, Not Silos

Agents are not isolated knowledge bases. They are **facets** of a unified worldview:

```
                    ┌─────────────────────────────────┐
                    │      YOUR WORLDVIEW             │
                    │   (The Complete Library)        │
                    │                                 │
                    │  Philosophy ◆ Investing ◆ Tech │
                    │  History ◆ Science ◆ Biography │
                    │  Strategy ◆ Psychology ◆ ...   │
                    └───────────────┬─────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │         FACETED ACCESS        │
                    └───────────────┬───────────────┘
                                    │
        ┌───────────┬───────────┬───┴───┬───────────┬───────────┐
        ▼           ▼           ▼       ▼           ▼           ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ Buffett │ │ Stoic   │ │ Tech    │ │ History │ │ Future  │
   │ Advisor │ │ Counsel │ │ Analyst │ │ Lens    │ │ Agents  │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

A "Buffett Advisor" agent has:
- **Primary access**: Value investing texts (Buffett letters, Graham, Munger)
- **Secondary access**: Psychology, business history, biography
- **Tertiary access**: Full library for synthesis when needed

### 1.4 Design Principles

1. **Composition over invention** - Use existing tools (Calibre, LlamaIndex, etc.), don't rebuild
2. **Human-in-the-loop curation** - LLMs suggest, humans approve classifications
3. **Your voice matters** - Annotations and notes are first-class citizens
4. **Start narrow, expand** - Build one agent deeply before generalizing

---

## 2. Source Material

### 2.1 Input Categories

| Source Type | Format | Challenge | Priority |
|-------------|--------|-----------|----------|
| Physical books | Paper | Scanning + OCR | P2 |
| Kindle purchases | AZW/KFX | DRM extraction | P1 |
| DRM-free ebooks | EPUB/MOBI | Format conversion | P0 |
| PDF books | PDF | Structure extraction | P1 |
| Academic papers | PDF | Citation/structure | P2 |
| Audiobooks | M4B/MP3 | Transcription | P3 |
| Personal notes | Various | Integration | P1 |

### 2.2 Legal Considerations

- Personal backup copies of owned material: defensible under fair use (US)
- DRM circumvention: gray area under DMCA anti-circumvention provisions
- Distribution: clearly prohibited
- **Recommendation**: Prefer DRM-free sources going forward; handle existing DRM content at personal discretion

---

## 3. Pipeline Architecture

### 3.1 High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     STAGE 1: INGESTION                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Physical Books ──► Scanner ──► TIFF ──► OCR ──┐                │
│                     (CZUR)         (ABBYY/      │                │
│                                    Tesseract)   │                │
│                                                 ▼                │
│  Kindle Books ──► [DRM Process] ─────────────► EPUB ──┐         │
│                                                        │         │
│  Purchased EPUB ─────────────────────────────────────►├──► Calibre
│                                                        │     (Hub)
│  PDF Books ──► Marker ──► Markdown ──────────────────►│         │
│                                                        │         │
│  Audiobooks ──► Whisper ──► Text ────────────────────►┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  STAGE 2: NORMALIZATION                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Calibre Library                                                 │
│       │                                                          │
│       ├── Metadata enrichment (ISBN → OpenLibrary/Google Books) │
│       ├── Format conversion (all → EPUB canonical)              │
│       ├── Cover extraction                                       │
│       ├── De-duplication                                         │
│       └── Initial subject tagging                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STAGE 3: EXTRACTION                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  EPUB ──► ebook_splitter ──► Structured Output                  │
│                                     │                            │
│                                     ▼                            │
│                          ┌─────────────────────┐                │
│                          │   books/            │                │
│                          │   └── {book-id}/    │                │
│                          │       ├── meta.yaml │                │
│                          │       ├── chapters/ │                │
│                          │       │   ├── 01.md │                │
│                          │       │   └── ...   │                │
│                          │       └── full.md   │                │
│                          └─────────────────────┘                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  STAGE 4: CLASSIFICATION                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  For each book:                                                  │
│    1. LLM analyzes content → suggests subjects & agent mappings │
│    2. Human reviews/approves/refines                            │
│    3. Tags written to meta.yaml                                  │
│    4. Optional: add personal annotations/notes                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STAGE 5: INDEXING                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Chunking (by chapter/section)                                   │
│       │                                                          │
│       ▼                                                          │
│  Embedding Generation (OpenAI/Cohere/local)                     │
│       │                                                          │
│       ▼                                                          │
│  Vector Store Insertion                                          │
│    ├── Per-facet collections (investing, philosophy, etc.)      │
│    └── Unified full-library index                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STAGE 6: AGENT ACCESS                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Agent queries facet collection(s)                               │
│    → Retrieves relevant chunks                                   │
│    → Synthesizes response grounded in your library              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Tool Selection

| Stage | Tool | Role | License |
|-------|------|------|---------|
| Ingestion/Hub | Calibre | Library management, format conversion | GPL |
| Physical Scan | CZUR Scanner | Non-destructive book scanning | Commercial |
| OCR | ABBYY FineReader | Text extraction from scans | Commercial |
| PDF Extraction | Marker | PDF/EPUB → Markdown | Apache 2.0 |
| EPUB Splitting | ebook_splitter | Chapter extraction | MIT |
| Chunking | LlamaIndex | Text splitting, metadata | MIT |
| Embeddings | OpenAI / Cohere | Vector generation | Commercial API |
| Vector Store | Qdrant / Chroma | Storage and retrieval | Apache 2.0 |
| Orchestration | LlamaIndex | RAG pipeline | MIT |
| Agent Layer | Claude API / MCP | Agent interface | Commercial |

---

## 4. Library Management

### 4.1 Calibre as Central Hub

Calibre serves as the canonical source of truth for library contents:

```
calibre-library/
├── metadata.db              # Calibre's SQLite database
├── Author Name/
│   └── Book Title (123)/
│       ├── cover.jpg
│       ├── metadata.opf
│       └── Book Title.epub
```

**Key Calibre Operations:**
- `calibredb add` - Import new books
- `calibredb set_metadata` - Update metadata
- `ebook-convert` - Format conversion
- `calibredb list` - Export catalog

### 4.2 Metadata Schema

Each book in the extracted library has a `meta.yaml`:

```yaml
# books/{book-id}/meta.yaml

id: "buffett-letters-2023"
calibre_id: 1234

# Basic metadata (from Calibre/ISBN lookup)
title: "Berkshire Hathaway Letters to Shareholders"
authors:
  - "Warren Buffett"
isbn: "978-0615975078"
publisher: "Max Olson"
year: 2023
pages: 820

# Classification (human-approved)
subjects:
  primary:
    - investing/value-investing
    - business/management
  secondary:
    - biography/business-leaders
    - economics/markets

# Agent mappings
agents:
  buffett-advisor:
    relevance: primary        # core material
    weight: 1.0
  investment-analyst:
    relevance: secondary      # useful context
    weight: 0.7

# Your voice
annotations:
  why_read: "Foundation for understanding long-term value investing"
  key_insights:
    - "Circle of competence concept"
    - "Owner earnings vs accounting earnings"
    - "Moat thinking"
  related_to:
    - "graham-intelligent-investor"
    - "munger-poor-charlies-almanack"

# Processing status
status:
  extracted: true
  indexed: true
  classified: true
  last_updated: "2025-01-13"
```

### 4.3 Subject Taxonomy

Start coarse, refine as agents are built:

```
subjects/
├── investing/
│   ├── value-investing
│   ├── macro-economics
│   ├── quantitative
│   └── behavioral-finance
├── philosophy/
│   ├── stoicism
│   ├── ethics
│   ├── epistemology
│   └── eastern
├── technology/
│   ├── software-engineering
│   ├── artificial-intelligence
│   ├── systems-thinking
│   └── history-of-tech
├── psychology/
│   ├── cognitive-biases
│   ├── decision-making
│   ├── motivation
│   └── behavioral
├── business/
│   ├── strategy
│   ├── management
│   ├── entrepreneurship
│   └── case-studies
├── history/
│   ├── economic-history
│   ├── military-history
│   ├── biography
│   └── civilizations
└── science/
    ├── physics
    ├── biology
    ├── complexity
    └── mathematics
```

---

## 5. Knowledge Base Architecture

### 5.1 Storage Structure

```
librarian/
├── SPEC.md                    # This document
├── config/
│   ├── settings.yaml          # Global configuration
│   ├── subjects.yaml          # Subject taxonomy
│   └── agents.yaml            # Agent definitions
│
├── calibre/                   # Calibre library (or symlink)
│   └── ...
│
├── books/                     # Extracted & processed books
│   ├── {book-id}/
│   │   ├── meta.yaml          # Book metadata + classifications
│   │   ├── chapters/
│   │   │   ├── 00-frontmatter.md
│   │   │   ├── 01-chapter-one.md
│   │   │   └── ...
│   │   ├── full.md            # Complete text
│   │   └── annotations.md     # Your notes (optional)
│   └── ...
│
├── vectors/                   # Vector store data
│   ├── full-library/          # Unified index
│   ├── investing/             # Per-facet collections
│   ├── philosophy/
│   └── ...
│
├── agents/                    # Agent configurations
│   ├── buffett-advisor/
│   │   ├── config.yaml        # Agent-specific settings
│   │   ├── system-prompt.md   # Agent persona/instructions
│   │   └── reading-list.yaml  # Books assigned to this agent
│   └── ...
│
└── scripts/                   # Pipeline automation
    ├── ingest.py
    ├── extract.py
    ├── classify.py
    ├── index.py
    └── query.py
```

### 5.2 Vector Store Organization

Using Qdrant collections (or equivalent):

```
Collections:
├── librarian_full            # All books, all chunks
│   └── payload: {book_id, chapter, subjects[], agents[]}
│
├── librarian_investing       # Filtered: subjects contains investing/*
├── librarian_philosophy      # Filtered: subjects contains philosophy/*
├── librarian_technology      # Filtered: subjects contains technology/*
└── ...

Agent queries:
  buffett-advisor:
    primary:   librarian_investing (weight: 1.0)
    secondary: librarian_philosophy, librarian_psychology (weight: 0.5)
    fallback:  librarian_full (on explicit request)
```

### 5.3 Access Patterns

| Pattern | Description | Implementation |
|---------|-------------|----------------|
| **Facet Query** | Agent queries its primary domain | Query domain-specific collection |
| **Expanded Query** | Agent needs broader context | Query primary + secondary collections |
| **Full Library** | Synthesis across all domains | Query unified index |
| **Book-Specific** | Deep dive into single source | Filter by book_id |
| **Cross-Reference** | Find related content | Query by subject overlap |

---

## 6. Agent Architecture

### 6.1 Agent Definition

```yaml
# agents/buffett-advisor/config.yaml

name: "Buffett Investment Advisor"
id: "buffett-advisor"
description: "Value investing advisor grounded in Buffett/Munger philosophy"

# Knowledge access
collections:
  primary:
    - librarian_investing
  secondary:
    - librarian_psychology
    - librarian_business

# Retrieval settings
retrieval:
  top_k: 10
  primary_weight: 1.0
  secondary_weight: 0.5
  rerank: true

# Model settings
model:
  provider: anthropic
  model: claude-sonnet-4-20250514
  temperature: 0.3
```

### 6.2 Agent Hierarchy

```
BaseWorldview (meta-agent)
│   → Access: full library
│   → Purpose: synthesize across all domains
│
├── InvestingFacet
│   ├── BuffettAdvisor
│   │   → Primary: value investing, Buffett corpus
│   │   → Secondary: psychology, business history
│   │
│   └── MacroAnalyst
│       → Primary: macro economics, market history
│       → Secondary: geopolitics, monetary policy
│
├── PhilosophyFacet
│   ├── StoicCounsel
│   │   → Primary: stoic texts, practical philosophy
│   │   → Secondary: psychology, biography
│   │
│   └── EthicsAdvisor
│       → Primary: ethics, moral philosophy
│       → Secondary: history, case studies
│
└── TechnologyFacet
    ├── TechAnalyst
    │   → Primary: technology, software engineering
    │   → Secondary: business strategy, history of tech
    │
    └── AIStrategist
        → Primary: AI/ML texts
        → Secondary: philosophy of mind, ethics
```

---

## 7. Implementation Phases

### Phase 1: Foundation
- [ ] Set up Calibre library
- [ ] Import existing ebooks
- [ ] Run metadata enrichment
- [ ] Export inventory/audit current collection

### Phase 2: Extraction Pipeline
- [ ] Build EPUB → structured markdown pipeline
- [ ] Process initial batch of books
- [ ] Validate output quality

### Phase 3: Classification System
- [ ] Define initial subject taxonomy (coarse)
- [ ] Build LLM-assisted classification tool
- [ ] Classify initial batch with human review

### Phase 4: First Agent (Buffett Advisor)
- [ ] Curate reading list (10-20 core texts)
- [ ] Index into vector store
- [ ] Build agent configuration
- [ ] Test and refine retrieval

### Phase 5: Expand
- [ ] Add more agents/facets
- [ ] Refine taxonomy based on usage
- [ ] Add annotation/notes workflow
- [ ] Build unified worldview agent

---

## 8. Open Questions

1. **Embedding model choice**: OpenAI ada-002, Cohere embed, or local (e5, bge)?
2. **Chunking strategy**: By chapter? Fixed token size? Semantic boundaries?
3. **Annotation capture**: How to efficiently capture highlights/notes from reading?
4. **Physical book priority**: Scan high-value books or find digital alternatives?
5. **Version control**: How to handle updated editions of books?
6. **Multi-modal**: Include diagrams, charts, images from books?

---

## 9. References & Resources

### Tools
- [Calibre](https://calibre-ebook.com/) - Library management
- [Marker](https://github.com/VikParuchuri/marker) - PDF extraction
- [ebook_splitter](https://github.com/hirowa/ebook_splitter) - EPUB chapter extraction
- [LlamaIndex](https://www.llamaindex.ai/) - RAG framework
- [Qdrant](https://qdrant.tech/) - Vector database

### Concepts
- [Building a Second Brain](https://www.buildingasecondbrain.com/) - PKM methodology
- [Faceted Classification](https://en.wikipedia.org/wiki/Faceted_classification) - Library science approach

### Commercial Alternatives Evaluated
- AWS Bedrock Knowledge Bases
- Khoj (self-hosted)
- Quivr
- RAGFlow

Decision: **Compositional approach** using best-of-breed tools rather than monolithic platform.
