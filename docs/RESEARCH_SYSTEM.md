# MimOSA Research System (Milestone 6)

A user & developer guide to MimOSA's research capability: balanced, budgeted,
privacy-aware web research with multi-source synthesis.

> **TL;DR** — Ask MimOSA to *"research electric cars"* or *"what are people
> saying about remote work?"* and it gathers sources from across the spectrum
> (mainstream, alternative, social, academic, official…), negotiates a token
> budget, and writes a balanced answer that attributes claims to perspectives
> and names the gaps. **Web search is off by default**; sensitive queries are
> synthesized on-device.

---

## 1. Architecture

```
                         ResearchSkill  (mimosa/skills/research_skill.py)
                                │  extract_topic("research electric cars") → "electric cars"
                                ▼
                       ResearchEngine.research()      (mimosa/research/research_engine.py)
                                │
   ┌────────────────────────────┼──────────────────────────────┬───────────────────────────┐
   ▼                            ▼                              ▼                           ▼
SearchClient            BudgetNegotiator              PrivacyGuard (M5.4)        ResearchSynthesizer
(search.py)             (token_budget.py)             create_provider_for()      (synthesizer.py)
   │                            │                              │                           │
 backend:                  plan(): trim excerpts,        local provider if          LLM path  ── or ──►
 ┌─ StaticBackend          drop least-valuable           query is sensitive         extractive fallback
 │  (offline,              (preserve perspective                                    (deterministic, local)
 │   deterministic)        diversity)                                                       │
 └─ DuckDuckGoBackend      record_usage() ─► PreferenceLearner (M5.2)                       ▼
    (key-free HTML)        suggest_budget()                                          ResearchReport
   │                                                                                 (answer + perspectives
 Source enrichment                                                                    + citations + plan)
 (sources.py): classify
 domain → category,
 dedupe, per-category cap
```

Each stage is independent, injectable, and **degrades gracefully**: no backend
→ "no sources"; no LLM → extractive synthesis; no `tiktoken` → heuristic token
count; no `requests`/`bs4` → the DuckDuckGo backend reports unavailable.

---

## 2. Components

### 2.1 Sources & perspective classification — `mimosa/research/sources.py`

Every result is a `Source`, auto-classified into a `SourceCategory`:

| Category | Examples |
|----------|----------|
| `mainstream` | reuters.com, bbc.co.uk, nytimes.com |
| `alternative` | substack.com, medium.com, reason.com |
| `social` | reddit.com, x.com, news.ycombinator.com |
| `video` | youtube.com, vimeo.com, rumble.com |
| `think_tank` | brookings.edu, rand.org, cato.org |
| `academic` | arxiv.org, nature.com, `.edu`, `.ac.uk` |
| `official` | `.gov`, `.gov.uk`, `.mil`, `.int` |
| `reference` | wikipedia.org, britannica.com |

```python
from mimosa.research.sources import classify_url, Source, summarize_perspectives

classify_url("https://www.bbc.co.uk/news/x")   # SourceCategory.MAINSTREAM
s = Source(title="Study", url="https://arxiv.org/abs/1234")
s.category      # SourceCategory.ACADEMIC
s.perspective   # "Academic & research"

summarize_perspectives([s])["missing"]
# ['mainstream', 'alternative', 'social', 'think_tank', 'official']  ← gaps reported
```

> The domain table is a small, editable **heuristic aid for balance** — not an
> authoritative or political judgement of any outlet.

### 2.2 Search — `mimosa/research/search.py`

```python
from mimosa.research.search import SearchClient, DuckDuckGoBackend, StaticBackend

# Offline / tests (deterministic):
client = SearchClient(backend=StaticBackend([
    {"title": "A", "url": "https://bbc.co.uk/a", "snippet": "..."},
]))

# Live (opt-in): key-free DuckDuckGo HTML endpoint
client = SearchClient(backend=DuckDuckGoBackend(), per_category_cap=3)

sources = client.search("electric cars")   # List[Source], deduped & balanced
client.online                                # False when backend is None/unavailable
```

Backends **never raise** — network/parse/dep failures return `[]`.

### 2.3 Token budget & negotiation — `mimosa/research/token_budget.py`

```python
from mimosa.research.token_budget import TokenBudget, BudgetNegotiator

neg = BudgetNegotiator(TokenBudget(max_total=3000, reserve_output=600))
plan = neg.plan("electric cars", sources, max_sources=6)
plan.num_sources, plan.per_source_tokens, plan.within_budget
plan.negotiation_message()
# "This research will use about 2310 tokens across 6 sources spanning 4 perspectives (budget 3000)."
```

When over budget the negotiator first shrinks per-source excerpts, then drops
the **least valuable** source — preferring to drop an over-represented category
so rare perspectives survive.

**Cost-pattern learning** (builds on M5.2): `record_usage(topic, tokens)` buckets
the spend (small/medium/large/xlarge) and stores it; `suggest_budget(topic)`
proposes a budget next time. No-ops without a `PreferenceLearner`.

### 2.4 Synthesis — `mimosa/research/synthesizer.py`

```python
from mimosa.research.synthesizer import ResearchSynthesizer

synth = ResearchSynthesizer(llm_provider)          # or None → extractive fallback
result = synth.synthesize("electric cars", sources, per_source_tokens=200)
result.answer                  # balanced, perspective-attributed answer
result.balanced                # True iff ≥ 2 distinct perspectives
result.perspectives_missing    # gaps named in the answer too
result.used_llm                # False if fallback was used
```

The LLM is instructed to attribute claims to perspectives, note agreement/
disagreement, **name missing perspectives**, stay neutral, and invent nothing.
Any LLM error → the deterministic extractive fallback.

### 2.5 Orchestrator & skill — `research_engine.py`, `skills/research_skill.py`

```python
from mimosa.research.research_engine import ResearchEngine
from mimosa.research.search import SearchClient, DuckDuckGoBackend

engine = ResearchEngine(
    SearchClient(backend=DuckDuckGoBackend()),   # online (opt-in)
    llm_provider=provider,
    privacy_guard=guard,                          # sensitive → local provider
    preference_learner=learner,                   # cost learning
)
report = engine.research("electric cars")
print(report.answer)
print(report.speakable(include_budget=True))
```

The **`ResearchSkill`** is what the router dispatches to. Its default engine is
**offline** — so a fresh install never makes a surprise network call; the host
app injects an online engine only when the user enables web search.

---

## 3. Privacy summary

| Concern | Behaviour |
|---------|-----------|
| Network on fresh install | **None.** `web_search_enabled = False` by default; default skill engine is offline. |
| What leaves the device | Only the query (to the search backend) and query + snippets (to the model). No identifiers, no telemetry. |
| Sensitive queries | Routed to a **local** provider via the Privacy Guard (M5.4). Routing failure fails *safe* (local extractive), never the cloud. |
| Cost learning | Stored on-device via the M5.2 `PreferenceLearner`; off when `learn_cost_patterns = False`. |
| Optional deps | `tiktoken`, `requests`, `bs4` all optional — every path degrades gracefully. |

---

## 4. Configuration quick reference

`ResearchSettings` (in `mimosa/utils/config.py`, under `AppConfig.research`):

| Field | Default | Meaning |
|-------|---------|---------|
| `web_search_enabled` | `False` | Master opt-in. Off = no network, local message. |
| `backend` | `"duckduckgo"` | `"none"` (offline) or `"duckduckgo"`. |
| `max_sources` | `6` | Max sources fed into synthesis (1–25). |
| `per_category_cap` | `3` | Max sources kept per perspective (1–25). |
| `token_budget` | `3000` | Evidence+synthesis token ceiling (256–200000). |
| `include_budget_note` | `False` | Prepend a spoken "this will use ~N tokens" note. |
| `learn_cost_patterns` | `True` | Learn per-topic cost via M5.2. |

```python
from mimosa.utils.config import AppConfigManager
mgr = AppConfigManager()
mgr.update_section("research", {"web_search_enabled": True, "max_sources": 8})
```

---

## 5. Trigger phrasing (intent routing)

The router classifies these as **research** (before the generic question
heuristic): *research X · do some research on X · look into X · investigate X ·
dig into X · find out about X · what are people saying about X · give me a
balanced overview of X · different/opposing perspectives on X · search the web
for X*.

---

## 6. Running the tests

```bash
cd MimOSA
python3 -m pytest -q tests/test_research_sources.py tests/test_token_budget.py \
  tests/test_research_search.py tests/test_synthesizer.py \
  tests/test_research_engine.py tests/test_research_skill.py
# all offline / hermetic — no network, LLM mocked, :memory: DBs
```
