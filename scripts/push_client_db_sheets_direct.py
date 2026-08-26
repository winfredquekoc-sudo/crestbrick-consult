#!/usr/bin/env python3
"""push_client_db_sheets_direct.py — NO-LLM replacement for the Client Database
Google Sheets push currently done by refresh-rental-dbs's SKILL.md Part D step 3
(a Task-tool subagent per tab that calls the google-workspace MCP).

STATUS (23 Aug 2026): PREPARED FOR REVIEW ONLY. Not wired into refresh-rental-dbs.sh
or any launchd job. Do not add this to a live run without Winfred's go.
STATUS (26 Aug 2026): Still not wired in / not live. The append-duplication bug this
script was written to fix has SINCE been fixed a different way (25 Aug 2026 runner
restructure: the in-skill Sheets-sync prompt now explicitly clears each range before
writing and is confirmed, by a live read on 26 Aug 2026, to exactly match the export
files with zero duplicate rows). What remains is a real but smaller win: replacing
that one remaining daily LLM call with this zero-token script. Auth is now WIRED for
one more source (the google-workspace MCP's own token, see addendum below) but still
NOT enabled by default -- same "needs Winfred's go" gate, now for a credential-choice
decision rather than a missing-file one.

Why this exists (see the audit follow-up write-up for the full root-cause dive):
  - export_client_db_tabs.py already builds the three tabs' data deterministically,
    zero LLM, zero network. The only step that used to need an LLM was pasting the
    rows into Google Sheets -- pure mechanical I/O with no judgment involved, and
    exactly the kind of thing an LLM subagent is the wrong tool for.
  - The current subagent path uses `appendSpreadsheetRows`, which ADDS to the bottom
    of each tab every night without ever clearing it first. The sheet has grown a
    fresh duplicate copy of every landlord/tenant on every successful run since Part D
    was added (4 Jul 2026) -- this, not a permissions or quota problem, is the main
    reason the Client DB sheet has read as "stale"/wrong since early July.
  - On top of that, at least one observed run had its subagent try to spawn a NESTED
    Agent to parallelize the remaining rows; that got rejected by the permission
    system and the subagent stopped there, leaving the Tenants tab (and the back half
    of Landlords Closed) unwritten for that night -- silently, no error surfaced.
  - Each subagent call also costs several hundred thousand to ~2M tokens for what is,
    underneath, a handful of API calls -- most of it is the google-workspace MCP's own
    ~110 tool schemas being re-read every turn of a long multi-step conversation.

What this script does instead: read the three already-built export files, and for
each tab CLEAR the full existing range then WRITE the fresh rows in ONE API call.
No LLM, no per-row chunking (the Sheets API takes the whole table in one request;
chunking was only ever needed because a model's own context was the bottleneck).
Idempotent: safe to re-run, always leaves the tab exactly matching the export file.

Auth (see "AUTH" section below): this environment already has google-auth /
google-auth-oauthlib / google-api-python-client importable from the SAME /usr/bin/python3
used everywhere else in this repo (confirmed 23 Aug 2026) -- nothing to install.
What is NOT yet wired up is a credential source: the live pipeline currently goes
through the `google-workspace` MCP server (an npx package), whose own OAuth token
cache was not something this read-only audit could safely locate/extract. Before
this script can run for real it needs ONE of:
  (a) a standard OAuth client (credentials.json from Google Cloud Console, installed-
      app flow) with its resulting token cached at GOOGLE_TOKEN_PATH, or
  (b) a service account JSON with edit access on the three sheet ids below, referenced
      via GOOGLE_SERVICE_ACCOUNT_PATH.
Everything downstream of get_sheets_service() is credential-source-agnostic and does
not need to change once either is in place.

26 Aug 2026 addendum: the google-workspace MCP's token store IS now located --
~/.google-mcp/tokens/<account>.json (account "winfred"), a standard Google
"authorized_user" JSON (type/client_id/client_secret/refresh_token), registered at
~/.google-mcp/accounts.json. get_sheets_service() below can load it as a THIRD,
opt-in fallback via GOOGLE_MCP_TOKEN_PATH -- wired but left OFF by default (see
DEFAULT_AUTH_MODE) for two concrete reasons, not just caution:
  1. The most recent operational record on this exact gap (project-nightly-db-refresh
     memory, modified 24 Aug 2026 -- one day before this addendum) documents the
     resolution as "needs Winfred to provision creds (OAuth client or service
     account); do not wire without his go" -- reusing the MCP's own token was not
     the recorded plan, so flipping DEFAULT_AUTH_MODE needs a fresh, explicit go, not
     an inference from an older task description.
  2. That token's OAuth app is still in Google Cloud "Testing" status, which force-
     expires refresh tokens roughly weekly (see reference_google_workspace_mcp memory
     -- re-authed 22 Jun, 16 Jul, 16 Aug 2026). Wiring an unattended daily job to it
     imports the same fragility this migration exists to remove. A dedicated service
     account (no expiry, and no shared blast radius with the interactive MCP session)
     is the more robust fix if/when Winfred wants this last LLM leg gone for good.
To opt in for a one-off manual test ONLY: GOOGLE_AUTH_MODE=mcp-token python3 ...
This mode is READ ONLY against the token file -- it never writes a refreshed access
token back to it, so it cannot corrupt the MCP's own store.

26 Aug 2026, Winfred's go: gcloud is not installed on this Mac (checked -- `which
gcloud` finds nothing), so the service-account route (more robust, no expiry) is not
headlessly achievable right now. Route (a) mcp-token is LIVE as of this addendum,
wired into refresh-rental-dbs.sh's 21:00 slot. Winfred accepts the known expiry
caveat. EXPECTED FAILURE MODE: this call will start failing with `invalid_grant`
whenever the google-workspace MCP's OAuth token next expires (Testing-mode app,
observed ~weekly to ~monthly) -- that is not a bug in this script, it is the accepted
tradeoff. It fails LOUD (non-zero exit, uncaught exception to stderr) rather than
silently. THE FIX when that happens: re-auth the MCP account (removeAccount then
addAccount for "winfred", see reference_google_workspace_mcp memory / SKILL.md) --
this script reads whatever is currently at GOOGLE_MCP_TOKEN_PATH on each run, so a
fresh MCP re-auth fixes this script too, no separate action needed. If Winfred later
wants this caveat gone entirely, revisit the service-account route once gcloud (or a
manually created service-account JSON) is available.

Manual dry run (safe, makes no network calls):
  /usr/bin/python3 push_client_db_sheets_direct.py --dry-run

Manual real run (once auth is wired):
  /usr/bin/python3 push_client_db_sheets_direct.py
"""
import argparse, json, os, sys

EXPORT_DIR = os.path.expanduser("~/.claude/state/client-db-export")
SPREADSHEET_ID = "1WdCMc0ktexARRVtHqMUoeYrPdS_HFYAk8pnPFobSeik"  # "Crestbrick Client Database"

# tab name -> export filename
TABS = {
    "Landlords Active": "landlords_active.json",
    "Landlords Closed": "landlords_closed.json",
    "Tenants": "tenants.json",
}

GOOGLE_TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", os.path.expanduser("~/.claude/secrets/google-sheets-token.json"))
GOOGLE_SERVICE_ACCOUNT_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH", os.path.expanduser("~/.claude/secrets/google-service-account.json"))
GOOGLE_CLIENT_SECRET_PATH = os.environ.get("GOOGLE_CLIENT_SECRET_PATH", os.path.expanduser("~/.claude/secrets/google-oauth-client.json"))

# google-workspace MCP's own token store (account "winfred"), located 26 Aug 2026 --
# see module docstring addendum for why this is opt-in, not the default.
GOOGLE_MCP_TOKEN_PATH = os.environ.get("GOOGLE_MCP_TOKEN_PATH", os.path.expanduser("~/.google-mcp/tokens/winfred.json"))
DEFAULT_AUTH_MODE = "service-account-or-oauth-client"  # do not change without Winfred's go
AUTH_MODE = os.environ.get("GOOGLE_AUTH_MODE", DEFAULT_AUTH_MODE)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def col_letter(n):
    """1-indexed column number -> spreadsheet column letter (1->A, 27->AA, ...)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def load_export(name):
    path = os.path.join(EXPORT_DIR, name)
    with open(path) as f:
        return json.load(f)


# ---- AUTH -------------------------------------------------------------------
def get_sheets_service():
    """Returns an authorized Sheets API v4 service object. See module docstring
    for what needs to exist on disk before this succeeds. Deliberately does NOT
    fall back to launching an interactive OAuth consent flow -- this runs headless."""
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if os.path.exists(GOOGLE_SERVICE_ACCOUNT_PATH):
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_PATH, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds)

    if os.path.exists(GOOGLE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            with open(GOOGLE_TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        return build("sheets", "v4", credentials=creds)

    # Opt-in fallback: reuse the google-workspace MCP's own token. NOT enabled by
    # DEFAULT_AUTH_MODE -- see module docstring addendum (26 Aug 2026) for why. Set
    # GOOGLE_AUTH_MODE=mcp-token explicitly to use it. Deliberately READ ONLY against
    # GOOGLE_MCP_TOKEN_PATH: refreshed access tokens are used in-memory only and never
    # written back, so this can never corrupt the MCP's own store (a shared write-back
    # would risk the MCP writing a shape google-workspace-mcp's own loader can't read).
    if AUTH_MODE == "mcp-token" and os.path.exists(GOOGLE_MCP_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(GOOGLE_MCP_TOKEN_PATH, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())  # in-memory only -- GOOGLE_MCP_TOKEN_PATH is never rewritten
        return build("sheets", "v4", credentials=creds)

    raise RuntimeError(
        "No Google credentials found. Set GOOGLE_SERVICE_ACCOUNT_PATH to a service "
        f"account JSON with edit access to spreadsheet {SPREADSHEET_ID}, or populate "
        f"{GOOGLE_TOKEN_PATH} via a one-time OAuth flow using a client secret at "
        f"{GOOGLE_CLIENT_SECRET_PATH}. A third option -- reusing the google-workspace "
        f"MCP's token at {GOOGLE_MCP_TOKEN_PATH} -- is wired but requires explicitly "
        "setting GOOGLE_AUTH_MODE=mcp-token; it is off by default, see module docstring "
        "addendum (26 Aug 2026) for why. Not wired up as the live default -- see module docstring.")


# ---- CORE ---------------------------------------------------------------
def push_tab(service, spreadsheet_id, tab, rows, dry_run):
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=1)
    last_col = col_letter(n_cols)
    full_range = f"'{tab}'!A1:{last_col}{max(n_rows, 1000)}"
    write_range = f"'{tab}'!A1"

    if dry_run:
        print(f"[DRY RUN] {tab}: would clear {full_range}, then write {n_rows} rows x {n_cols} cols starting at A1")
        return

    # Clear the whole plausible range first -- this is the actual bug fix: the old
    # subagent path only ever appended, so a shrinking tab (or any tab, forever)
    # accumulated duplicate rows. Clearing first makes every push idempotent.
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=full_range, body={}
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=write_range,
        valueInputOption="RAW", body={"values": rows}
    ).execute()
    print(f"{tab}: cleared + wrote {n_rows} rows x {n_cols} cols")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen; no network calls, no auth needed.")
    args = ap.parse_args()

    meta_path = os.path.join(EXPORT_DIR, "meta.json")
    if not os.path.exists(meta_path):
        print(f"ERROR: {meta_path} missing -- run export_client_db_tabs.py first.", file=sys.stderr)
        sys.exit(1)
    meta = json.load(open(meta_path))
    if meta.get("spreadsheet_id") != SPREADSHEET_ID:
        print(f"WARNING: meta.json spreadsheet_id {meta.get('spreadsheet_id')!r} != "
              f"{SPREADSHEET_ID!r} hardcoded here -- verify before trusting this run.", file=sys.stderr)

    service = None if args.dry_run else get_sheets_service()

    for tab, fname in TABS.items():
        rows = load_export(fname)
        push_tab(service, SPREADSHEET_ID, tab, rows, args.dry_run)

    print(f"done. spreadsheet: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    main()
