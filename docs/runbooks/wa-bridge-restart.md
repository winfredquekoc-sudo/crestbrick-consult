# Runbook: WhatsApp Bridge Restart

**Service label**: `com.crestbrick.whatsapp-bridge`
**Last updated**: 2026-07-05
**Owner**: Winfred Quek (operations)

---

## Before you run this runbook

Two separate components share the "WhatsApp" name. Knowing which one is down saves you
from running the wrong recovery steps.

| Component | What it is | Managed by | Auto-restart |
|---|---|---|---|
| Go bridge (`whatsapp-bridge`) | Holds the WhatsApp Web session. Exposes REST API on port 8080. Writes `messages.db`. | launchd (`com.crestbrick.whatsapp-bridge`, `KeepAlive true`) | Yes, within 30s |
| Python MCP server (`main.py`) | stdio subprocess that lets Claude read messages and contacts. Spawned by the Claude app. | Claude app process | Only when Claude reopens a session |

The Go bridge is extremely stable. In 1137 hourly watchdog runs it has had only 5
down events. If you or another agent are experiencing WhatsApp tool failures inside a
Claude session, the MCP server (Python process) is almost certainly what dropped, not
the bridge.

**Check which component is actually down before proceeding:**

```zsh
# Is the bridge process running?
pgrep -fl whatsapp-bridge

# Does the bridge REST API respond? (4xx = up; 000 = down)
curl -s -m 5 -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{}'
```

If the bridge is up (process running AND HTTP 4xx response), but Claude WhatsApp tools
are failing, the MCP server dropped. Fix: restart the Claude app (or disconnect and
reconnect the `whatsapp` MCP in Claude settings). You do not need this runbook.

If the bridge is down (no process OR HTTP 000), continue below.

---

## File locations

| Item | Path |
|---|---|
| Binary | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge` |
| Backup binary | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge.backup-20260425` |
| Working directory | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/` |
| Session database | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db` |
| Messages database | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/messages.db` |
| Store directory | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/` |
| Stdout log | `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log` |
| Stderr log | `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log` |
| Bridge plist | `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist` |
| Watchdog plist | `~/Library/LaunchAgents/com.crestbrick.wa-bridge-http-check.plist` |
| Watchdog script | `/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh` |
| Watchdog stdout | `/Users/winfredquek/.claude/bin/wa-bridge-http-check.out.log` |
| Watchdog stderr | `/Users/winfredquek/.claude/bin/wa-bridge-http-check.err.log` |
| Watchdog state file | `/Users/winfredquek/.claude/state/wa-bridge-http-state.txt` |
| Telegram env | `/Users/winfredquek/.telegram-bot.env` |
| REST API | `http://127.0.0.1:8080` |
| v4 pipeline runner | `/Users/winfredquek/crestbrick-consult/src/wa-pipeline/wa_intake_runner.py` |
| Pipeline plist (active) | `~/Library/LaunchAgents/com.crestbrick.wa-intake.plist` |
| Pipeline runner log | `/Users/winfredquek/.claude/state/listing-templates/runner.out.log` |

---

## Context

The bridge is a Go binary (`whatsapp-bridge`) built on whatsmeow. It connects to WhatsApp
as a linked device and exposes a local REST API on port 8080.

**v4 pipeline (live as of 2026-06-12):** Inbound messages are polled by
`com.crestbrick.wa-intake` (runs `wa_intake_runner.py` every 120 seconds). This is the
single authorised sender. The old `com.crestbrick.wa-pipeline` plist is disabled and the
n8n autoreply workflow is deactivated (double-send risk). Do not re-enable either without
checking the current pipeline state first.

If the bridge is down, `wa-intake` will fail silently on every poll cycle. Inbound lead
qualification and all outbound reply logic stop entirely.

The bridge itself runs as `com.crestbrick.whatsapp-bridge` with `KeepAlive true` and
`ThrottleInterval 30`, so crash restarts are automatic within 30 seconds. The watchdog
job `com.crestbrick.wa-bridge-http-check` runs hourly and sends a Telegram alert
(@Maddieprop_bot) on any healthy-to-down or down-to-healthy transition. The watchdog
alerts; launchd restarts.

**Pausing without a restart:** If you need to pause outbound processing without taking down
the bridge (e.g. during a maintenance window or to investigate bad messages), send `/hold`
or `/pause` via Telegram to the bot. This stops the pipeline from sending replies without
disconnecting the bridge session.

---

## 0. You have been alerted via Telegram

If you received a Telegram alert saying the WA bridge is unhealthy, the bridge is confirmed
down. Skip section 1 and go directly to section 2. Use the log indicators in section 1c
only to identify which recovery path in section 5 to follow.

If you are investigating without an alert, run section 1 first.

---

## 1. Detect the disconnect

Run checks 1a through 1d in order. One failure confirms the bridge is down.

### 1a. Process check

```zsh
pgrep -fl whatsapp-bridge
```

No output means the process is not running. With `KeepAlive true`, launchd should
auto-restart within 30 seconds (`ThrottleInterval`). If the process is still absent
after 60 seconds, proceed to section 2.

### 1b. REST API health probe

```zsh
curl -s -m 5 -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{}' || echo "CANNOT CONNECT"
```

- `HTTP 400` or `HTTP 422` — server is up (bad payload is the correct response)
- `CANNOT CONNECT` or `HTTP 000` — process crashed or stuck at startup

### 1c. Log scan

```zsh
tail -60 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
tail -20 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log
```

Healthy indicators:

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
Connected to WhatsApp!
```

Bad indicators — note which one, as it determines the recovery path:

```
Device logged out, please scan QR code to log in again   -> section 5b
405 client-outdated                                       -> section 5c
listen tcp 127.0.0.1:8080: bind: address already in use  -> section 5d
database is locked                                        -> section 5e
unable to open database file                              -> section 5e
stream:error (with code 401 or 403)                      -> section 6 (security — do not restart)
stream:error (any other code)                             -> section 2 (standard restart)
```

### 1d. launchd job status

```zsh
launchctl list | grep whatsapp-bridge
```

Output format: `<PID>   <last-exit-code>   com.crestbrick.whatsapp-bridge`

- PID present, exit code 0 = running normally
- PID `-`, exit code non-zero = crashed; launchd is in throttled backoff

---

## 2. Standard restart (launchd managed — do this first)

Use this for all crashes, silent disconnects, and stream errors (except 401/403).

```zsh
# Stop the job (kills the process)
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Confirm it has stopped
pgrep -fl whatsapp-bridge && echo "still running — wait 5s and check again" || echo "stopped"

# Reload (RunAtLoad = true, so the process starts immediately)
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Watch startup output
tail -f /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

Expected output within 3-8 seconds of reload:

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
Connected to WhatsApp!
```

If `Scan this QR code` appears instead, the session was wiped. Go to section 5b.

Press Ctrl-C to stop tailing once the connected message appears.

---

## 3. Manual restart (if launchd is not managing the job)

Use this only if `launchctl list | grep whatsapp-bridge` returns nothing at all,
indicating the job is not loaded.

```zsh
# Kill any stale zombie process
pkill -x whatsapp-bridge 2>/dev/null; sleep 2

# Re-register the job with launchd (also starts it due to RunAtLoad = true)
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Confirm the job is loaded
launchctl list | grep whatsapp-bridge

# Watch startup
tail -f /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

If launchd load fails (plist not found or permission error), run the binary directly
as a fallback while you investigate:

```zsh
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
nohup ./whatsapp-bridge \
  >> /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log 2>&1 &
echo "PID $!"
```

This fallback is temporary. Restore launchd management as soon as possible so
crash recovery is automatic.

---

## 4. Automated recovery via launchd (current production config)

The plist at `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist` provides
the following guarantees as of 2026-07-05:

| Key | Value | Effect |
|---|---|---|
| `KeepAlive` | `true` | Restarts the process on any exit, including crash |
| `ThrottleInterval` | `30` | 30-second backoff between crash-restarts |
| `RunAtLoad` | `true` | Starts on login and on any `launchctl load` |
| `WorkingDirectory` | `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge` | Binary resolves `store/` as a relative path from here |

Do not reduce `ThrottleInterval` below 10. Do not remove `KeepAlive`.

To apply any plist changes:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
# Edit the plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

The watchdog (`com.crestbrick.wa-bridge-http-check`) runs every 3600 seconds and
complements launchd: launchd handles process-exit crashes; the watchdog catches
silent disconnects where the process is alive but not delivering messages. The
watchdog does NOT auto-restart the bridge.

---

## 5. Confirm reconnection

All three checks must pass before the bridge is considered restored.

```zsh
# 1. Process is alive
pgrep -fl whatsapp-bridge

# 2. REST listener is up (expect HTTP 400, not a connection error)
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}'

# 3. Log confirms WhatsApp connection (not just process running)
grep "Connected to WhatsApp" \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log | tail -1
```

End-to-end smoke test — send a message to yourself:

```zsh
# Replace +65XXXXXXXX with your actual Singapore number
curl -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"+65XXXXXXXX","message":"bridge restart test - ok"}'
```

Expected response: `{"success":true}`. The message should appear on the phone within
a few seconds. If the API returns success but the message does not arrive on the phone,
the bridge has a silent disconnect — force a full restart (section 2).

Also confirm the v4 pipeline runner is still active after the restart:

```zsh
launchctl list | grep wa-intake
tail -5 /Users/winfredquek/.claude/state/listing-templates/runner.out.log
```

---

## 6. Specific failure modes

### 6a. Silent disconnect (process alive, messages not delivering)

Symptom: process is running, REST API responds with HTTP 4xx, but outbound messages are
not received and inbound leads are not being picked up. The bridge's internal
`IsConnected()` state is false. `KeepAlive` cannot detect this because the process has
not exited. The watchdog will alert on the next hourly cycle.

Fix: force a full restart via launchd (section 2). No state needs to be cleared.

---

### 6b. "Device logged out" — QR re-pairing required

Symptom: log contains `Device logged out, please scan QR code to log in again`.

Cause: the linked device entry was removed from the phone (Settings > Linked Devices),
or the WhatsApp phone was offline or inactive for more than 14 days.

Fix: the QR code must be displayed in an interactive terminal. launchd cannot render it.

```zsh
# 1. Stop the launchd job
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# 2. Wipe the stale session (it is invalid at this point — the credentials are revoked)
rm -rf /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/

# 3. Run the binary interactively — the QR code prints to the terminal
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
./whatsapp-bridge
```

On the phone: WhatsApp > Settings > Linked Devices > Link a Device. Scan the QR.

```zsh
# 4. Wait for "Connected to WhatsApp" to appear in the terminal output
# 5. Press Ctrl-C to stop the interactive session
# 6. Hand control back to launchd
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Run section 5 verification after reload.

---

### 6c. 405 client-outdated

Symptom: log contains `405 client-outdated`. WhatsApp rejected the whatsmeow protocol
version — the library is out of date.

Fix: rebuild the binary against the latest whatsmeow. Always back up the working binary
first.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge

# Back up the current binary before touching anything
cp whatsapp-bridge whatsapp-bridge.backup-$(date +%Y%m%d)

go get -u go.mau.fi/whatsmeow@latest
go mod tidy
go build -o whatsapp-bridge .

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Known compile-time issue from the 2026-04-18 whatsmeow upgrade: API changes required
adding `context.Background()` as a first argument to `client.Download`, `sqlstore.New`,
`container.GetFirstDevice`, `client.GetGroupInfo`, and
`client.Store.Contacts.GetContact`. If the build fails, inspect `main.go` at those call
sites.

If `405 client-outdated` returns immediately after a fresh build, there is a breaking
upstream change requiring source edits to `main.go`. Stop the service and escalate to the
developer agent.

---

### 6d. Port 8080 in use (crash-loop on startup)

Symptom: log contains `listen tcp 127.0.0.1:8080: bind: address already in use`. A prior
process did not exit cleanly and still holds the port.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Kill whatever is holding port 8080
lsof -ti tcp:8080 | xargs kill -9

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

---

### 6e. SQLite database locked or corrupted

Symptom: log contains `database is locked` or `unable to open database file`.

The store directory contains the following files. Know what each one is before deleting
anything:

| File | Role | Safe to delete? |
|---|---|---|
| `whatsapp.db` | Session credentials and linked-device identity | No — deleting forces QR re-pair |
| `messages.db` | Message history cache used by the v4 pipeline runner | Yes — loss of history only, no session impact |
| `*.db-wal` | SQLite write-ahead log artifact | Yes — always safe to delete |
| `*.db-shm` | SQLite shared-memory artifact | Yes — always safe to delete |

Fix for locked WAL artifacts (try this first):

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-wal
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-shm

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Fix if messages.db is corrupt (still failing after WAL removal):

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/messages.db

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Do not delete `whatsapp.db` unless you are intentionally triggering a QR re-pair and are
prepared to do section 6b immediately after.

---

## 7. Escalation path

Stop the service before escalating. Do not keep restart-looping — repeated restarts during
a ban or account security event worsen the situation.

| Condition | Action |
|---|---|
| QR re-pair succeeds but bridge disconnects again within minutes | Possible temporary WhatsApp ban or rate limit. Leave the service stopped. Wait 24 hours before the next attempt. |
| `405 client-outdated` returns immediately after a fresh whatsmeow rebuild | Breaking upstream API change — requires source edits to `main.go`. Stop the service; escalate to developer agent. |
| `stream:error` with code `401` or `403` in logs | Account security event. Do not restart. Check WhatsApp on the phone for security alerts immediately. Escalate to Winfred directly. |
| Linked device quota exhausted | WhatsApp allows 4 linked devices. Open WhatsApp on the phone > Settings > Linked Devices. Remove a stale entry, then do section 6b. |
| Bridge down more than 2 hours with no recovery | Inbound leads, qualifier triggers, and v4 pipeline processing are entirely blocked. Winfred must handle urgent client communications manually by phone. File a timestamped ops incident note. |

---

## 8. Prevention

### Watchdog health check

The watchdog script `/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh` runs every
3600 seconds via `com.crestbrick.wa-bridge-http-check`. It probes
`http://localhost:8080/api/send`, writes its state to
`~/.claude/state/wa-bridge-http-state.txt`, and sends a Telegram message on any
transition (healthy to down, or down to healthy).

Check current watchdog state:

```zsh
cat /Users/winfredquek/.claude/state/wa-bridge-http-state.txt
```

Check watchdog is loaded:

```zsh
launchctl list | grep wa-bridge-http-check
```

If it is not loaded:

```zsh
launchctl load ~/Library/LaunchAgents/com.crestbrick.wa-bridge-http-check.plist
```

Note: `RunAtLoad false` on the watchdog plist — it fires only after the first 3600-second
interval, not immediately on load.

Run the watchdog manually to force an immediate health check or test alert:

```zsh
/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh
cat /Users/winfredquek/.claude/state/wa-bridge-http-state.txt
```

The `com.crestbrick.heartbeat-watchdog` job monitors the bridge process and the
Telegram bot, but NOT the Python MCP server (that monitor was removed 2026-06-27 because
the MCP server is a short-lived stdio subprocess, not a daemon; process-liveness checks
on it produced false alerts every time a Claude session closed). Do not re-add MCP server
process checks to the heartbeat watchdog.

### v4 pipeline sender discipline

Only `com.crestbrick.wa-intake` should be sending messages. The old
`com.crestbrick.wa-pipeline.plist` is disabled and n8n autoreply is deactivated. Do not
re-enable either without confirming they will not send duplicate replies alongside the
active runner. Before any pipeline change, use `/hold` or `/pause` via Telegram to pause
outbound processing.

### Session longevity

- Keep the WhatsApp phone online and reachable at least every 10 days. The linked-device
  session expires after approximately 14 days of phone inactivity.
- Do not remove the `com.crestbrick.whatsapp-bridge` linked-device entry from the phone
  unless deliberately retiring the integration.
- Back up the session store before any binary upgrade or store manipulation:

```zsh
cp -r /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/ \
  /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store.backup-$(date +%Y%m%d)
```

### Log rotation

The stdout and stderr logs grow unbounded. Trim manually when either exceeds 10 MB:

```zsh
for f in \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log; do
  tail -1000 "$f" > /tmp/wa-trim.log && mv /tmp/wa-trim.log "$f"
done
```

---

## Quick-reference decision tree

```
Seeing WhatsApp failures? Start here.
  |
  +-- Is the failure inside a Claude session (MCP tools not working)?
  |     Check: pgrep -fl whatsapp-bridge AND curl probe above
  |     Bridge up (process + HTTP 4xx) -> MCP server dropped. Restart Claude app.
  |     Bridge down -> continue below.
  |
  +-- pgrep -fl whatsapp-bridge returns nothing?
  |     YES -> Section 2 (launchd restart)
  |
  +-- curl returns CANNOT CONNECT or HTTP 000?
  |     YES -> Section 2 (launchd restart)
  |
  +-- curl returns HTTP 4xx but messages not delivering?
  |     YES -> Silent disconnect. Section 6a then Section 2 (force restart, no state wipe)
  |
  +-- What does the log say?
        "Device logged out"               -> Section 6b (QR re-pair)
        "405 client-outdated"             -> Section 6c (rebuild)
        "address already in use"          -> Section 6d (kill stale port)
        "database is locked"              -> Section 6e (remove WAL files)
        "stream:error" code 401 or 403    -> Section 7 (security — do not restart)
        "stream:error" any other code     -> Section 2 (launchd restart)

Need to pause without restarting?
        Send /hold or /pause via Telegram -> pauses v4 pipeline, bridge stays connected
```
