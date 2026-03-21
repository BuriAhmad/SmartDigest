# SmartDigest — Conversation Decisions & Clarifications
> Paste this after the main spec document when prompting an LLM to build or extend SmartDigest.
> These are decisions made AFTER the original spec was written. They override or clarify the spec where there is a conflict.

---

## 1. Content Sourcing — How it works

**Decision: Option B — Curated source list (app-managed RSS feeds)**

The app maintains a `curated_sources` table seeded with 12–15 RSS feeds. When a user creates a subscription, they see a checkbox list and pick 2–3 sources. The app stores the selected URLs in `subscriptions.sources` as a JSONB array. The user never types or sees a raw URL.

**What to build:**
- A `curated_sources` table with pre-seeded rows (see seed list below)
- The create-subscription form shows these as labelled checkboxes, not a URL text field
- Selected sources are stored as their RSS URL strings in `subscriptions.sources JSONB`

**Seed data for `curated_sources`:**
```
name             | rss_url
-----------------|--------------------------------------------------
Hacker News      | https://news.ycombinator.com/rss
TechCrunch       | https://techcrunch.com/feed/
MIT Tech Review  | https://www.technologyreview.com/feed/
The Verge        | https://www.theverge.com/rss/index.xml
Wired            | https://www.wired.com/feed/rss
Ars Technica     | https://feeds.arstechnica.com/arstechnica/index
VentureBeat      | https://venturebeat.com/feed/
InfoQ            | https://www.infoq.com/feed/
Dev.to           | https://dev.to/feed
Simon Willison   | https://simonwillison.net/atom/everything/
```

**V2 upgrade:** Add a text input below the checkbox list labelled "+ add your own RSS URL" for users who want custom sources. This is intentionally out of scope for MVP.

---

## 2. LLM Summarisation Stage — How it works

**Pipeline position:** Stage 2 of 3. Runs after fetch, before deliver.

**What triggers it:** After `FetcherService` returns a list of `FetchedItem` objects (one per article), `SummariserService` is called with that list.

**How it calls the LLM:**
- Model: `claude-haiku-4-5` via the Anthropic Python SDK
- One API call per article (not batched)
- Concurrency: `asyncio.gather` with a semaphore limiting to 3 simultaneous calls (avoids rate limit hammering)
- Timeout: 30 seconds per call via `asyncio.wait_for`

**Exact system prompt to use:**
```
You are a concise newsletter editor. Summarise the following article in 2–3 sentences. Be direct. No filler phrases. No "In this article..." or "The author discusses...". Just the information.
```

**User message format:**
```
Title: {item.title}

Content: {item.raw_content}
```

Where `raw_content` is the first 1000 characters of the article body extracted during the fetch stage.

**What happens with the result:**
- The summary string is saved to `digest_items.summary`
- A `pipeline_events` row is written: `stage="summarise"`, `status="success"`, `duration_ms=<wall time>`, `item_count=<number of items processed>`

**Failure handling:**
- If the Anthropic API returns an error (429, 500, timeout): `summary = "[Summary unavailable]"` — the item is still included in the digest, just without a summary
- The failure is logged via structlog with the error details
- A `pipeline_events` row is written: `stage="summarise"`, `status="failed"`, `error_msg=<exception message>`
- Pipeline does NOT stop — remaining items continue to be summarised

**Code structure:**
```python
# app/services/summariser.py

async def summarise_items(digest_id: int, items: list[FetchedItem], db: AsyncSession) -> list[SummarisedItem]:
    sem = asyncio.Semaphore(3)
    async def summarise_one(item):
        async with sem:
            try:
                start = time.monotonic()
                response = await asyncio.wait_for(
                    client.messages.create(
                        model="claude-haiku-4-5",
                        max_tokens=200,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": f"Title: {item.title}\n\nContent: {item.raw_content}"}]
                    ),
                    timeout=30.0
                )
                summary = response.content[0].text
                duration = int((time.monotonic() - start) * 1000)
                # write success pipeline_event
                return SummarisedItem(**item.__dict__, summary=summary)
            except Exception as e:
                # write failed pipeline_event, log error
                return SummarisedItem(**item.__dict__, summary="[Summary unavailable]")
    return list(await asyncio.gather(*[summarise_one(i) for i in items]))
```

---

## 3. Dashboard UI — What it looks like

**Layout:** Two-column. Left column (60%) = subscriptions + recent digests. Right column (40%) = pipeline health + usage stats.

**Topbar:** App name "SmartDigest" | API key prefix display (e.g. "Key: a3f2…") | "Docs" link | "+ New subscription" button

**Left column — Subscription cards:**
Each card shows:
- Topic name (bold)
- Source chips: coloured pill badges showing which sources are selected (e.g. "Hacker News", "MIT Tech Review")
- Last delivered timestamp OR "Never"
- Active/paused status badge
- Three action buttons: "Trigger now" (primary), "Edit", "Delete"

Empty state: "No subscriptions yet. Add your first topic."

Below the cards: "Recent digests" table with columns — Topic | Timestamp | Status badge | Item count | View link

Status badges: delivered (green) | processing (yellow) | failed (red) | queued (gray)

**Right column — Pipeline health panel:**
- Auto-refreshes every 5 seconds via HTMX: `hx-get="/dashboard/metrics" hx-trigger="every 5s"`
- 4 metric cells: Jobs run (24h) | Failed | Total API calls | Manual triggers
- Stage latency bars: Fetch / Summarise / Deliver shown as horizontal bars with ms values
- Last error box (red background): shows stage name + error message + timestamp of most recent failure
- Below pipeline health: Usage card showing key prefix, total calls, calls today, last active

**Tech:** HTMX + Jinja2 templates served from FastAPI. Tailwind CSS via CDN. No JS framework. No build step.

**Create subscription modal (triggered by "+ New subscription"):**
- Topic label: text input (required)
- Sources: checkbox list from `curated_sources` table (min 1 required)
- Email: email input (required)
- Schedule: select dropdown — "Daily 7AM" only for MVP
- Submit via HTMX POST, new card appended to list on success, modal closes
- Validation errors shown inline under each field

---

## 4. Key Constraints to Remember

- No user types a raw RSS URL anywhere in the MVP
- LLM failures are silent to the user (summary shows as "[Summary unavailable]") but always logged and written to pipeline_events
- The pipeline never fully stops due to a single item or source failure — it degrades gracefully
- The pipeline_events table is the ONLY observability mechanism — no external metrics tooling
- Frontend is served directly from FastAPI — no separate frontend deployment
