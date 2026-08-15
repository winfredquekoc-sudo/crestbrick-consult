# serp-snapshot.sh backend is down — diagnosis and options

16 Aug 2026. `scripts/serp-snapshot.sh` depends on a local SearXNG instance at
`http://localhost:8888`. It has been unreachable since at least 5 Aug (the only
row in `~/.claude/state/serp-history.csv` past that date is the script's own
"unreachable" marker — it fails soft by design, it does not fabricate ranks).

## Diagnosis

Checked every place a local SearXNG could plausibly be running. All negative:

| Check | Result |
|---|---|
| `launchctl list \| grep -i searx` | no job |
| `docker ps -a \| grep -i searx` | Docker itself is not installed on this machine (`command -v docker` empty) |
| `brew services list \| grep -i searx` | no service |
| `~/searxng*`, `/opt/searxng` | do not exist |
| `curl localhost:8888` | `HTTP 000` (connection refused, nothing listening) |
| `lsof -i :8888` | nothing bound to the port |
| `pip3 list` / `pipx list` | no searxng package |
| filesystem search for `*searxng*` under `$HOME` | only the MCP **client** cache dirs (`mcp-logs-searxng` under `~/Library/Caches/claude-cli-nodejs/...`) — that's `npx mcp-searxng`, a thin JSON-RPC-to-HTTP bridge configured in `~/.claude.json`. It still needs a real SearXNG server at `localhost:8888` to talk to, and there isn't one. |

**Conclusion: there is no existing local SearXNG installation to restart.** Whatever
was reachable at localhost:8888 on 5 Aug either ran from a terminal session /
process that's since exited (not a managed service — nothing survived a reboot
or logout), or was never fully installed as a persistent service in the first
place. Per instructions, nothing was installed to fix this now — the two paths
below are written up for a decision, not executed.

## Before choosing: the "free API swap" landscape changed in 2026

Both major free-tier hosted search APIs that would normally be the obvious
swap-in for a broken local search backend are gone as of this writing
(verified via web search, 16 Aug 2026):

- **Bing Web Search API** — fully retired by Microsoft. Announced 15 May 2025,
  new signups disabled Feb 2025, all existing instances decommissioned
  **11 Aug 2025**. It is not a live option at all anymore, at any price. The
  suggested replacement (Grounding with Bing Search inside Azure AI Foundry)
  is not a drop-in search API — it's an LLM-grounding product that requires
  standing up an Azure AI Agents project.
- **Brave Search API** — lost its genuine free tier in **Feb 2026**. New
  accounts now get a $5/month credit (~1,000 queries) on metered billing and
  need a card on file; keeping even that $5 credit requires public attribution
  of Brave on the site using it. Existing pre-Feb-2026 free subscribers keep
  up to 2,000 free queries/month, grandfathered — Crestbrick has no such
  account. So this isn't "free" either, just cheap-if-you-opt-in-to-billing.

Net effect: **as of Aug 2026 there is no truly free hosted search API left**
for this use case. Option A (fix the local SearXNG) is the only genuinely free
path. Option B is documented below because it was asked for, but it points at
a product that no longer exists — treat it as a historical reference, not a
plan.

## Option A — stand up SearXNG properly (free, self-hosted, recommended)

Nothing to "restart" — needs a real install. SearXNG's own project supports
this cleanly via Docker, which is the path most likely to survive reboots and
match how `serp-snapshot.sh` already expects it (plain HTTP on 8888, JSON
format enabled).

1. Install Docker (not currently present): `brew install --cask docker` (Docker
   Desktop) or a lighter daemon-only alternative like OrbStack
   (`brew install --cask orbstack`) or Colima (`brew install colima docker`).
2. Run SearXNG with JSON output enabled (off by default — `serp-snapshot.sh`
   requests `format=json` and needs the backend to allow it):
   ```bash
   mkdir -p ~/searxng-data
   cat > ~/searxng-data/settings.yml <<'EOF'
   use_default_settings: true
   server:
     secret_key: "$(openssl rand -hex 32)"
   search:
     formats:
       - html
       - json
   EOF
   docker run -d --name searxng --restart unless-stopped \
     -p 8888:8080 \
     -v ~/searxng-data:/etc/searxng \
     searxng/searxng:latest
   ```
   `--restart unless-stopped` is the load-bearing bit — it's what makes this
   survive a reboot without a launchd job, since Docker Desktop/OrbStack itself
   auto-starts on login and reattaches the container.
3. Verify: `curl -s http://localhost:8888/search?q=test\&format=json | head -c 200`
   should return JSON, not a connection error.
4. Re-run the real snapshot: `scripts/serp-snapshot.sh` (backfills today's
   positions for the 20 queries in `scripts/serp-queries.txt`).
5. Optional hardening once it's confirmed stable: add a tiny launchd job that
   runs `docker start searxng` at login as a second line of defense (belt and
   suspenders alongside `--restart unless-stopped`), and a weekly
   `docker pull searxng/searxng:latest` to pick up security fixes.

Cost: free (self-hosted, no API key, no usage limit beyond your own machine's
capacity). Downside: it's a standing local service that needs Docker running,
which is one more thing that can silently stop working — worth a periodic
reachability check (the existing `serp-snapshot.sh` already fails soft and
logs "unreachable" rather than faking data if this happens again, so silent
failure degrades to "gap in the CSV," not "wrong numbers").

## Option B — Bing Web Search API swap (documented as asked; the API is dead)

**Do not implement this.** Bing Web Search API was decommissioned 11 Aug 2025
(see above) — this section exists only because it was explicitly requested as
one of the two options to document. The code sketch shows what the swap
would have looked like, for the record.

```bash
# Old: SearXNG JSON search
RESPONSE="$(curl -s --max-time 10 -G "$SEARXNG_URL/search" \
  --data-urlencode "q=$QUERY" \
  --data-urlencode "format=json" \
  --data-urlencode "language=en")"
POSITION="$(printf '%s' "$RESPONSE" | jq -r --arg domain "$DOMAIN" --argjson topn "$TOP_N" '
    (.results // [])[0:$topn] | map(.url // "") | to_entries[]
    | select(.value | test($domain)) | (.key + 1)' | head -1)"
```
```bash
# Sketch of the Bing v7 equivalent (BING_API_KEY from an Azure Cognitive
# Services resource — the resource type itself no longer exists to create)
RESPONSE="$(curl -s --max-time 10 -G "https://api.bing.microsoft.com/v7.0/search" \
  -H "Ocp-Apim-Subscription-Key: $BING_API_KEY" \
  --data-urlencode "q=$QUERY" \
  --data-urlencode "mkt=en-SG" \
  --data-urlencode "count=$TOP_N")"
POSITION="$(printf '%s' "$RESPONSE" | jq -r --arg domain "$DOMAIN" --argjson topn "$TOP_N" '
    (.webPages.value // [])[0:$topn] | map(.url // "") | to_entries[]
    | select(.value | test($domain)) | (.key + 1)' | head -1)"
```
The only structural change is the response shape (`.webPages.value[].url`
instead of `.results[].url`) and swapping the query-string auth for a header.
Everything else in `serp-snapshot.sh` (the CSV row format, the reachability
check, the fail-soft behavior, the sleep between queries) would carry over
unchanged. Moot until/unless Microsoft ships a genuine successor API.

**Never scrape Google directly** for this — ToS risk and no rate-limit
courtesy path — which is exactly why `serp-snapshot.sh` was built around
SearXNG in the first place. Worth noting in the same breath: `~/.claude/bin/
keyword-rank-tracker.sh` (Monday 06:00 SGT, `com.crestbrick.keyword-rank-
tracker.plist`) already does scrape `google.com/search` directly today for a
separate 10-keyword list. That predates this SearXNG effort and still runs.
Flagging it here since it's directly relevant to "never scrape Google
directly," but fixing it is outside this task's scope.

## Recommendation

Option A. It is free, matches what `serp-snapshot.sh` already expects with
zero code changes, and both hosted alternatives that used to make Option B
attractive are gone or no longer free. The only decision is whether Docker
Desktop / OrbStack / Colima is an acceptable thing to install — that install
step needs a go-ahead since it wasn't done as part of this task.
