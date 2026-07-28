# Runbook: WhatsApp Bridge Restart

**Service label**: `com.crestbrick.whatsapp-bridge`
**Binary**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`
**Working directory**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/`
**REST API**: `http://127.0.0.1:8080`
**Plist**: `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist`
**Stdout log**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log`
**Stderr log**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log`
**Session store**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/`
**Watchdog script**: `/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh`
**Watchdog plist**: `~/Library/LaunchAgents/com.crestbrick.wa-bridge-http-check.plist`
**Intake engine runner**: `~/Library/LaunchAgents/com.crestbrick.wa-intake.plist`
**Last updated**: 2026-07-12

---

## Architecture in one paragraph

The bridge is a whatsmeow-based Go binary. It holds a persistent WebSocket session to WhatsApp, writes all incoming messages to SQLite (`store/messages.db` and `store/whatsapp.db`), and exposes a REST API on `http://127.0.0.1:8080`. Two things depend on it: (1) the intake engine (`wa_intake_runner.py`, run by `com.crestbrick.wa-intake` every 60 seconds), which reads `messages.db` and POSTs outbound messages to `/api/send`; and (2) the whatsapp MCP server (`whatsapp-mcp-server/whatsapp.py`), which Claude Code tools use to read and send messages interactively. The MCP server uses a 30-second DB busy timeout and 10-second HTTP timeout. The intake engine uses a 30-second DB busy timeout. Both degrade gracefully: if the bridge is down, neither crashes, but inbound leads stop flowing and outbound sends will fail with rollback.

**Single-sender doctrine**: `com.crestbrick.wa-intake` is the only automated sender. Never start a second script that sends to prospects while the intake engine is loaded.

---

## 0. You should have been alerted already

The hourly watchdog (`com.crestbrick.wa-bridge-http-check`) Telegrams Winfred's personal chat on any healthy->down or down->healthy transition. If you received that alert, detection is done. Skip to section 2.

If investigating proactively, run section 1.

---

## 1. Detect the disconnect

Run 1a through 1d in order. Any single failure is enough to confirm the bridge is down.

### 1a. Watchdog state (fastest read)

```zsh
cat ~/.claude/state/wa-bridge-http-state.txt
```

`healthy` = last hourly probe passed. `DOWN(http=...)` or `DOWN(http=000)` = confirmed down. A `000` means the port is unreachable (process dead or not yet listening).

### 1b. Process alive?

```zsh
pgrep -fl whatsapp-bridge
```

No output = process is not running. With `KeepAlive true` and `ThrottleInterval 30`, launchd auto-restarts within 30 seconds. If the process is still gone after 60 seconds, go to section 2.

### 1c. REST API responding?

```zsh
curl -s -m 5 -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}' \
  || echo "CANNOT CONNECT"
```

- `HTTP 400` = bridge is up (correct rejection of empty payload)
- `HTTP 000` or `CANNOT CONNECT` = process is dead or stuck at startup

### 1d. Log signals

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

Failure indicators and where they send you:

```
Device logged out, please scan QR code       -> section 5b (QR re-pair required)
405 client-outdated                          -> section 5c (rebuild binary)
listen tcp 127.0.0.1:8080: address in use   -> section 5d (stale zombie process)
database is locked                           -> section 5e (SQLite WAL cleanup)
unable to open database file                 -> section 5e
stream:error 401 / 403                       -> section 6 (security event, do NOT restart)
stream:error (any other code)                -> section 2 (launchd restart)
Failed to establish stable connection        -> section 2 (launchd restart)
```

### 1e. launchd job status

```zsh
launchctl list | grep -E "whatsapp-bridge|wa-intake"
```

Format: `<PID>  <last-exit-code>  <label>`

- PID present, exit code 0 = running
- PID `-`, exit code non-zero = crashed, launchd is in 30-second backoff

---

## 2. Pre-restart checks (do not skip)

These protect the single-sender doctrine and prevent data loss.

### 2a. Is the intake engine mid-send?

The intake engine runs every 60 seconds and holds a flock lock for the duration of each tick. Check whether a tick is currently active:

```zsh
flock --nonblock ~/.claude/state/listing-templates/.wa-intake.lock echo "idle" 2>/dev/null \
  || echo "wa-intake tick is currently running — wait 10s and check again"
```

If the lock is held, wait for it to release before restarting the bridge. A bridge restart during an active send causes a `SEND_FAIL` in the runner, which it handles safely (latch rollback, next tick retries) — but waiting is cleaner.

### 2b. Is messages.db locked by another process?

```zsh
lsof /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/messages.db 2>/dev/null
```

If only `whatsapp-bridge` and/or `Python` (the intake engine) appear, that is normal. If a stale `whatsapp-bridge` zombie appears here while `pgrep` shows the process gone, clear it with the port cleanup in section 5d.

### 2c. Confirm the intake engine is NOT in quiet hours

```zsh
# SGT time right now
TZ=Asia/Singapore date +"%H:%M"
```

The intake engine holds its cursor during quiet hours (01:00 to 07:00 SGT) and does not send. A bridge restart during quiet hours has zero send-interruption risk. Outside quiet hours, confirm the lock in 2a.

### 2d. Note the current watermark (for post-restart verification)

```zsh
cat ~/.claude/state/listing-templates/runner-last.json
```

Record the `last_rowid` value. After restart you confirm the engine resumes from the same rowid, not from zero.

---

## 3. Standard restart (launchd managed)

This is the correct procedure for all normal disconnects. `KeepAlive true` means launchd owns the process lifecycle; always restart through launchctl rather than killing the binary directly.

```zsh
# Step 1: unload (kills the process cleanly)
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Step 2: confirm it stopped (should be immediate)
pgrep -fl whatsapp-bridge && echo "still up — wait 5s and re-check" || echo "stopped"

# Step 3: reload (RunAtLoad true means it starts immediately)
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Step 4: watch startup
tail -f /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

Expected log output within 3 to 8 seconds:

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
Connected to WhatsApp!
```

If you see `Scan this QR code` instead of `Connected to WhatsApp`, stop here and go to section 5b.

---

## 4. Post-restart verification

All three checks must pass before the bridge is considered restored.

### 4a. Process is alive

```zsh
pgrep -fl whatsapp-bridge
```

Expect a PID.

### 4b. REST API is responding

```zsh
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}'
```

Expect `HTTP 400` (bridge up, rejected bad payload).

### 4c. WhatsApp is connected (not just the HTTP listener)

```zsh
grep "Connected to WhatsApp" \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log | tail -1
```

The timestamp on this line must be after the restart time.

### 4d. End-to-end smoke test

Send a message to Winfred's own number:

```zsh
curl -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"+6581618149","message":"bridge restart ok - smoke test"}'
```

Expect `{"success":true}`. Confirm the message appears on the phone.

### 4e. MCP server reconnects automatically

The whatsapp MCP server (`whatsapp.py`) does not hold a persistent bridge connection; it makes fresh HTTP calls on each tool invocation. No manual action needed. After the smoke test in 4d passes, MCP tools will work on the next invocation.

### 4f. Intake engine resumes correctly

The intake engine's next tick (within 60 seconds) will open `messages.db` and continue from the watermark rowid noted in step 2d. Verify:

```zsh
# Wait 90 seconds, then check the runner log
tail -5 ~/.claude/state/listing-templates/dry-run-preview.log
```

The log must show rows being processed from roughly the same rowid, not from rowid 0 (which would indicate a watermark reset).

---

## 5. Specific failure modes

### 5a. Silent disconnect (process alive, messages not delivering)

**Symptom**: process is running, REST API returns HTTP 4xx, but no messages arrive or send. The whatsmeow WebSocket dropped without exiting the process. `KeepAlive` only fires on process exit, so launchd does not auto-recover this.

**Fix**: force a full restart via section 3. The watchdog catches this within one hour; if you know about it sooner, restart immediately.

### 5b. "Device logged out" — QR re-pair required

**Symptom**: log contains `Device logged out, please scan QR code to log in again`. Cause: linked device removed from phone, or phone offline for more than 14 days.

launchd cannot render a QR code. Run interactively.

```zsh
# Stop the launchd job
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Back up the session store before wiping
cp -r /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/ \
  /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store.backup-$(date +%Y%m%d)

# Wipe the stale session
rm -rf /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/

# Run interactively — QR prints to terminal
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
WA_PAIR_PHONE=6581618149 ./whatsapp-bridge
```

On the phone: WhatsApp > Settings > Linked Devices > Link a Device. Scan the QR code.

```zsh
# Once "Connected to WhatsApp" appears in terminal, press Ctrl-C
# Hand back to launchd
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Then run section 4 verification.

Note: when you wipe `store/`, all `@lid` subdirectories and `messages.db` are gone. The intake engine will set a fresh watermark on its next tick (it checks for the first-run condition). All past message history is lost from the local DB but the active intake state (`intake-state.json`) is unaffected.

### 5c. 405 client-outdated

**Symptom**: log shows `405 client-outdated`. WhatsApp rejected the whatsmeow protocol version.

**Fix**: rebuild the binary.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge

# Back up before touching anything
cp whatsapp-bridge whatsapp-bridge.backup-$(date +%Y%m%d)

go get -u go.mau.fi/whatsmeow@latest
go mod tidy
go build -o whatsapp-bridge .

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Known API change from the 2026-04-18 upgrade: `context.Background()` was added to `client.Download`, `sqlstore.New`, `container.GetFirstDevice`, `client.GetGroupInfo`, and `client.Store.Contacts.GetContact`. If the build fails, check `main.go` at those call sites.

If `405` returns immediately after a successful rebuild, escalate to the developer agent. There may be a breaking API change requiring `main.go` edits.

### 5d. Port 8080 already in use (crash-loop on startup)

**Symptom**: log shows `listen tcp 127.0.0.1:8080: bind: address already in use`. A zombie process held the port after a crash.

**Fix**:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
lsof -ti tcp:8080 | xargs kill -9
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5e. SQLite locked or corrupted

**Symptom**: log shows `database is locked`, `unable to open database file`, or `disk I/O error`.

**Fix** (transient WAL artifacts):

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# WAL and shared-memory files are safe to delete when the process is stopped
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-wal
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-shm

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

If it still fails after removing WAL files, `messages.db` may be corrupt. You can delete it without triggering a QR re-pair (the session is in `whatsapp.db` and `whatsmeow.db`, not `messages.db`). The intake engine will start a fresh watermark.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
rm /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/messages.db
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Do not delete `whatsapp.db`, `whatsmeow.db`, or the `@lid` directories. Deleting those wipes the session and forces a QR re-pair.

---

## 6. When to escalate rather than self-heal

Stop the service before escalating. Do not keep restart-looping.

| Condition | Action |
|---|---|
| `stream:error` with code `401` or `403` in logs | Account security event. Do not restart. Check the WhatsApp app on the phone for security alerts immediately. Escalate to Winfred directly. |
| QR re-pair succeeds but bridge drops again within minutes | Possible temporary WhatsApp ban or rate limit. Stop restarting. Wait 24 hours before next attempt. |
| `405 client-outdated` returns immediately after a whatsmeow rebuild | Breaking API change in whatsmeow requires `main.go` edits. Stop the service and escalate to the developer agent. |
| Linked device quota hit (WhatsApp allows max 4) | Open WhatsApp on phone, go to Linked Devices, remove a stale device, then re-pair (section 5b). |
| Bridge down more than 2 hours with no recovery | Inbound leads, intake automation, and Telegram alerts are all blocked. Winfred must handle urgent client messages manually by phone. File an ops incident note with timestamp, symptoms, and steps tried. |
| `messages.db` corrupt and `whatsapp.db` also unreadable | Do not delete both. Stop the service and escalate — losing the session DB forces a full re-pair and brief service outage. |

---

## 7. Prevention and ongoing hygiene

### 7a. launchd configuration (do not change these values)

The plist has `KeepAlive true` and `ThrottleInterval 30`. Do not reduce ThrottleInterval below 10. Do not remove KeepAlive. These are the production-tested values. The `WA_PAIR_PHONE=6581618149` environment variable in the plist identifies Winfred's number for the pairing process.

### 7b. Watchdog — hourly HTTP health check

`com.crestbrick.wa-bridge-http-check` runs every 3600 seconds. It probes `http://localhost:8080/api/send`, writes state to `~/.claude/state/wa-bridge-http-state.txt`, and Telegrams Winfred on any healthy->down or down->healthy transition. The watchdog detects silent disconnects (process alive but unresponsive) that launchd `KeepAlive` cannot catch. The watchdog does not auto-restart the bridge.

Check watchdog status:

```zsh
launchctl list | grep wa-bridge-http-check
cat ~/.claude/state/wa-bridge-http-state.txt
tail -30 /Users/winfredquek/.claude/bin/wa-bridge-http-check.out.log
```

Run it manually to force an immediate probe and alert test:

```zsh
/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh
```

Note: `RunAtLoad false` on the watchdog plist means it does not fire immediately on load, only after the first 3600-second interval. Manually running it after a restart confirms the new state is recorded.

### 7c. Session longevity

Keep the WhatsApp phone online and reachable at least every 10 days. A phone offline for more than 14 days causes the linked device session to expire, requiring a QR re-pair. Do not remove the `com.crestbrick.whatsapp-bridge` entry from the phone's Linked Devices list unless retiring the integration.

Back up the session store before any binary upgrade or major change:

```zsh
cp -r /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/ \
  /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store.backup-$(date +%Y%m%d)
```

### 7d. Log growth

The stdout log grows continuously. Trim manually if it exceeds 10 MB:

```zsh
tail -2000 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log \
  > /tmp/wa-bridge.log.tmp \
  && mv /tmp/wa-bridge.log.tmp \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

---

## Quick-reference decision tree

```
Got a Telegram alert OR messages have stopped flowing?
  |
  +-- Check state file: cat ~/.claude/state/wa-bridge-http-state.txt
  |     healthy -> silent disconnect (5a) or false alarm; continue checks
  |     DOWN    -> confirmed down; proceed
  |
  +-- Is pgrep -fl whatsapp-bridge empty?
  |     YES -> process is dead; run section 3 (launchd restart)
  |
  +-- Does curl return HTTP 4xx?
  |     YES -> process alive, no WA connection; run section 3 (force restart)
  |
  +-- What does the log say?
        "Device logged out"                  -> section 5b (QR re-pair)
        "405 client-outdated"                -> section 5c (rebuild binary)
        "address already in use"             -> section 5d (kill zombie)
        "database is locked"                 -> section 5e (WAL cleanup)
        "stream:error 401/403"               -> section 6 (DO NOT RESTART)
        "stream:error" (other codes)         -> section 3 (launchd restart)
        "Failed to establish stable connection" -> section 3 (launchd restart)
```
