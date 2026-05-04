# Runbook: WhatsApp Bridge Restart

**Service**: `com.crestbrick.whatsapp-bridge`
**Binary**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`
**Log (stdout)**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log`
**Log (stderr)**: `/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log`
**REST API**: `http://127.0.0.1:8080`
**Session store**: `/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/`
**Last updated**: 2026-05-03

---

## 1. Detecting a Disconnect

Run these checks in order. Any one failing confirms the bridge is down.

### 1a. Process check

```zsh
pgrep -fl whatsapp-bridge
```

No output = process is not running. launchd with `KeepAlive true` should have restarted it already; if it hasn't, check the ThrottleInterval (30s) and whether launchd itself has the job loaded.

### 1b. REST API health probe

```zsh
curl -sf http://127.0.0.1:8080/api/send -X POST \
  -H "Content-Type: application/json" \
  -d '{"recipient":"self","message":"ping"}' \
  && echo "UP" || echo "DOWN"
```

A 400 or 500 response still means the process is alive. `curl: (7) Failed to connect` means the HTTP listener is not up — process crashed or stuck in startup.

### 1c. Log signal scan

```zsh
tail -50 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
tail -50 /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log
```

Indicators the bridge is healthy:

```
Connected to WhatsApp
✓ Connected to WhatsApp!
Starting REST API server on 127.0.0.1:8080...
```

Indicators it is disconnected or in a bad state:

```
Device logged out, please scan QR code to log in again
Failed to connect: ...
Failed to establish stable connection
stream:error                    # whatsmeow WebSocket error
405 client-outdated             # library version rejected by WhatsApp
```

### 1d. launchd job status

```zsh
launchctl list | grep whatsapp-bridge
```

Output format: `<PID>  <last-exit-code>  com.crestbrick.whatsapp-bridge`

- PID present, exit code 0 = running normally.
- PID `-`, exit code non-zero = crashed and launchd is in backoff. Check the exit code against section 5.

---

## 2. Manual Restart

Use this when launchd auto-restart has not fired within 60 seconds, or you need to force a clean restart.

```zsh
# Step 1 — unload the job (stops the process)
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Step 2 — confirm the process is gone
pgrep -fl whatsapp-bridge && echo "still running — wait 5s" || echo "stopped"

# Step 3 — reload (launchd starts it immediately due to RunAtLoad=true)
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Step 4 — tail logs to watch startup
tail -f /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

Expected startup sequence in logs (takes 3-8 seconds):

```
Starting REST API server on 127.0.0.1:8080...
Connected to WhatsApp
✓ Connected to WhatsApp!
```

If you see `Scan this QR code` instead of `Connected to WhatsApp`, the session was wiped. Go to section 5 (QR re-pairing).

---

## 3. Automated Restart via launchd

The plist at `~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist` already handles auto-restart with `KeepAlive true` and a 30-second throttle. This is the production configuration.

Reference plist (current as of 2026-05-03):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.crestbrick.whatsapp-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/winfredquek</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>/Users/winfredquek/whatsapp-mcp/whatsapp-bridge</string>
</dict>
</plist>
```

To apply plist changes after editing:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

---

## 4. Verifying Successful Reconnection

Run all three checks. All three must pass.

```zsh
# 1. Process is alive
pgrep -fl whatsapp-bridge

# 2. REST listener is up
curl -s -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8080/api/send \
  -X POST -H "Content-Type: application/json" \
  -d '{"recipient":"","message":""}' \
  # expect 400 (bad request) — that means the server is responding

# 3. Log confirms WhatsApp connection (not just process running)
grep -m1 "Connected to WhatsApp" \
  /Users/winfredquek/.claude/bin/whatsapp-bridge.launchd.out.log
```

For end-to-end confirmation, send a test message to yourself via the REST API:

```zsh
# Replace +6591234567 with your own number
curl -X POST http://127.0.0.1:8080/api/send \
  -H "Content-Type: application/json" \
  -d '{"recipient":"+6591234567","message":"bridge restart test"}'
```

A `{"success":true}` response and receipt of the message on the device confirms full connectivity.

---

## 5. Common Failure Modes

### 5a. Silent disconnect (process running, WhatsApp not connected)

**Symptom**: process is alive, REST API responds, but no messages are delivered. `client.IsConnected()` internally returns false. The bridge has no auto-reconnect logic beyond a full process restart.

**Fix**: force a full restart (section 2). launchd `KeepAlive` only restarts on exit; a hung process requires manual unload/reload.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5b. "Device logged out" / QR re-pairing required

**Symptom**: log shows `Device logged out, please scan QR code to log in again`. This means WhatsApp revoked the session. Common cause: linked device was removed from the phone, or phone was inactive for >14 days.

**Fix**: run the binary interactively (launchd cannot display a QR code in a terminal).

```zsh
# Step 1 — stop the launchd job
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Step 2 — wipe the stale session
rm -rf /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/

# Step 3 — run interactively to display the QR code
cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
./whatsapp-bridge
# QR code appears in terminal — scan with WhatsApp on phone
# (Settings > Linked Devices > Link a Device)

# Step 4 — once "Connected to WhatsApp" appears, Ctrl-C and reload launchd
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5c. 405 client-outdated error

**Symptom**: log shows `405 client-outdated` or similar. WhatsApp rejected the whatsmeow protocol version.

**Fix**: rebuild the binary against the latest whatsmeow.

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

cd /Users/winfredquek/whatsapp-mcp/whatsapp-bridge
# Backup the working binary first
cp whatsapp-bridge whatsapp-bridge.backup-$(date +%Y%m%d)

go get -u go.mau.fi/whatsmeow@latest
go mod tidy
go build -o whatsapp-bridge .

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

Note from the 2026-04-18 upgrade: API changes in whatsmeow may require adding `context.Background()` to `client.Download`, `sqlstore.New`, `container.GetFirstDevice`, `client.GetGroupInfo`, and `client.Store.Contacts.GetContact`. Check `main.go` for compilation errors and fix accordingly before rebuilding.

### 5d. Port 8080 already in use (crash-loop)

**Symptom**: log shows `listen tcp 127.0.0.1:8080: bind: address already in use`. A prior process did not exit cleanly.

**Fix**:

```zsh
lsof -ti tcp:8080 | xargs kill -9
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

### 5e. SQLite database locked or corrupted

**Symptom**: log shows `database is locked` or `unable to open database file`.

**Fix**:

```zsh
launchctl unload ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist

# Check for lock files
ls /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/

# Remove WAL/lock artifacts (safe — they are transient)
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-wal
rm -f /Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/*.db-shm

launchctl load ~/Library/LaunchAgents/com.crestbrick.whatsapp-bridge.plist
```

If the database itself is corrupt (repeated failures after above), delete `store/messages.db` only. Do not delete the session database (`store/whatsapp.db` or similar) or you will trigger a QR re-pair.

---

## 6. When to Escalate

A restart is not enough in these situations. Stop the service and escalate before proceeding.

| Condition | Action |
|---|---|
| QR re-pair succeeds but bridge disconnects again within minutes | The WhatsApp account may be under a temporary ban or rate limit. Do not keep restarting. Wait 24h before the next attempt. |
| `405 client-outdated` returns immediately after a whatsmeow rebuild | whatsmeow upstream may have a breaking API change requiring code edits to `main.go`. Escalate to developer. |
| Session store is intact but `client.IsConnected()` never becomes true across 3+ restarts | Check whether the linked device quota on the phone is exhausted (WhatsApp allows 4 linked devices). Remove a stale device on the phone and retry. |
| Any log line containing `stream:error` with code `401` or `403` | Account security event. Do not restart. Check the WhatsApp app on the phone for security alerts. Escalate to Winfred directly. |
| Bridge has been down for more than 2 hours and automated recovery has failed | Client broadcasts, nurture drips, and qualifier triggers are all blocked. Escalate: Winfred to handle urgent client comms manually via phone until resolved. |
