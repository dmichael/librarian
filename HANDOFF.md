# Session Handoff: Librarian Project

**Date**: 2025-01-13
**Branch**: `claude/librarian-knowledge-base-4uRmW`

---

## Context Summary

The user is building **Librarian** - a personal knowledge base system that transforms their book collection into structured knowledge accessible to AI agents. This is not just document search; it's "epistemology as infrastructure."

### Key Insight from Discussion

The user's framing evolved during conversation:
1. Started as "process books for RAG"
2. Evolved to "agents are facets of my personal worldview"
3. Final framing: **the library IS the user's epistemology**, and agents inherit different views into it

Example discussed: A "Buffett Investment Advisor" agent wouldn't just have Buffett's letters—it would have access to related psychology, business history, and philosophy texts that the user has curated as relevant to that worldview.

---

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Build vs Buy | **Compose** existing tools | Don't reinvent; user explicitly said no forking/extending |
| Library hub | **Calibre** | Industry standard, CLI tools, plugin ecosystem |
| Extraction | **Marker + ebook_splitter** | Best-of-breed for PDF and EPUB respectively |
| RAG framework | **LlamaIndex** | Mature, flexible, good composability |
| Vector store | **Qdrant** (tentative) | Open source, good performance, faceted filtering |
| Classification | **Human-in-the-loop** | LLM suggests, user approves |

---

## Open Questions (Not Yet Resolved)

1. **Scale of library** - How many books? % physical vs digital?
2. **DRM stance** - Will user use DeDRM tools or stick to DRM-free?
3. **Budget** - Hardware (scanner) budget tolerance?
4. **Annotation capture** - How to efficiently capture reading highlights/notes?
5. **Embedding model** - OpenAI, Cohere, or local?
6. **Chunking strategy** - Chapter-based? Token-based? Semantic?

---

## What Was Researched

### Tools Evaluated

**RAG Frameworks:**
- LlamaIndex, LangChain, Haystack, RAGFlow, DSPy
- Conclusion: LlamaIndex for flexibility + composition

**Personal KB Tools:**
- Khoj, Quivr, Cognee
- Conclusion: Good but not book-specific enough; prefer composition

**Book-Specific:**
- Calibre (with new AI features in 8.x)
- Calibre-RAG MCP server
- ebook_splitter for chapter extraction

**Cloud KBaaS:**
- AWS Bedrock Knowledge Bases
- Azure AI Search
- Google Vertex AI RAG
- Conclusion: Viable but prefer self-hosted for personal library

### Key Resources Identified

- [Marker](https://github.com/VikParuchuri/marker) - PDF → Markdown
- [ebook_splitter](https://github.com/hirowa/ebook_splitter) - EPUB → structured chapters
- [Calibre DeDRM](https://itsfoss.com/calibre-remove-drm-kindle/) - If user chooses this path
- [CZUR scanners](https://shop.czur.com/blogs/blog/best-book-scanners-for-library-digitization-2025) - Non-destructive book scanning

---

## Architecture Overview

```
Physical/Digital Books
        │
        ▼
   ┌─────────┐
   │ Calibre │ ← Central hub, metadata, format conversion
   └────┬────┘
        │
        ▼
   ┌─────────────┐
   │ Extraction  │ ← Marker, ebook_splitter → structured markdown
   └──────┬──────┘
          │
          ▼
   ┌──────────────┐
   │Classification│ ← LLM suggests, human approves subjects + agent mappings
   └──────┬───────┘
          │
          ▼
   ┌─────────────┐
   │   Indexing  │ ← LlamaIndex → Qdrant (per-facet collections)
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │   Agents    │ ← Query facet collections, inherit worldview
   └─────────────┘
```

---

## File Structure Created

```
librarian/
└── SPEC.md          # Full specification document (542 lines)
    - Intent & vision
    - 6-stage pipeline architecture
    - Tool selections with rationale
    - Metadata schema (YAML example)
    - Subject taxonomy structure
    - Vector store organization
    - Agent architecture (hierarchy, access patterns)
    - Implementation phases
    - Open questions
```

---

## Suggested Next Steps

1. **Answer open questions** - Scale, budget, DRM stance
2. **Audit current library** - What does user actually have?
3. **Build extraction pipeline** - EPUB → markdown first (lowest friction)
4. **Prototype one agent** - Buffett advisor with 10-20 texts
5. **Iterate** - Refine taxonomy and access patterns based on usage

---

## Key Quotes from User

> "One goal of the library is to bootstrap agent context"

> "I could do this thing for each agent as I dev them but ultimately all agents are facets of my personal worldview (which of course could expand)"

> "I don't want to fork or extend existing projects but will do commercial or compose different projects"

> "Some text (most) is copyright but I own the text - it's just not something I can expect on the web"

---

## To Resume This Session

1. Read `SPEC.md` for full architecture
2. Review open questions above
3. Ask user: "Ready to start with Phase 1 (Calibre setup + audit)?"
4. Or: "Which open questions can we resolve?"
