# ADR-0001: Read Upwork job postings via human copy/paste, not automated scraping

**Status:** Accepted
**Date:** 2026-05-27
**Owner:** John Lamont
**Supersedes:** the prior `upwork-scraper` / `upwork-filter` / `zoho-upwork` MCP pipeline (retired by this decision)

---

## Context

The tool needs to read Upwork job postings so Claude can evaluate fit and draft proposals. The obvious technical approaches — poll the feed via HTTP, drive a headless browser, run a Chrome extension that watches the page — are all forms of *programmatic reading*. Each one creates an automation signature that Upwork's enforcement systems are built to detect.

The prior MCP pipeline (`upwork_get_jobs`) read the feed programmatically. The natural evolution path of that design — "poll faster, alert sooner, win the speed race" — is the path this ADR rejects.

### What Upwork's policy actually says

Upwork's automation policy is **behavioral, not consent-based**, and the bar is low. Per their published Help Center documentation:

> A bot is any script, program, browser extension, or third-party service that automatically sends requests, collects data, or performs actions faster or more frequently than a human could.

Critically, **an API key does not exempt scraping**: the same policy states that scraping public or private data remains off-limits even with API access. The named examples of behavior that can trigger a warning, restriction, or block include:

- Job alert/watcher tools that scrape or run searches
- Auto-refresh tools that reload pages on a timer
- Page monitors that poll for updates

A Python process hitting the feed every 60 seconds — even one triggered by a human button press — fits all three.

### How enforcement actually works

Two facts about Upwork's enforcement make this load-bearing rather than theoretical:

1. **It is automated and unforgiving.** In November 2025, a developer's account was restricted over a Chrome extension that only *improved* job search — no auto-bidding, no scraping. The Trust & Safety email noted the review was completed fully by automation. This is the standard, not the exception.

2. **The signals are technical fingerprints, not intent.** Reported triggers include scheduled feed loads without user input and API calls from non-browser user agents. A Python `requests` session reading the feed presents the same fingerprint whether a cron job or a finger triggered the call. Manual triggering reduces *cadence*; it does not make the read itself indistinguishable from a human.

### What Upwork allows

The same source material is explicit about the compliant pattern:

> AI for drafting and job-matching notifications is allowed as long as a human reviews and sends the final proposal.

The five AI uses that survive on the platform: job scoring, proposal drafting, speed alerts, performance analytics, reply handling. The line is **where the human sits** — not whether AI is involved, not how fast the system is, but whether a human is in the loop when content leaves the account.

### Why the speed-race premise doesn't justify the risk

The argument for accepting automation risk would be that early application materially improves win rate. This claim is widespread but the evidence base is weak: the "apply within 15 minutes" consensus comes overwhelmingly from advice content, freelancing blogs, and the marketing copy of paid extensions whose entire business is selling speed. No source measured win rate against application order while holding proposal quality constant.

The freelancer this would matter most for is a beginner with no reviews, who gets filtered out on credentials regardless of speed. For an established certified Zoho partner with a 30-year background, a strong specific credentialed proposal at minute 25 plausibly beats a fast generic one at minute 3. The optimization target is **fit + quality + drafting leverage**, not latency.

The downside is asymmetric: a marginal improvement in speed-to-bid traded against a non-trivial chance of losing the account entirely. Account loss is livelihood loss. The trade does not pencil.

---

## Decision

**The tool reads Upwork job postings exclusively by human copy/paste from a browser the human is operating.**

Mechanically:
- The owner reads Upwork in Chrome, as they would anyway.
- For each job worth evaluating, they copy the URL (one paste) and the Summary block (a second paste) into the local Tkinter window, optionally adding margin annotations.
- They click a button to trigger Claude's evaluation.
- No Python process ever connects to Upwork. There is no scraper, no poller, no API client, no headless browser, no extension. The local tool and Upwork share no data path other than the human's clipboard and hands.

This is captured as the load-bearing invariant in `ARCHITECTURE.md` §2: *Python and the local tool never communicate with Upwork. The browser is operated only by the human.*

---

## Alternatives Considered

**Sanctioned Upwork API.** Upwork operates an API-key program, but the public API is narrow — profile retrieval, search, messaging — and per the owner's prior research does not expose job-feed reads in a way that would support a fit-evaluation workflow. Even if it did, the same policy text explicitly says scraping is prohibited *with* an API key, so a programmatic feed read built on the official API is not automatically compliant. Rejected: doesn't expose what we need, and wouldn't necessarily be safe even if it did.

**Headless scrape with conservative throttling.** Spreading requests out — "we'll only poll every 10 minutes, like a careful human" — does not change the fingerprint. Non-browser user agent, scheduled cadence, no UI interaction: still detectable, still bannable, just slower. Rejected: throttling treats the symptom (cadence) and not the cause (programmatic reading).

**Browser extension that reads the page the human is already on.** Closer to compliant in spirit — the human's session, the human's browser — but Upwork explicitly names browser extensions in its definition of "bot," and the November 2025 case turned on a benign extension. The enforcement risk is the same category as scraping, with the additional surface of the extension itself being inspectable evidence. Rejected: same category of risk as the thing we're avoiding.

**Manual copy/paste with a single paste of mixed content.** An earlier draft of the design had the human paste a blob of whole-card text and let Claude structure it. Rejected in favor of *disciplined* copy/paste (URL + Summary block only) because the Summary block is already clean prose, and limiting input to it eliminates the temptation to extract fields (budget, payment-verified) that aren't actually there — which would invite Claude to fabricate them. See ARCHITECTURE §4.3.

---

## Consequences

### Positive

- **The compliance question dissolves.** Upwork cannot distinguish a human reading their own job feed and copying text from any other human reading their own job feed and copying text. Because that *is* what's happening. The whole category of detection risk does not apply.
- **No detection-evasion arms race.** No user-agent rotation, no request-spacing heuristics, no proxy infrastructure, no fingerprint masking — none of which would have worked anyway, all of which would have added permanent maintenance burden.
- **The architecture simplifies materially.** No scraper module, no rate limiter, no session/cookie management, no retry logic for blocked requests, no dedup-by-job-ID (the human is the dedup). The prior three-server pipeline retires entirely.
- **Quality replaces speed as the optimization target.** Forced off the latency arms race, the tool's design centers on the things that actually move win rate for this freelancer: sharper fit judgment, better drafts, faster human review of strong candidates.
- **The human's eyeballs are the first filter.** Obvious mismatches (COBOL jobs, $5 fixed-price tasks) never get copied in the first place. This pre-filtering is free and high-quality, and it shapes the calibration discussion in ARCHITECTURE §8 (the real discrimination is between `strong` and `medium`, and on the `MODEL FIT` axis, not coarse triage).

### Negative / accepted costs

- **A few seconds of manual copy/paste per job.** This is the cost of compliance. It is the design, not a flaw in it.
- **No batch processing.** One job at a time. For a high-volume mass-bidder this would matter; for this freelancer's positioning (selective, high-quality bids on a small number of well-fit jobs), it does not.
- **No automated "new job" alerts.** The tool cannot tell the owner about jobs they haven't seen yet, because it cannot see Upwork at all. Discovery remains the owner's job, in the browser. (Acceptable: discovery was never the bottleneck — evaluation and drafting were.)
- **The owner must manually upload deep-dive artifacts** (Phase II) into the posting by hand. Per the §2 invariant in ARCHITECTURE, this is non-negotiable and the cost is small.

### Out-of-scope by construction

This decision makes the following features **out of scope by definition**, not merely deferred:

- Polling, scheduled feed reads, alerting bots
- Auto-filling the Upwork proposal box
- Attaching files to a submission via automation
- Posting any content to Upwork via any mechanism
- Browser automation (Puppeteer, Playwright, Selenium, extensions)
- Reusing the owner's Upwork session cookie from any non-browser code

Any future feature request that would require crossing the Python ↔ Upwork boundary is answered: *the human copies/pastes it*. Not negotiable; revisiting requires superseding this ADR.

---

## References

- Upwork Help Center — automation and bot policy (the bot definition, the "API key does not exempt scraping" clause, the named-example list).
- November 2025 enforcement case: Chrome extension restriction with automated Trust & Safety review.
- ARCHITECTURE.md §2 (the load-bearing invariant), §10 (non-goals).
- This conversation, 2026-05-27, where the policy text was retrieved and the prior pipeline's polling-evolution path was rejected.
