# FreeFlow 🌬️

*A small RAG project that started with a blocked nose.*

## How this started

Last year I had a sinus problem that wouldn't go away. Like most people, I spent a lot of evenings reading whatever I could find: CDC pages, MedlinePlus, and eventually the American Academy of Otolaryngology's clinical practice guidelines, PDFs written for doctors and not really for someone squinting at them at 11pm with a stuffy head.

The sinus problem eventually cleared up. The curiosity stayed.

While I was reading I kept thinking it would be nice to just *ask* a question ("how long should I wait before antibiotics?", "what does watchful waiting actually mean?") and get an answer drawn only from trustworthy sources, not from a random forum. So once I felt better I built that. FreeFlow is the result. The name is the thing I wanted most back then.

It's a learning project. I used it to get hands-on with crawling, chunking, embeddings, vector stores and LLMs end to end, one piece at a time.

> ⚠️ **Not medical advice.** FreeFlow only repeats what's in a handful of public health documents. If you're unwell, talk to a doctor.

---

## What it does

```
 trusted sites ──► crawl & filter ──► raw HTML / PDF
                                          │
                                   load + extract text
                                          │
                                   chunk (1000 chars, 200 overlap)
                                          │
                              embed (Amazon Titan v2 via Bedrock)
                                          │
                                  store in ChromaDB (local)
                                          │
   your question ──► embed ──► top-5 similar chunks ──► Gemini ──► streamed answer
```

1. **Collect.** `collect_sinus_docs.py` crawls only CDC, MedlinePlus and ENTnet (AAO-HNS). It finds pages through `robots.txt` sitemaps plus a small, depth-limited crawl from a few seed pages.
2. **Filter.** Every page or PDF is scored for relevance and checked to be English, readable and not a search or index page. Each keep or reject decision is logged with a reason in `data/manifest.jsonl`.
3. **Chunk.** HTML goes through `trafilatura` to remove navigation and boilerplate. PDFs are read with `pypdf`. Both are split with LangChain's `RecursiveCharacterTextSplitter` and saved as JSONL with metadata.
4. **Embed and store.** Each chunk is embedded with `amazon.titan-embed-text-v2:0` (1024 dimensions) and stored in a persistent Chroma collection called `sinus_chunks`.
5. **Ask.** You type a question in the terminal. It gets embedded, the 5 closest chunks become the context, and Gemini streams back an answer. The system prompt tells Gemini to answer **only** from that context and to say so when the documents don't cover the question.

## What's in the knowledge base right now

The last collection run (March 2026) looked at 62 candidate URLs and kept 12. After de-duplication, 9 sources ended up chunked into 53 pieces:

| Source | What it is |
|---|---|
| MedlinePlus | Sinusitis overview, antibiotics overview |
| CDC | Sinus infection basics |
| ENTnet / AAO-HNS | Adult Sinusitis Clinical Practice Guideline: summary, diagnosis and treating ABRS; *Sinus Disease and Disorders* (2021); glossary of terms |

It's small on purpose. I wanted every document to be one I'd actually trust.

## Project layout

```
FreeFlow/
├── collect_sinus_docs.py      # the "grown-up" crawler: discovery, filtering, manifest
├── scripts/
│   └── crawler.py             # my very first attempt (kept for nostalgia, see below)
├── src/
│   ├── loader.py              # reads HTML / PDF from disk
│   ├── chunker.py             # trafilatura + recursive text splitting
│   ├── orchestrator.py        # glues everything together; interactive Q&A entry point
│   └── services/
│       ├── embedding.py       # Bedrock Titan embeddings
│       ├── vector_store.py    # ChromaDB (plus a commented-out OpenSearch attempt)
│       └── model.py           # Gemini client + system prompt
├── data/
│   ├── manifest.jsonl         # every URL considered, with keep/reject reason
│   ├── collector.log
│   ├── raw/html | raw/pdfs    # saved source documents
│   ├── raw/chunks/            # chunked JSONL per document
│   └── db/chroma_db/          # persisted vector store
└── requirements.txt
```

## Running it

**You'll need:** Python 3.12+ (I used 3.14), AWS credentials with Bedrock access to Titan Text Embeddings V2, and a Gemini API key.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install requests        # used by the collector
export GEMINI_API_KEY="your-gemini-key"   # AWS creds come from your usual AWS profile/env

# 1. Collect documents (run from the project root)
python collect_sinus_docs.py

# 2. Chunk, embed and ask (run from src/, since the paths are relative to it)
cd src
python orchestrator.py
```

`orchestrator.py`'s `__main__` block is my scratchpad. The pipeline steps (`orchestrate()`, `generate_embeddings_and_store()`, `inspect_vector_store()`) are commented out there, so uncomment whichever stage you want to run. By default it goes straight to the question prompt using the existing Chroma DB:

```
Please enter the input: when should I actually take antibiotics for a sinus infection?
```

## Things I learned along the way

- **The first crawler was naive, and that was useful.** `scripts/crawler.py` is what I wrote first: hit a few big health sites, grab `robots.txt`, print everything. It showed me quickly that the hard part of crawling is deciding what to *throw away*, not fetching. That's why the real collector has relevance scoring, language checks, content-hash de-duplication and a manifest that records the reason behind every decision.
- **Be polite to the sites you crawl.** The collector keeps a 1-second delay, retries with backoff, sends an honest user agent and has hard caps on pages and depth. These are government health sites, not a load test.
- **Boilerplate removal matters more than chunk size.** Before I added `trafilatura`, many chunks were cookie banners and nav menus. Removing that junk before splitting made a big difference to what came back.
- **I tried a managed vector DB first.** `vector_store.py` still contains a commented-out Amazon OpenSearch Serverless version with a k-NN index. It worked, but for nine documents a local Chroma folder is much simpler and costs nothing.
- **Mixing providers is fine.** Embeddings come from AWS Bedrock and generation comes from Google Gemini. RAG doesn't need both from the same vendor, as long as the same embedding model is used for documents and queries.
- **Grounding goes in the prompt.** The system prompt does most of the safety work: answer only from the context, admit when you don't know, never diagnose.

## Rough edges / what's next

- [ ] Turn `orchestrator.py`'s commented-out steps into proper CLI commands (`collect`, `index`, `ask`)
- [ ] Show citations: the source file of each retrieved chunk is already in the Chroma metadata, so it just needs to be printed
- [ ] Make paths independent of the working directory
- [ ] Try header-aware HTML splitting (there's a commented-out `HTMLSectionSplitter` attempt in `chunker.py`)
- [ ] A small eval set of questions I remember asking back then, to measure whether changes actually help

---

Built for fun and for learning, after a long stretch of breathing through my mouth. If you're here because your sinuses are bad right now: I hope it clears up soon. Drink water, try the saline rinse, and see a doctor if it drags on.
