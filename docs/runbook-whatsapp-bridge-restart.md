# Runbook: WhatsApp Bridge Restart

**Service label**: `com.crestbrick.whatsapp-bridge`
**Binary**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`
**Working dir**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/`
**Session store**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/`
**REST API**: `http://127.0.0.1:8080`
**Stdout log**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log`
**Stderr log**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log`
**Plist**: `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist`
**Watchdog plist**: `~/Library/LaunchAgents/com.crestbrick.wa-bridge-http-check.plist`
**Watchdog script**: `/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh`
**Last updated**: 2026-05-17

This runbook is for Winfred Quek's Crestbrick property advisory operation.
The bridge is whatsmeow-based. When it drops, inbound WhatsApp leads stop
reaching n8n and the Telegram alert pipeline goes dark.

---

## 0. You should have been alerted already

The hourly watchdog (`com.crestbrick.wa-bridge-http-check`) fires a Telegram
alert to Winfred's personal chat when the bridge transitions from healthy to
unhealthy. If you are reading this because you got that alert, the bridge is
confirmed down. Skip detection and go straight to section 2.

If you are investigating proactively, run section 1 first.

---

## 1. Detect the disconnect

Run checks 1a-1d in order. One failure is enough to confirm the bridge is down.

### 1a. Process alive?

```zsh
pgrep -fl whatsapp-bridge
```

No output means the process is not running. With `KeepAlive true`, launchd
should auto-restart within 30 seconds (ThrottleInterval). If the process is
still gone after 60 seconds, proceed to section 2.

### 1b. REST API responding?

```zsh
curl -sf http://127.0.0.1:8080/api/send \
  -X POST -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}' \
  -w "\nHTTP %{http_code}\n" || echo "CANNOT CONNECT — process down"
```

- `HTTP 400` = server is up (bad input, correct response)
- `curl: (7) Failed to connect` = process crashed or stuck at startup

### 1c. Log scan

```zsh
tail -60 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
tail -20 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log
```

Healthy indicators:

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
✓ Connected to WhatsApp!
```

Bad indicators (note which one — it determines the recovery path):

```
Device logged out, please scan QR code to log in again   → go to 5b
405 client-outdated                                       → go to 5c
listen tcp 127.0.0.1:8080: bind: address already in use  → go to 5d
database is locked                                        → go to 5e
stream:error                                              → whatsmeow WS error, restart (section 2)
```

### 1d. launchd job status

```zsh
launchctl list | grep whatsapp-bridge
```

Output: `<PID>  <exit-code>  com.crestbrick.whatsapp-bridge`

- PID present, exit code 0 = running
- PID `-`, exit code non-zero = crashed, launchd backing off

---

## 2. Standard restart (launchd managed — do this first)

The process is managed by launchd with `KeepAlive true`. This is the fastest path.

```zsh
# Unload (kills the process)
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Confirm it stopped
pgrep -fl whatsapp-bridge && echo "still up — wait 5s" || echo "stopped"

# Reload (RunAtLoad = true, starts immediately)
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Watch startup
tail -f /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

Expected output within 3-8 seconds:

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
✓ Connected to WhatsApp!
```

If you see `Scan this QR code` instead, the session was wiped. Skip to 5b.

---

## 3. Manual restart (if launchd is not managing the job)

Use this only if `launchctl list | grep whatsapp-bridge` returns nothing.

```zsh
# Kill any stale zombie
pkill -x whatsapp-bridge 2>/dev/null; sleep 2

# Start the binary directly in background
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
nohup ./whatsapp-bridge \
  >> /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log 2>&1 &

echo "PID $!"
```

Then re-register with launchd so future crashes auto-recover:

```zsh
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

---

## 4. Verify reconnection

All three checks must pass before the bridge is considered restored.

```zsh
# 1. Process is alive
pgrep -fl whatsapp-bridge

# 2. REST API is up (expect HTTP 400, not a connection error)
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  http://127.0.0.1:8080/api/send \
  -X POST -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}'

# 3. Log confirms WhatsApp connection (not just process running)
grep "Connected to WhatsApp" \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log | tail -1
```

End-to-end smoke test — send a message to yourself:

```zsh
# Replace +6591234567 with Winfred's own number
curl -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"+6591234567","message":"bridge restart test - ok"}'
```

Expected response: `{"success":true}`. The message should appear on the phone.

---

## 5. Specific failure modes

### 5a. Silent disconnect (process alive, messages not delivering)

**Symptom**: process is running and REST API responds, but no messages flow.
whatsmeow's internal `IsConnected()` is false. launchd `KeepAlive` only fires
on process exit — it cannot detect a stuck-but-alive process.

**Fix**: force a full restart via launchd (section 2).

### 5b. "Device logged out" — QR re-pair required

**Symptom**: log shows `Device logged out, please scan QR code to log in again`.
Cause: linked device removed from phone, or phone offline for more than 14 days.

**Fix**: run the binary interactively (launchd cannot render a QR code).

```zsh
# 1. Stop the job
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# 2. Wipe the stale session
rm -rf /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/

# 3. Run interactively — QR code prints to terminal
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
./whatsapp-bridge
```

On your phone: WhatsApp > Settings > Linked Devices > Link a Device. Scan QR.

```zsh
# 4. Once "Connected to WhatsApp" appears in terminal, press Ctrl-C
# 5. Hand back to launchd
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5c. 405 client-outdated

**Symptom**: log shows `405 client-outdated`. WhatsApp rejected the whatsmeow
protocol version.

**Fix**: rebuild against latest whatsmeow.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge

# Always back up the working binary first
cp whatsapp-bridge whatsapp-bridge.backup-$(date +%Y%m%d)

go get -u go.mau.fi/whatsmeow@latest
go mod tidy
go build -o whatsapp-bridge .

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Note from 2026-04-18 upgrade: API changes in whatsmeow required adding
`context.Background()` to `client.Download`, `sqlstore.New`,
`container.GetFirstDevice`, `client.GetGroupInfo`, and
`client.Store.Contacts.GetContact`. If the build fails with compile errors,
check `main.go` against those call sites.

### 5d. Port 8080 in use (crash-loop on startup)

**Symptom**: log shows `listen tcp 127.0.0.1:8080: bind: address already in use`.
A prior process did not exit cleanly.

**Fix**:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
lsof -ti tcp:8080 | xargs kill -9
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5e. SQLite locked or corrupted

**Symptom**: log shows `database is locked` or `unable to open database file`.

**Fix**:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Remove WAL / shared-memory lock artifacts (transient, safe to delete)
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-wal
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-shm

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

If it still fails, the messages database may be corrupt:

```zsh
ls /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/
```

Delete only `messages.db` (not the session database — deleting the session DB
forces a QR re-pair). Then reload.

---

## 6. Escalation path (restart not enough)

Stop the service before escalating. Do not keep restart-looping.

| Condition | Action |
|---|---|
| QR re-pair succeeds but bridge drops again within minutes | Possible WhatsApp temporary ban or rate limit. Stop restarting. Wait 24h before next attempt. |
| `405 client-outdated` returns immediately after whatsmeow rebuild | Breaking API change in whatsmeow upstream requires `main.go` edits. Stop service; escalate to developer agent. |
| `stream:error` with code `401` or `403` in logs | Account security event. Do not restart. Check WhatsApp app on phone for security alerts immediately. |
| Linked devices quota hit (max 4) | Open WhatsApp on phone > Linked Devices > remove a stale device, then re-pair. |
| Bridge down more than 2 hours with no recovery | Inbound leads, qualifier triggers, and drip sequences are blocked. Handle urgent client comms manually by phone. File an ops incident note with timestamp and root cause. |

---

## 7. Prevention

### 7a. Current launchd config (as of 2026-05-17)

The plist at `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist`
already has the recommended production settings:

- `KeepAlive true` — auto-restarts on any process exit
- `ThrottleInterval 30` — 30-second backoff between crash-restarts (prevents
  a bug from burning CPU in a tight loop)
- `RunAtLoad true` — starts on login/load, no manual trigger needed
- `WorkingDirectory` set — binary finds the `store/` directory via relative path

Do not reduce `ThrottleInterval` below 10. Do not remove `KeepAlive`.

### 7b. Watchdog — hourly HTTP health check (already deployed)

`com.crestbrick.wa-bridge-http-check` runs `/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh`
every 3600 seconds via `StartInterval`. It probes `http://localhost:8080/api/send`,
records state in `~/.claude/state/wa-bridge-http-state.txt`, and sends a
Telegram message on any healthy -> down or down -> healthy transition.

Check watchdog status:

```zsh
launchctl list | grep wa-bridge-http-check
```

Check watchdog logs:

```zsh
tail -30 /Users/winfredquek/.claude/bin/wa-bridge-http-check.out.log
tail -10 /Users/winfredquek/.claude/bin/wa-bridge-http-check.err.log
```

Run the watchdog manually (useful to force an alert test):

```zsh
/Users/winfredquek/.claude/bin/wa-bridge-http-check.sh
```

The watchdog does NOT auto-restart the bridge — it only alerts. The bridge
restarts itself via launchd `KeepAlive`. The two mechanisms are complementary:
launchd handles crash restarts; the watchdog catches silent disconnects where
the process is alive but unresponsive.

If the watchdog plist is not loaded:

```zsh
launchctl load ~/Library/LaunchAgents/com.crestbrick.wa-bridge-http-check.plist
```

Note: `RunAtLoad false` on the watchdog plist — it will not fire immediately on
load, only after the first 3600-second interval.

### 7c. Log rotation

The two log files grow unbounded. Add a monthly rotation via newsyslog:

```
/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log   640  4  1000  *  JN
/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log   640  4  1000  *  JN
```

Add those lines to `/etc/newsyslog.d/whatsapp-bridge.conf` (requires sudo).
Keeps 4 generations, max 1 MB each, sends SIGHUP on rotation (`N` flag).

Alternatively, manual trim when the file exceeds 10 MB:

```zsh
tail -1000 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log \
  > /tmp/wa.log && mv /tmp/wa.log \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

### 7d. Session longevity

- Keep the WhatsApp phone online and connected at least every 10 days to
  prevent the linked-device session from expiring.
- Do not remove the `com.crestbrick.whatsapp-bridge` entry from Linked Devices
  on the phone unless deliberately retiring the integration.
- Back up the `store/` directory before any binary upgrade:

```zsh
cp -r /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/ \
  /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store.backup-$(date +%Y%m%d)
```

---

## Quick-reference decision tree

```
Got Telegram alert OR bridge seems silent?
  |
  +-- Is pgrep -fl whatsapp-bridge empty?
  |     YES -> Section 2 (launchd restart)
  |
  +-- Does curl return HTTP 4xx?
  |     YES -> Bridge is up; silent disconnect. Section 2 (force restart)
  |
  +-- What does the log say?
        "Device logged out"              -> Section 5b (QR re-pair)
        "405 client-outdated"            -> Section 5c (rebuild)
        "address already in use"         -> Section 5d (kill stale port)
        "database is locked"             -> Section 5e (remove WAL files)
        "stream:error 401/403"           -> Section 6 (security — do not restart)
        "stream:error" (other)           -> Section 2 (launchd restart)
```
