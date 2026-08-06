# Search Console auth that stops breaking every 7 days

**The problem this fixes.** GSC access has died repeatedly because the token was
hand pasted from a Google OAuth app left in **Testing** publishing status. Google
expires refresh tokens for Testing apps after **7 days**, so every re-auth bought
one week. The token stored on 31 Jul 2026 expired on 7 Aug 2026, exactly on schedule.

Switching the consent screen to **In production** removes that expiry. Combined
with the refresh script below, GSC then keeps working without you touching it.

## One time setup, about 10 minutes

1. **Google Cloud Console** (console.cloud.google.com), signed in as the Google
   account that owns winfredquek.com in Search Console. Create a project, or reuse one.
2. **APIs & Services → Library →** enable **Google Search Console API**.
3. **APIs & Services → OAuth consent screen**
   - User type: External
   - Add yourself as the only user if prompted
   - **Publishing status: In production** — this is the step that matters. Leaving it
     on Testing is what causes the weekly death.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Copy the client ID and client secret
5. Put them in `~/.claude/.gsc.env` (the file already exists, mode 600):

   ```
   GSC_CLIENT_ID=<the client id>
   GSC_CLIENT_SECRET=<the client secret>
   GSC_PROPERTY_URL=https://winfredquek.com/
   ```

6. Grant consent once:

   ```bash
   ~/.claude/bin/gsc-auth-bootstrap.sh
   ```

   It prints a Google URL and opens it. Pick the right account, approve read only
   access, done. The refresh token is written back to `~/.claude/.gsc.env`.

## After that

- `~/.claude/bin/gsc-auth-refresh.sh` mints a fresh access token on demand.
- `~/.claude/bin/gsc-rank-tracker.sh` calls it automatically before each run, so the
  daily rank tracking self heals instead of going silent.
- Claude can then pull real query data and run the search query to content backlog
  loop (which queries you already rank for, where you sit at positions 5 to 20 and a
  title or intro fix would win the click, and which questions get impressions with no
  page answering them).

## Notes

- Scope is `webmasters.readonly` — read only. Nothing can change your Search Console.
- Never commit `~/.claude/.gsc.env`. It lives outside the repo for that reason.
- If Google returns "no refresh token" during bootstrap, revoke prior access at
  myaccount.google.com/permissions and rerun (Google only issues a refresh token on
  first consent unless `prompt=consent` forces it, which the script does).
