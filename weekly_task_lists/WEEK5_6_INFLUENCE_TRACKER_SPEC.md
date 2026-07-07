# Influence Tracker — full technical specification (Weeks 5–6, Stage 3)

_A build-ready spec for the "who moves retail?" layer: identify the most
influential accounts on Reddit and X, track their influence OVER TIME,
capture their opinions, and add the second layer — their audience and what
the audience thinks. Written to hand to a capable model/engineer verbatim.
Everything here is checked against what APIs actually allow in 2026; the
things that are NOT possible on self-serve access are flagged, with the
honest workaround used instead._

---

## 0. What we are building (matches the Weeks 5–6 prototype mock)

Four surfaces, exactly like the prototype image in the proposal:

1. **Influence Network** — force-directed graph: nodes = accounts, tickers,
   themes; node size = PageRank; edges = interactions (replies, mentions,
   quotes). Time-filterable (TODAY / THIS WEEK / window).
2. **Most Influential Accounts** — ranked list with: influence score,
   Δ over window, engagement volume, their current stance per ticker/theme
   (sentiment chips like `$SOL BULLISH`), verified via our own sentiment
   pipeline on their actual posts.
3. **Centrality Leaders + Communities Detected** — PageRank/centrality
   table, plus Louvain clusters of co-discussed themes and co-acting users.
4. **Audience layer** — for each influencer: who engages with them, and
   what THAT crowd's aggregate sentiment is. The killer read is the
   DIVERGENCE: "influencer turned bearish, their audience is still bullish"
   (or the reverse — audiences sometimes turn first).

### Design constraints inherited from the main project (non-negotiable)

- **Point-in-time discipline**: every influence score must be computable
  from data available on that day (trailing windows, snapshots never
  revised). Influence weights that use FINAL follower counts to weight
  PAST posts repeat the score² look-ahead leak we already removed
  (design_decisions.xlsx #30).
- **Registry pattern**: every new data source = raw immutable file + one
  normaliser + one registry entry.
- **Same output surface**: panels land in the existing Streamlit
  dashboard, served by the `optimised/` package; heavy collection runs in
  `run_daily.py`.

---

## 1. Prior art (what exists, what to copy, what to avoid)

| Project | What it does | What we take |
|---|---|---|
| **Social Blade** (socialblade.com) | The canonical follower-count-over-time tracker; daily snapshots + deltas; has a paid API | The MODEL: influence-over-time = self-collected daily snapshots + Δ tables. We self-host the snapshotting because their API is paid and X coverage is degraded post-2023 |
| **ReplyWisely / extension-based trackers** | Record follower counts from the logged-in browser session, no API | Fallback pattern if API costs block us: a browser extension or manual weekly CSV of follower counts for a small curated list |
| **memgraph/reddit-network-explorer** (GitHub) | Real-time Reddit graph + sentiment visualisation | Validation that reply-graph + sentiment is buildable; we stay batch/parquet instead of a graph DB until scale demands it |
| **samridhprasad/reddit-analysis** (GitHub) | PRAW-based: defines influencers as repeat top-posters in a subreddit | The cheap seed heuristic: influencers surface from top posts — but we improve it with PageRank on the reply graph |
| **PageRank-on-social literature** | PageRank as influence centrality is standard and well-studied | Use `networkx.pagerank` on the weighted interaction graph; no need to invent a score |
| **fintwit-bot** (already studied) | Curated Twitter lists as the influencer universe | The curated-seed idea: hand-pick 50–200 known fintwit accounts as the X starting universe instead of trying to discover from scratch |

Key negative finding from research: **X removed follower/following LIST
endpoints from all self-serve tiers (Basic/Pro) — Enterprise contract
only.** Follower COUNTS are still available cheaply via user lookup.
This single fact shapes the whole X design below.

---

## 2. Defining "influence" (three measurable components)

No single number is "influence". Track three orthogonal components and
combine them into one score at the end:

1. **REACH** — how many people COULD see this account.
   - X: `followers_count` (from user lookup `public_metrics`) —
     snapshot daily, influence-relevant signal is the **Δ and Δ%**
     (Social-Blade style), not the level.
   - Reddit: there is NO follower API. Proxy = **karma velocity**
     (comment+link karma snapshots, daily Δ) + subreddit-weighted post
     visibility (sum of scores on their posts in our tracked subs).
2. **ENGAGEMENT RECEIVED** — how much the crowd actually responds.
   - X: sum of likes/replies/reposts/quotes on their posts
     (`public_metrics` per tweet — we already collect tweets).
   - Reddit: replies received (from the comment reply graph), scores on
     their submissions/comments.
3. **NETWORK POSITION** — being replied to by people who are themselves
   replied to. **PageRank on the interaction graph** (see §4.3). This is
   what separates a genuine node from a loud account nobody engages with.

**Composite influence score** (per account, per weekly window):

```
influence = 0.25 * z(reach_delta) + 0.35 * z(engagement) + 0.40 * z(pagerank)
```

z = cross-sectional z-score within the window (so the three parts are
comparable). Weights are a starting judgement call — log them in
design_decisions.xlsx and calibrate later against "did this account's
stance-change lead theme sentiment?" (§8).

---

## 3. Data access matrix — what is actually possible (checked 2026)

### Reddit

| Need | Access | Reality |
|---|---|---|
| Historical posts | ✅ have it | our posts.parquet (submissions, 15 subs, 2008–2025) |
| **Historical COMMENTS** | ✅ free, big download | The Academic Torrents / Pushshift-style dumps ship `RC_*` comment files alongside the `RS_*` submissions we already ingested. **Required new ingestion** — the reply graph lives in comments (`parent_id` field links child→parent). Budget: comments are ~5-10x submission volume; ingest only the 15 tracked subs, same prep pipeline pattern |
| Live posts/comments | ✅ free | PRAW, 100 req/min (credentials already spec'd in `.env`) |
| User karma | ✅ free | PRAW `redditor.link_karma`, `comment_karma` — snapshot daily for tracked accounts |
| Follower lists | ❌ does not exist | Reddit has profile followers but exposes NO API for them. **Audience = engagers** (users who reply to the influencer) — actually a BETTER signal than passive followers |
| User post history | ✅ free | PRAW `redditor.submissions.new(limit=100)` — for stance extraction |

### X / Twitter

| Need | Access | Reality |
|---|---|---|
| Follower COUNT of an account | ✅ paid tier | `GET /2/users/by?usernames=a,b,c&user.fields=public_metrics` — 100 accounts per request. A 200-account universe = 2 requests/day. Trivial quota |
| **Follower LISTS** | ❌ Enterprise only | `GET /2/users/:id/followers` was removed from Basic/Pro (self-serve) in the 2023+ API era. DO NOT design around it. **Audience = engagers** here too |
| Engagers (replies/quotes/mentions) | ✅ paid tier | recent search: `query="to:handle OR @handle"` or `conversation_id:` lookups. Costs read quota — budget in §6 |
| Influencer's own posts | ✅ paid tier | recent search `from:handle` (7-day window) — cheap for a 200-account list |
| Historical follower counts | ❌ nobody sells this cheap | You cannot backfill what you never snapshotted. **Start snapshotting on day 1** — the time series only exists from then on. (Social Blade has partial history; their paid API is the only shortcut) |

### The unified honest architecture that falls out of this table

> **Influence graph = INTERACTIONS, not follower lists, on both platforms.**
> Follower counts (X) and karma (Reddit) are snapshot time series for the
> REACH component only. The audience layer = engagers, sampled and run
> through the existing sentiment pipeline. Nothing in this spec needs an
> Enterprise contract.

---

## 4. Architecture (files, schemas, algorithms)

```
 seeds (curated + auto-discovered)          daily snapshots            our posts/comments data
        │                                        │                            │
        ▼                                        ▼                            ▼
 accounts registry ────────► account_snapshots.parquet          interaction_edges.parquet
 (data/reference/            (account, platform, date,          (date, src, dst, kind,
  influencers.csv)            followers, karma_link,             weight)   kind ∈ {reply,
        │                     karma_comment, n_posts)            mention, quote}
        │                                        │                            │
        │                                        └──────────┬─────────────────┘
        ▼                                                   ▼
 influencer posts (their text)              weekly influence engine (networkx):
        │                                    PageRank + engagement + reach Δ
        ▼                                    → influence_scores.parquet
 stance extraction (existing                 → communities.parquet (Louvain)
 extract_tickers + themes +                              │
 VADER sentiment per account)                            ▼
        │                                    audience layer: top-N engagers per
        ▼                                    influencer → their posts → sentiment
 account_stance.parquet                      → audience_sentiment.parquet
        └───────────────┬────────────────────────────────┘
                        ▼
             dashboard "Influence" tab (optimised/ package)
```

### 4.1 The accounts registry (`data/reference/influencers.csv`)

Columns: `handle, platform, added_date, source (curated|discovered), notes`.
Seeding strategy, in order of effort:
1. **Auto-discover from data we already have** (day-1, free): top 200
   Reddit authors by total post score in posts.parquet (excluding
   `[deleted]`, AutoModerator, known bots) + top 200 X authors by post
   count in the tweet dumps. One notebook cell.
2. **Curate** 50–100 known fintwit names by hand (the fintwit-bot
   approach). Quality beats coverage for the X paid-quota budget.
3. **Ongoing discovery**: any account entering the weekly PageRank top-50
   that is not in the registry gets auto-appended with `source=discovered`.

### 4.2 Snapshot collector (runs inside `run_daily.py`)

- Reddit: PRAW loop over registry accounts → karma + account age →
  append row per account to `account_snapshots.parquet`. ~1 req/account,
  400 accounts ≈ 4 minutes at polite pacing. Free.
- X: batched user lookup (100 handles/request) → followers_count,
  tweet_count → same snapshot file. 2-4 requests/day.
- **Append-only, dated, never revised** — this IS the follower-history
  asset that cannot be bought later. Snapshot from day 1 even if the rest
  ships months later.

### 4.3 Interaction graph builder

Edges from data we already/soon have:
- **Reddit replies** (needs the RC comment dumps + live PRAW comments):
  edge `commenter → parent_author`, weight = 1 per reply, optionally
  weight *= log(1+comment_score). This is the core graph.
- **Reddit mentions**: `u/username` regex in post/comment text → edge
  `author → mentioned`.
- **X**: from collected tweets: `in_reply_to` → reply edge;
  `@mentions` in text → mention edge; quotes → quote edge.
- Keep edges in LONG format with dates; the engine aggregates per window.

Weekly influence engine (pure Python, ~50 lines):

```python
import networkx as nx

def weekly_influence(edges_window: pd.DataFrame) -> pd.DataFrame:
    g = nx.DiGraph()
    for src, dst, w in edges_window[["src", "dst", "weight"]].itertuples(index=False):
        g.add_edge(src, dst, weight=g.get_edge_data(src, dst, {}).get("weight", 0) + w)
    pr = nx.pagerank(g, weight="weight")          # network position
    in_deg = dict(g.in_degree(weight="weight"))    # engagement received
    return pd.DataFrame({"account": list(pr), "pagerank": pr.values(),
                         "engagement": [in_deg.get(a, 0) for a in pr]})
```

Communities: `python-louvain` (`community.best_partition`) on the
undirected projection — produces the "Communities Detected" clusters
(they will naturally be theme-ish: an AI cluster, a gold cluster...).

Coordinated-group detection (the prototype's "detect coordinated groups"):
- **Text near-duplicates**: MinHash over post texts (`datasketch`
  library); accounts repeatedly posting near-identical ticker content
  within short windows = a coordination cluster. Flag, don't auto-exclude.
- **Temporal co-activity**: correlation of accounts' hourly posting
  vectors; sustained correlation > 0.9 across weeks = suspicious.

### 4.4 Stance extraction (what do influencers think?)

Reuse the existing pipeline wholesale — per influencer post:
`extract_tickers_from_text` (with screening) + `themes_in_text` +
`score_text` (VADER; FinTwitBERT upgrade path applies here MOST since
volumes are small). Aggregate per account × week × entity:
`account_stance.parquet(account, week, entity, n_posts, net_bullish)`.
This produces the prototype's per-account chips (`$SOL BULLISH`).

### 4.5 The audience layer (the second layer you asked for)

For each top-K influencer (K≈25 to control quota):
1. **Find the audience** = accounts that engaged with them in the window
   (reply edges pointing AT them; on X additionally `to:handle` search).
2. **Sample** up to N=50 engagers per influencer (dedup across weeks).
3. **Get the audience's thoughts**: those engagers' OWN recent posts —
   Reddit: already in our data (filter posts.parquet by author!) — free.
   X: `from:` searches cost quota; alternatively restrict audience
   analysis to what engagers said IN the reply threads themselves
   (already collected with the thread) — zero extra quota, and arguably
   the purest "what does the audience think about what X said" signal.
4. **Score** with the sentiment pipeline → `audience_sentiment.parquet
   (influencer, week, audience_n, audience_net_bullish, influencer_net_bullish)`.
5. **The headline metric: stance divergence** =
   `influencer_net_bullish − audience_net_bullish`, tracked over time.
   Alert-worthy states: influencer flips while audience hasn't (leader
   turning), audience flips first (the crowd front-running its guru).

### 4.6 Storage schemas (all parquet, all append-friendly)

```
account_snapshots : account, platform, date, followers, karma_link,
                    karma_comment, n_posts_total
interaction_edges : date, src, dst, kind(reply|mention|quote), weight, platform
influence_scores  : week, account, platform, pagerank, engagement,
                    reach_delta, influence (composite), rank, rank_change
communities       : week, account, community_id, community_label
account_stance    : account, week, entity(ticker|theme), n_posts, net_bullish
audience_sentiment: influencer, week, audience_n, audience_net_bullish,
                    influencer_net_bullish, divergence
```

---

## 5. Notebook plan (deliverables, in the project's numbering)

Build in this order; each is independently useful.

### `11_account_registry.ipynb`
- Cells: seed from posts.parquet top authors (with bot/deleted filters) →
  merge curated list → dedupe → save registry → summary table.
- Acceptance: registry ≥ 200 accounts; zero `[deleted]`/bot entries;
  re-running is idempotent.

### `12_influence_graph.ipynb`  (needs RC comment ingestion first — see §7)
- Cells: load reply/mention edges for the window → build weekly graphs →
  PageRank + engagement + communities → save influence_scores +
  communities → force-directed visual (networkx spring_layout, node size
  = PageRank, colour = community; matplotlib is fine, the dashboard gets
  an Altair/pyvis version later) → "Centrality Leaders" table.
- Acceptance: a hand-checkable sanity — DeepFuckingValue-class accounts
  must rank top-10 in a 2021 window; warm, deterministic runs (seeded
  layout).

### `13_influencer_tracker.ipynb`
- Cells: snapshot time series per account → Δ and Δ% tables (Social-Blade
  style: TODAY / THIS WEEK / custom) → composite influence score (§2) →
  "Most Influential Accounts" ranked cards with stance chips from
  account_stance → rank-change arrows... (charts, not arrow strings —
  house rule) → rank trajectory chart per account.
- Acceptance: influence is computed ONLY from trailing data; snapshots
  never rewritten; composite weights logged in xlsx.

### `14_audience_layer.ipynb`
- Cells: engager extraction per top-25 influencer → audience sampling →
  audience sentiment via existing pipeline → divergence time series →
  divergence chart (influencer line vs audience line per account) →
  flag table: biggest divergences this week.
- Acceptance: audience of a known bull influencer in a bull window reads
  bullish (sanity); Reddit-only mode works with zero X quota.

### Dashboard: "Influence" tab
- Served by new `optimised/influence.py`: ranked accounts table with Δ,
  stance chips, divergence flags; network graph (pyvis embedded HTML or
  Altair edge bundle — pyvis recommended for the force-directed look);
  communities list; all filtered by the existing timeframe pills.

---

## 6. Quota & cost budget (X paid tier, per day, 200-account registry)

| Job | Requests/day | Posts read/day |
|---|---|---|
| Follower-count snapshots (batched 100/req) | 2 | 0 |
| Influencer posts (`from:` searches, 25 top accounts) | 25 | ~250 |
| Engager threads (`to:`/conversation, 25 accounts) | 25–50 | ~500–1,000 |
| **Total** | **~75** | **~1,250** |

≈ 35–40k posts/month — sized for a Basic-like tier IF the engager job
runs on the top-25 only. Reddit side: everything is free within PRAW
limits. **Phase the rollout accordingly** (§9): the Reddit-only version
needs zero new spend and delivers ~70% of the value.

## 7. New ingestion required: Reddit COMMENT dumps

The single biggest data gap: our archive has submissions only, and the
reply graph lives in comments. Plan:
- Source: the same Academic Torrents ecosystem as our RS_ dumps ships
  RC_YYYY-MM comment files. Download for the 15 tracked subreddits only.
- Extend `prep_posts.py`'s pattern into `prep_comments.py`: stream,
  normalise to `(id, date, author, score, subreddit, parent_id, link_id,
  body)`, dedupe first-seen-wins → `data/processed/comments.parquet`.
- Size expectation: comments ≈ 5–10× submissions → plan for a 5–10 GB
  parquet; store `body` truncated to 2,000 chars to halve it.
- Live continuation: PRAW comment streams per subreddit inside
  `run_daily.py` once historical is in.

## 8. Integration hooks back into the main project

- **Influence-weighted mentions**: a second mention series where each
  post is weighted by its author's influence score AS OF THAT WEEK
  (point-in-time — this is the legitimate version of what score²
  couldn't do). Backtest it against the unweighted series in notebook 10;
  if influence-weighting doesn't beat raw counts, it dies like score² did.
- **Signal 6 for the conviction score**: "top-decile influencers net
  bullish on this theme this week" as a 6th check in the notebook-09
  stacking score (keep max score /5 by making it a tiebreaker first).
- **Divergence alerts on the dashboard**: audience-vs-influencer flips
  are exactly the "narrative spreading" moments the proposal wants traced.

## 9. Phased rollout (each phase ships something usable)

1. **Phase 1 — Reddit-only, zero new cost**: registry + karma snapshots +
   mention-edges from EXISTING posts data (no comments yet) + stance
   extraction. Ships notebooks 11 & 13 minus reply-PageRank.
2. **Phase 2 — comment dumps**: RC ingestion → true reply graph →
   notebook 12 (PageRank, communities, coordination detection). Still free.
3. **Phase 3 — X counts**: follower snapshots + influencer posts via the
   armed-but-off X key (2 extra requests/day on the existing plan).
4. **Phase 4 — audience layer at full depth**: engager threads on X,
   divergence metrics, dashboard tab. Needs the §6 budget.

## 10. Pitfalls & rules (learned the hard way elsewhere in this project)

- **Look-ahead**: never weight past posts with current influence
  (the score² lesson). Influence scores are weekly snapshots, joined
  point-in-time.
- **Survivorship**: suspended/deleted accounts vanish from live lookups —
  keep their snapshot history (that's the DELISTED_TICKERS lesson for
  accounts). A row of NaNs after suspension is information.
- **Bot inflation**: follower Δ is gameable; PageRank on interactions is
  much harder to fake — hence its 0.40 weight. Coordination flags (§4.3)
  should DISCOUNT influence, not add to it.
- **ToS/privacy**: store only public data, only public handles; no DMs,
  no scraping behind login (the curl.txt X trick stays out of this spec);
  aggregate audience sentiment rather than profiling small accounts —
  publish influencer-level, keep engager-level internal.
- **Warm-up**: composite influence needs ~4 weeks of snapshots before Δz
  is meaningful — same warm-up discipline as every other signal here.

## Addendum — the confirmed visual + the influencer-thoughts API calls

_User confirmed the target look: the samridhprasad/reddit-analysis style
network graph (bipartite users ↔ subreddits, spring layout, node size by
importance), plus sentiment of the most influential names pulled via API._

### A1. The bipartite graph (buildable TODAY from posts.parquet — Phase 1)

The reference image is a user↔subreddit bipartite graph. We can draw ours
before any new ingestion exists, because posts.parquet already has
`author` and `subreddit`:

```python
import networkx as nx
import matplotlib.pyplot as plt

# edges: author -> subreddit, weight = total post score there (visibility)
top = (posts[~posts.author.isin(["[deleted]", "AutoModerator"])]
       .groupby(["author", "subreddit"])["score"].sum().reset_index())
top = top[top["score"] > 500]                      # keep the graph readable

g = nx.Graph()
for a, s, w in top.itertuples(index=False):
    g.add_node(a, kind="user"); g.add_node(s, kind="sub")
    g.add_edge(a, s, weight=w)

pos = nx.spring_layout(g, k=0.35, seed=42)          # deterministic layout
sizes = [g.degree(n, weight="weight") for n in g]   # later: influence score
colors = ["#F9A825" if g.nodes[n]["kind"] == "user" else "#A3C9E2" for n in g]
plt.figure(figsize=(13, 11))
nx.draw_networkx(g, pos, node_size=[20 + s / 50 for s in sizes],
                 node_color=colors, edge_color="#CCCCCC", width=0.5,
                 font_size=7, with_labels=True)
```

Upgrade path: swap node size from degree to the §2 composite influence
score once notebook 13 exists; add ticker/theme nodes (tri-partite) for
the full prototype look; pyvis for the interactive dashboard version.

### A2. Influencer thoughts via API — the exact calls

**Reddit (free, PRAW)** — recent posts of one influencer, scored with the
EXISTING pipeline:

```python
import praw, os
reddit = praw.Reddit(client_id=os.environ["REDDIT_PERSONAL_USE"],
                     client_secret=os.environ["REDDIT_SECRET"],
                     username=os.environ["REDDIT_USERNAME"],
                     password=os.environ["REDDIT_PASSWORD"],
                     user_agent=os.environ["REDDIT_APP_NAME"])

def influencer_thoughts_reddit(handle: str, limit: int = 100) -> pd.DataFrame:
    rows = []
    for s in reddit.redditor(handle).submissions.new(limit=limit):
        rows.append({"date": pd.to_datetime(s.created_utc, unit="s"),
                     "title": s.title, "selftext": s.selftext or ""})
    df = pd.DataFrame(rows)
    from src.sentiment import add_sentiment
    from src.extract_tickers import extract_tickers_from_text
    scored = add_sentiment(df)          # -> per-post compound in [-1, 1]
    # entity stance: tickers each post mentions, via the screened extractor
    return scored
```

**X (paid tier)** — one request per influencer per day:

```python
params = {"query": f"from:{handle} -is:retweet",
          "max_results": 50,
          "tweet.fields": "created_at,public_metrics"}
r = requests.get("https://api.x.com/2/tweets/search/recent",
                 headers={"Authorization": f"Bearer {token}"}, params=params)
# -> score each tweet text with score_text(); aggregate to weekly
#    account_stance rows exactly like the Reddit side.
```

Both feed `account_stance.parquet` (§4.4) — same schema, same sentiment
engine, so the "Most Influential Accounts" cards can mix platforms.

## 12. EXACT implementation playbook (endpoints, payloads, state, tests)

_This section is the "given the API, what precisely do we need" level of
detail. An engineer should be able to build from here without opening the
API docs except to confirm current rate numbers._

### 12.1 Reddit — exact requirements

**Credentials** (already templated in `example.env`): a "script"-type app
→ `client_id` (REDDIT_PERSONAL_USE), `client_secret` (REDDIT_SECRET),
plus username/password of a dedicated account. PRAW handles OAuth token
refresh automatically. Rate limit: **100 requests/min per OAuth client**
(PRAW auto-throttles via the `X-Ratelimit-*` response headers — do not
build your own limiter, just don't run two clients with one app id).

**The four Reddit jobs and their exact mechanics:**

1. **Karma snapshots** (daily, per registry account):
   ```python
   r = reddit.redditor(handle)
   row = {"account": handle, "platform": "reddit", "date": today,
          "karma_link": r.link_karma, "karma_comment": r.comment_karma,
          "created_utc": r.created_utc}
   ```
   Failure modes to handle explicitly: `prawcore.exceptions.NotFound`
   (deleted account → write a row with NaN karma, keep the account),
   `Forbidden` (suspended → same), and shadowbanned accounts (raise 404
   too). **Never delete an account's history on failure — a NaN streak
   IS the signal that they died** (the account version of the
   DELISTED_TICKERS lesson).

2. **Influencer post history** (stance extraction, weekly):
   `r.submissions.new(limit=None)` and `r.comments.new(limit=None)` —
   **hard API ceiling: ~1,000 most-recent items per listing** (a Reddit
   platform limit, not a PRAW one). For prolific accounts, history beyond
   1,000 items comes from OUR dumps instead (filter posts.parquet /
   comments.parquet by author — free and unlimited). Rule: dumps for
   history, API only for the gap since the dump's end date.

3. **Live comment firehose** (feeds the reply graph forward):
   ```python
   for c in reddit.subreddit("wallstreetbets+stocks+...").stream.comments(skip_existing=True):
       save(c.id, c.created_utc, c.author, c.score, c.subreddit,
            c.parent_id, c.link_id, c.body[:2000])
   ```
   One streaming process covers all 15 subs (multireddit syntax). It
   yields ~100 items per poll internally; PRAW manages pacing. Run it as
   its own long-lived process (NOT inside run_daily) writing hourly
   jsonl.zst files; run_daily just ingests whatever accumulated.

4. **Historical reply graph from the RC dumps** — the fiddly detail that
   matters, `parent_id` decoding:
   - `parent_id = "t3_abc123"` → parent is a SUBMISSION with id `abc123`
     → resolve author by joining on posts.parquet `id`.
   - `parent_id = "t1_def456"` → parent is a COMMENT with id `def456`
     → resolve author by SELF-joining the comments table on `id`.
   Two-pass algorithm (memory-safe at 100M+ comments): pass 1 builds an
   `id → author` map for comments+submissions of the window (a parquet
   join, not a dict, if RAM is tight); pass 2 streams comments again,
   emitting `(date, commenter, parent_author, 'reply', weight)` edges.
   Drop rows where either side is `[deleted]`. Weight = 1 (optionally
   `1 + log1p(score)` — test both, log the choice).

### 12.2 X — exact requirements

**Credentials**: one Bearer Token (`X_BEARER_TOKEN` in `.env` — the
armed-but-off pattern already built for `fetch_x_live.py`).
Header on every call: `Authorization: Bearer <token>`.

**Endpoint contract table** (v2; verify current numbers at
docs.x.com the week you activate — tiers have been repriced repeatedly):

| Job | Endpoint + params | Response fields we consume | Cost notes |
|---|---|---|---|
| Follower snapshots | `GET /2/users/by?usernames=<up to 100, comma-sep>&user.fields=public_metrics,created_at,verified` | `data[].public_metrics.followers_count`, `.tweet_count`; `errors[]` lists suspended/renamed handles — write NaN rows, keep them | 2 req/day for 200 handles; reads do NOT count against the post cap (user objects ≠ posts) |
| Influencer posts | `GET /2/tweets/search/recent?query=from:HANDLE -is:retweet&max_results=50&tweet.fields=created_at,public_metrics&since_id=<watermark>` | text, created_at, like/reply/repost counts | consumes post cap; `since_id` watermark makes re-polls cheap |
| Engager threads | `GET /2/tweets/search/recent?query=to:HANDLE&max_results=100&expansions=author_id&user.fields=username,public_metrics` | replier usernames + their reply texts (the audience layer, zero extra calls for their thoughts) | the expensive job — top-25 influencers only |
| Volume without reading | `GET /2/tweets/counts/recent?query=from:HANDLE` | daily tweet counts | counts endpoints do NOT consume the post cap — use for activity monitoring before deciding whose posts to actually read |
| Pagination | `meta.next_token` → pass as `next_token=` | — | recent search covers the LAST 7 DAYS ONLY; full-archive search is a higher tier |

**State the collector must keep** (`data/reference/x_watermarks.json`):
per query, the highest tweet id seen (`since_id`) and a monthly
post-read ledger (`{month: reads_used}`) — the code refuses to fetch once
the ledger hits the tier cap minus a 10% safety margin.

**429 discipline**: on HTTP 429 read the `x-rate-limit-reset` response
header (epoch seconds), sleep until then OR abort the run and let the
next scheduled run resume from watermarks — never hot-retry.

### 12.3 OLD DATASETS to test everything BEFORE paying for anything

This is the answer to "what can we validate against historically":

| Dataset | What it contains | What it lets us TEST |
|---|---|---|
| **Archive Team Twitter Stream Grab** (archive.org/details/twitterstream, 2011→~2022, ~50 GB/month tarballs of hourly .bz2 JSON) | 1% sample of ALL tweets in original API JSON — **every tweet embeds the author's `user.followers_count` AT THAT MOMENT** | THE test asset. Filter months for cashtag tweets → any account appearing repeatedly yields a real historical FOLLOWER TIME SERIES (the thing you cannot buy) → validates the snapshot-Δ logic, the reach component, and Social-Blade-style tables end-to-end. Also carries reply/mention fields → historical X interaction edges |
| **Kaggle crypto/stock tweet dumps with `user_followers` columns** (e.g. the big "Bitcoin tweets" corpora, 2016–2022) | Millions of finance tweets, each row with the author's follower count at posting | Same follower-trajectory reconstruction, pre-filtered to finance, much smaller download than the stream grab |
| **Cha et al. "Million Follower Fallacy" (ICWSM 2010) + Kwak et al. WWW 2010 graphs** (twitter.mpi-sws.org / SNAP `twitter-2010`) | Complete 2009 follow graph (~1.9B edges) + tweets | Scale-tests the graph engine (PageRank on millions of nodes), and the paper's own finding is our calibration target: follower count correlates POORLY with retweet/mention influence — our composite must reproduce that divergence, or the weights are wrong |
| **Our own RS_ dumps + the matching RC_ comment dumps** (same Academic Torrents ecosystem) | 15 subreddits, 2008–2025, submissions (have) + comments (to ingest) | The full Reddit side historically: reply-graph PageRank, karma-proxy reconstruction (cumulative sum of an author's post/comment scores over time ≈ karma trajectory), community detection, coordination detection |
| **Our own mjw/stock_market_tweets (2015–2020) + financial-tweets (2023+)** | Finance tweets with authors + engagement | Stance extraction + engagement component per X account, historically, with zero new downloads |
| **Jan 2021 GME window** (in data we already have) | The most-documented influence event in retail history | The acceptance test: DeepFuckingValue-class accounts MUST rank top-10 for the window; if they don't, the engine is wrong, full stop |

**The replay harness** (how to use them): every collector writes raw
files; the influence engine only reads parquets. So testing = write a
small adapter per historical dataset that emits the SAME schemas
(`account_snapshots`, `interaction_edges`) from the old data, then run
the untouched engine over it. One engine, two feeds (replay vs live) —
the same live-parity trick as the rest of the project.

### 12.4 Validation experiments (run these before trusting anything)

1. **DFV test** (Reddit, free): build the Jan–Feb 2021 WSB reply graph
   from RC dumps → weekly PageRank → assert DeepFuckingValue in top 10.
2. **Million-follower-fallacy replication** (X, free): on a stream-grab
   month, rank accounts by follower count vs by our composite → Spearman
   correlation should be clearly < 1 (paper found follower rank ≠
   influence rank); if the two rankings match too well, the composite is
   just repackaging follower counts.
3. **Snapshot-Δ correctness** (free): follower series reconstructed from
   the stream grab → feed through the Δ/Δ% tables → spot-check five
   accounts against their known history.
4. **Divergence sanity** (free, our data): pick a 2021 influencer with a
   documented stance flip; audience sentiment (their repliers' posts)
   must be computable and non-degenerate around the flip date.

### 12.5 File/state inventory the implementation must create

```
data/reference/influencers.csv            # registry (append-only, source column)
data/reference/x_watermarks.json          # since_id per query + monthly read ledger
data/raw/RedditComments/RC_*.zst          # comment dumps (new ingestion)
data/raw/live_comments/YYYY-MM-DD_HH.jsonl.zst   # streaming collector output
data/processed/comments.parquet           # normalised comments (id, date, author,
                                          #   score, subreddit, parent_id, link_id, body)
data/processed/account_snapshots.parquet  # append-only, never revised
data/processed/interaction_edges.parquet
data/processed/influence_scores.parquet   # weekly, point-in-time
data/processed/account_stance.parquet
data/processed/audience_sentiment.parquet
```

## 13. Influential SUBREDDITS — the second map (separate from people)

_People and communities are different influence objects and get SEPARATE
maps: an influential person moves their audience; an influential
subreddit ORIGINATES narratives that other subreddits then echo. Track
both, then connect them through the bipartite bridge (people ↔ the subs
they post in — the A1 graph we can already draw)._

### 13.1 What makes a subreddit "influential" (four measurables)

1. **Size & growth** — subscriber snapshots. Exact call (free, PRAW):
   ```python
   s = reddit.subreddit("wallstreetbets")
   row = {"sub": s.display_name, "date": today,
          "subscribers": s.subscribers,          # total members
          "active": s.accounts_active}           # here-now count (noisy but useful intraday)
   ```
   Same snapshot discipline as accounts: daily rows, append-only, Δ and
   Δ% are the signal (a sub gaining 5%/week is where the next crowd is
   forming). Backfill option: subredditstats.com holds historical
   subscriber curves for spot-checking our snapshots' plausibility.
2. **Origination score — who mentions it FIRST.** For each ticker
   take-off (notebook 03 events), attribute the earliest surge: compute
   per-sub daily mentions of the ticker (posts.parquet has `subreddit` —
   free), find which sub's trailing z crossed K earliest. A sub that
   repeatedly originates take-offs that other subs echo 1-3 days later is
   upstream in the narrative flow. Concretely: the notebook-02 lead/lag
   cross-correlation machinery, run SUB-vs-SUB per ticker instead of
   Reddit-vs-X — the code already exists.
3. **Narrative flow edges (sub → sub graph):**
   - **Crossposts**: submissions carry `crosspost_parent_list` (in dumps
     and PRAW) → edge `origin_sub → reposting_sub`.
   - **Explicit references**: `r/subname` regex in post/comment text →
     edge `mentioning_sub → mentioned_sub`.
   - **User-overlap flow**: authors active in A this week who become
     active in B next week (migration edges) — computable entirely from
     posts.parquet author×sub×week counts.
   PageRank over THIS graph = subreddit influence rank; Louvain over it =
   the community clusters (crypto cluster, value cluster...).
4. **Per-sub stance**: we already compute sentiment per post — group by
   `subreddit` instead of theme and the existing pipeline yields each
   sub's net-bullish per ticker/theme per week ("WSB is bullish semis,
   r/stocks is neutral") with zero new code paths.

### 13.2 Test datasets specific to the subreddit map

| Dataset | What it is | What it tests |
|---|---|---|
| **SNAP `soc-RedditHyperlinks`** (Stanford, 2014–2017) | 858k subreddit→subreddit hyperlink edges from posts, EACH LABELLED with sentiment of the linking post | The sub→sub graph engine end-to-end on a canonical corpus, including sentiment-signed edges; their companion embeddings (`web-RedditEmbeddings`) sanity-check our Louvain clusters |
| **Our posts.parquet** | 15 subs, 2008–2025 | Origination scores + migration edges + per-sub stance, historically, today, free |
| **subredditstats.com curves** | Historical subscriber counts | Plausibility checks for the growth component before our own snapshots accumulate |
| **Jan 2021 again** | — | Acceptance test: WSB must dominate origination for GME/AMC; satellite squeeze subs (e.g. Superstonk, created Mar 2021) must appear as DOWNSTREAM nodes that then grow explosively in the subscriber Δ table |

### 13.3 How the two maps connect (and land on the dashboard)

- `subreddit_snapshots.parquet (sub, date, subscribers, active)` and
  `subreddit_edges.parquet (date, src_sub, dst_sub, kind(crosspost|
  mention|migration), weight)` join the §4.6 schema list;
  `influence_scores` gains a `node_type ∈ {account, subreddit}` column so
  one table serves both maps.
- Dashboard Influence tab gets a toggle: **People map | Subreddit map |
  Bridge** (bipartite people↔subs, §A1 — node size from the respective
  influence score). The origination table ("which sub called it first,
  by how many days") sits beside the subreddit map — that single table
  is arguably the most tradeable artifact in this whole spec.
- Definition-of-done additions: subreddit snapshots running daily;
  origination scores reproduce WSB-first for Jan-2021 GME; sub→sub
  PageRank stable week over week (rank churn < 30% on quiet weeks).

## 11. Definition of done

- [ ] Registry with ≥200 accounts, idempotent rebuild
- [ ] ≥30 consecutive days of snapshots, append-only, gap-flagged
- [ ] Weekly influence_scores with PageRank sanity check passing (known
      2021 meme leaders rank top-10 in 2021 windows)
- [ ] Stance chips per top-25 account per week
- [ ] Audience divergence series for top-25, Reddit-only mode working
- [ ] Dashboard Influence tab reading only from optimised/influence.py
- [ ] Every design choice appended to design_decisions.xlsx with dates
- [ ] Zero Enterprise-only API calls anywhere in the code
