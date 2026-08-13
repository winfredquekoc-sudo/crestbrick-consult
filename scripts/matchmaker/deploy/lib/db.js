commit 29f233e991cdba88ee40693b49df1110206346d1
Author: Winfred Quek <275661976+winfredquekoc-sudo@users.noreply.github.com>
Date:   Thu Aug 13 15:03:15 2026 +0800

    Give the Matchmaker a cloud CRM backend
    
    The app could already read everything and remember nothing: stages, notes and
    contacted stamps lived in localStorage, so a rebuild or a second device lost
    them. This adds the durable half.
    
    Facts and CRM state are kept strictly apart. Names, phones, budgets and last
    contact dates still come from the rental databases via build.py and stay
    read-only. Everything Winfred records now lives in Postgres behind /api/crm,
    so a nightly rebuild cannot wipe a note and the app cannot corrupt a database.
    
    - api/crm.js: transactional batch writes, snapshot-on-write so a second device
      picks up changes without polling
    - lib/db.js: driver-portable pg against DATABASE_URL (Neon/Supabase/Vercel PG)
    - client: offline-first queue persisted to localStorage, backoff retry, and a
      one-time import of the pre-backend localStorage keys so no existing stamp,
      flag or mark is lost
    - Pipeline tab, per-record drawer (stage, next action, notes, tasks, history)
    - With no DATABASE_URL the API answers 501 and the app stays local-only, which
      is a supported mode rather than a failure
    
    deploy.sh now rolls back if /api/crm ever answers anything but 401 — that
    endpoint can read every note and phone number in the CRM, so it must never sit
    outside the auth wall.
    
    deploy/.gitignore: deploy.sh copies the built PII artifact to deploy/index.html
    on every run and that path was committable. It is not any more.
    
    Verified against real Postgres (PGlite): 10/10 API contract tests, state
    recovered after wiping localStorage and rebuilding, offline writes self-healing
    with no user action, legacy migration preserving original dates, and all 8 tabs
    rendering with no console errors.
    
    Co-Authored-By: claude-flow <ruv@ruv.net>

diff --git a/scripts/matchmaker/deploy/.gitignore b/scripts/matchmaker/deploy/.gitignore
new file mode 100644
index 00000000..3eb78148
--- /dev/null
+++ b/scripts/matchmaker/deploy/.gitignore
@@ -0,0 +1,10 @@
+# deploy.sh copies the built app to index.html here on EVERY deploy, and that file is the
+# full PII artifact — every tenant and landlord name, phone number and note. It is a
+# build output that happens to land inside the repo, so it must never be committable.
+# This lives in the deploy folder rather than the root .gitignore so it cannot be left
+# behind if the folder is ever copied or moved.
+index.html
+
+# Vercel installs from package.json; a local `npm i` here must not reach a commit.
+node_modules/
+.vercel
diff --git a/scripts/matchmaker/deploy/api/crm.js b/scripts/matchmaker/deploy/api/crm.js
new file mode 100644
index 00000000..f076ff15
--- /dev/null
+++ b/scripts/matchmaker/deploy/api/crm.js
@@ -0,0 +1,239 @@
+// Matchmaker CRM API — the durable half of the app.
+//
+// The split this endpoint exists to preserve:
+//   FACTS  (who exists, their phone, budget, last message) come from the WhatsApp and
+//          CSV databases via build.py and are baked into index.html. They are read only
+//          here and are regenerated on every build.
+//   CRM    (stage, notes, tasks, flags, contacted stamps, match verdicts) lives in this
+//          database and is the ONLY thing the app writes.
+// Nothing in here ever writes back to the rental databases, so a rebuild can never be
+// overwritten by the app and the app can never be clobbered by a rebuild.
+//
+// Auth is the project's Basic Auth middleware, which matches /api/* like everything
+// else. There is no second auth check here on purpose: a request that reached this
+// function already passed the wall, and a second scheme would be another thing to
+// get wrong. Never loosen middleware.js's matcher to exclude /api.
+import { db, ensureSchema, configured } from "../lib/db.js";
+
+const STAGES = new Set([
+  "new", "contacted", "qualified", "viewing_set", "viewed",
+  "offer", "closed_won", "closed_lost", "dormant",
+]);
+const KINDS = new Set(["tenant", "landlord", "listing", "sale", "person"]);
+const MAX_OPS = 200;
+
+const str = (v, max) => {
+  if (v == null) return null;
+  const s = String(v).trim();
+  return s ? s.slice(0, max) : null;
+};
+// Dates arrive as YYYY-MM-DD from the client. Anything else becomes null rather than
+// reaching Postgres, where a malformed date aborts the whole transaction — and since the
+// batch is transactional, one bad date would discard every good write sent with it.
+// The shape check alone is not enough: "2026-13-99" matches the pattern and is still
+// rejected by Postgres, so the calendar itself has to agree the day exists.
+const date = (v) => {
+  const s = String(v || "");
+  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
+  const d = new Date(s + "T00:00:00Z");
+  return !isNaN(d) && d.toISOString().slice(0, 10) === s ? s : null;
+};
+const bool = (v) => v === true || v === "true" || v === 1;
+
+async function readBody(req) {
+  if (req.body && typeof req.body === "object") return req.body;
+  let raw = "";
+  for await (const chunk of req) {
+    raw += chunk;
+    if (raw.length > 1_000_000) throw new Error("payload too large");
+  }
+  return raw ? JSON.parse(raw) : {};
+}
+
+async function snapshot(client) {
+  const [entities, notes, tasks, match, activity] = await Promise.all([
+    client.query(`select key, kind, ref_id, name, phone, stage, next_action,
+                         to_char(next_due,'YYYY-MM-DD') as next_due, flagged,
+                         to_char(contacted_on,'YYYY-MM-DD') as contacted_on, archived
+                  from crm_entity`),
+    // `id desc` is a tiebreaker, not decoration: now() is the transaction timestamp, so
+    // two notes written in one batch share created_at exactly and would otherwise come
+    // back in whatever order the planner felt like — the drawer shows these newest first.
+    client.query(`select id, key, body, to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at
+                  from crm_note order by created_at desc, id desc limit 2000`),
+    client.query(`select id, key, title, to_char(due,'YYYY-MM-DD') as due, done,
+                         to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at
+                  from crm_task order by done asc, due asc nulls last, created_at desc, id desc limit 1000`),
+    client.query(`select listing_id, tenant_id, status from crm_match_status`),
+    client.query(`select id, key, verb, detail, to_char(at,'YYYY-MM-DD"T"HH24:MI:SSZ') as at
+                  from crm_activity order by at desc, id desc limit 300`),
+  ]);
+  return {
+    ok: true,
+    entities: entities.rows,
+    notes: notes.rows,
+    tasks: tasks.rows,
+    match: match.rows,
+    activity: activity.rows,
+  };
+}
+
+// Creates the entity row if it is not there yet, and only overwrites name/phone when the
+// incoming build actually has one — a rebuild that loses a contact's saved name must not
+// blank the name already recorded here.
+async function upsertEntity(client, o) {
+  const key = str(o.key, 120);
+  if (!key) return null;
+  const kind = KINDS.has(o.kind) ? o.kind : "person";
+  await client.query(
+    `insert into crm_entity (key, kind, ref_id, name, phone)
+     values ($1,$2,$3,$4,$5)
+     on conflict (key) do update set
+       ref_id = coalesce(excluded.ref_id, crm_entity.ref_id),
+       name   = coalesce(excluded.name,   crm_entity.name),
+       phone  = coalesce(excluded.phone,  crm_entity.phone)`,
+    [key, kind, str(o.ref_id, 80), str(o.name, 200), str(o.phone, 40)]
+  );
+  return key;
+}
+
+async function applyOp(client, o) {
+  switch (o.op) {
+    case "entity": {
+      const key = await upsertEntity(client, o);
+      if (!key) return 0;
+      const p = o.patch || {};
+      const sets = [], vals = [];
+      const put = (col, val) => { sets.push(`${col} = $${vals.push(val) + 1}`); };
+      if ("stage" in p && STAGES.has(p.stage)) put("stage", p.stage);
+      if ("next_action" in p) put("next_action", str(p.next_action, 400));
+      if ("next_due" in p) put("next_due", date(p.next_due));
+      if ("flagged" in p) put("flagged", bool(p.flagged));
+      if ("contacted_on" in p) put("contacted_on", date(p.contacted_on));
+      if ("archived" in p) put("archived", bool(p.archived));
+      if (!sets.length) return 0;
+      sets.push("updated_at = now()");
+      await client.query(`update crm_entity set ${sets.join(", ")} where key = $1`, [key, ...vals]);
+      const verb = "stage" in p ? "stage:" + p.stage
+                 : "contacted_on" in p ? (p.contacted_on ? "contacted" : "contacted:undo")
+                 : "flagged" in p ? (bool(p.flagged) ? "flagged" : "unflagged")
+                 : "updated";
+      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
+        [key, verb, str(o.name, 200)]);
+      return 1;
+    }
+    case "note": {
+      const key = await upsertEntity(client, o);
+      const body = str(o.body, 4000);
+      if (!key || !body) return 0;
+      await client.query(`insert into crm_note (key, body) values ($1,$2)`, [key, body]);
+      await client.query(`insert into crm_activity (key, verb, detail) values ($1,'note',$2)`,
+        [key, body.slice(0, 120)]);
+      return 1;
+    }
+    case "note_delete": {
+      const id = parseInt(o.id, 10);
+      if (!Number.isFinite(id)) return 0;
+      await client.query(`delete from crm_note where id = $1`, [id]);
+      return 1;
+    }
+    case "task": {
+      const key = o.key ? await upsertEntity(client, o) : null;
+      const id = parseInt(o.id, 10);
+      if (Number.isFinite(id)) {
+        await client.query(
+          `update crm_task set title = coalesce($2, title), due = $3, done = $4,
+                               done_at = case when $4 then now() else null end
+           where id = $1`,
+          [id, str(o.title, 300), date(o.due), bool(o.done)]
+        );
+        return 1;
+      }
+      const title = str(o.title, 300);
+      if (!title) return 0;
+      const r = await client.query(
+        `insert into crm_task (key, title, due) values ($1,$2,$3) returning id`,
+        [key, title, date(o.due)]
+      );
+      await client.query(`insert into crm_activity (key, verb, detail) values ($1,'task',$2)`,
+        [key, title.slice(0, 120)]);
+      return r.rowCount;
+    }
+    case "task_delete": {
+      const id = parseInt(o.id, 10);
+      if (!Number.isFinite(id)) return 0;
+      await client.query(`delete from crm_task where id = $1`, [id]);
+      return 1;
+    }
+    case "match": {
+      const lid = str(o.listing_id, 80), tid = str(o.tenant_id, 80);
+      if (!lid || !tid) return 0;
+      if (!str(o.status, 60)) {
+        await client.query(`delete from crm_match_status where listing_id = $1 and tenant_id = $2`, [lid, tid]);
+        return 1;
+      }
+      await client.query(
+        `insert into crm_match_status (listing_id, tenant_id, status) values ($1,$2,$3)
+         on conflict (listing_id, tenant_id) do update
+           set status = excluded.status, updated_at = now()`,
+        [lid, tid, str(o.status, 60)]
+      );
+      return 1;
+    }
+    default:
+      return 0;
+  }
+}
+
+export default async function handler(req, res) {
+  res.setHeader("Cache-Control", "no-store");
+  if (!configured) {
+    // 501 rather than 500: the client reads this as "run in local mode", not "retry".
+    return res.status(501).json({
+      ok: false,
+      configured: false,
+      error: "DATABASE_URL is not set on this Vercel project — CRM is running in local-only mode.",
+    });
+  }
+
+  let client;
+  try {
+    await ensureSchema();
+    client = await db().connect();
+
+    if (req.method === "GET") {
+      return res.status(200).json(await snapshot(client));
+    }
+
+    if (req.method === "POST") {
+      const body = await readBody(req);
+      const ops = Array.isArray(body.ops) ? body.ops : [];
+      if (ops.length > MAX_OPS) {
+        return res.status(413).json({ ok: false, error: `too many ops (max ${MAX_OPS})` });
+      }
+      let applied = 0;
+      // One transaction for the whole batch: the client sends a queue it has already
+      // applied optimistically, so a half applied batch would leave the two out of sync
+      // with no way for the client to tell which half landed.
+      await client.query("BEGIN");
+      try {
+        for (const o of ops) applied += await applyOp(client, o);
+        await client.query("COMMIT");
+      } catch (e) {
+        await client.query("ROLLBACK");
+        throw e;
+      }
+      // Return the fresh snapshot so a write doubles as a sync — this is what lets a
+      // second device pick up the first one's changes without a separate poll.
+      const snap = await snapshot(client);
+      return res.status(200).json({ ...snap, applied });
+    }
+
+    res.setHeader("Allow", "GET, POST");
+    return res.status(405).json({ ok: false, error: "method not allowed" });
+  } catch (e) {
+    return res.status(500).json({ ok: false, error: String(e && e.message || e) });
+  } finally {
+    if (client) client.release();
+  }
+}
diff --git a/scripts/matchmaker/deploy/api/health.js b/scripts/matchmaker/deploy/api/health.js
new file mode 100644
index 00000000..c80e795f
--- /dev/null
+++ b/scripts/matchmaker/deploy/api/health.js
@@ -0,0 +1,31 @@
+// Deploy check for the CRM backend. deploy.sh calls this after every deploy so that a
+// backend that cannot reach its database is reported at deploy time rather than
+// discovered later as writes that silently stayed on one device.
+//
+// Answers 200 in all three healthy states — the body says which one:
+//   configured:false          → no DATABASE_URL, app runs local-only (an intended state)
+//   configured:true, db:true  → backend live
+//   configured:true, db:false → DATABASE_URL set but unreachable, and `error` says why
+// Reports no connection string, host, or credential.
+import { db, ensureSchema, configured } from "../lib/db.js";
+
+export default async function handler(_req, res) {
+  res.setHeader("Cache-Control", "no-store");
+  if (!configured) {
+    return res.status(200).json({ ok: true, configured: false, db: false, mode: "local-only" });
+  }
+  try {
+    await ensureSchema();
+    const r = await db().query(
+      `select (select count(*) from crm_entity) as entities,
+              (select count(*) from crm_note)   as notes,
+              (select count(*) from crm_task where done = false) as open_tasks`
+    );
+    return res.status(200).json({ ok: true, configured: true, db: true, mode: "cloud", counts: r.rows[0] });
+  } catch (e) {
+    return res.status(200).json({
+      ok: false, configured: true, db: false, mode: "cloud-unreachable",
+      error: String((e && e.message) || e).slice(0, 300),
+    });
+  }
+}
diff --git a/scripts/matchmaker/deploy/deploy.sh b/scripts/matchmaker/deploy/deploy.sh
new file mode 100755
index 00000000..a10e6f5a
--- /dev/null
+++ b/scripts/matchmaker/deploy/deploy.sh
@@ -0,0 +1,149 @@
+#!/usr/bin/env bash
+# Manual only — nothing in build.py or any automated job calls this. Run by hand:
+#   python3 scripts/matchmaker/build.py && scripts/matchmaker/deploy/deploy.sh
+# Roll back only, no new deploy:
+#   scripts/matchmaker/deploy/deploy.sh --rollback
+#
+# INCIDENT (11 Aug 2026): this folder was once deployed to production without
+# deploy/middleware.js present, which shipped the PII app unauthenticated on
+# the production alias for ~5 minutes until manual rollback. Two structural
+# fixes below make that harder to repeat: (a) deploy/middleware.js presence is
+# now a hard precondition checked BEFORE every deploy, and (b) if the
+# production alias is ever caught answering 200 after a deploy, this script
+# rolls back and re-verifies automatically instead of just printing a warning.
+set -euo pipefail
+HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
+# Absolute, not relative: build.py always writes to ~/crestbrick-consult/_local
+# regardless of which worktree ran it, so deploy.sh must match that, not walk
+# up from wherever this copy of deploy.sh happens to sit (e.g. a worktree).
+SRC="$HOME/crestbrick-consult/_local/matchmaker.html"
+# The stable alias is what matters — Vercel platform SSO does NOT cover it,
+# only hash suffixed per deployment URLs get a 302 to vercel.com/sso-api.
+PROD_ALIAS="https://crestbrick-matchmaker-private.vercel.app"
+CURL="curl -s --max-time 20"
+
+rollback_and_verify() {
+  echo "deploy.sh: rolling back to the previous production deployment..." >&2
+  # Bare `vercel rollback --yes` does NOT roll back — verified live during the
+  # 11 Aug incident, it only reports "No deployment rollback in progress".
+  # The previous production deployment must be targeted explicitly.
+  local prev_url
+  prev_url="$( (cd "$HERE" && vercel ls --prod 2>&1) | grep -Eo 'https://[a-z0-9-]+\.vercel\.app' | sed -n '2p')"
+  if [ -z "$prev_url" ]; then
+    echo "deploy.sh: could not identify the previous production deployment — go to the Vercel dashboard now and roll back manually. Do not treat this app as private until the alias returns 401 again." >&2
+    exit 1
+  fi
+  (cd "$HERE" && vercel rollback "$prev_url" --yes) || {
+    echo "deploy.sh: vercel rollback FAILED — go to the Vercel dashboard now and roll back manually. Do not treat this app as private until the alias returns 401 again." >&2
+    exit 1
+  }
+  sleep 3
+  local status_after
+  status_after="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS")"
+  if [ "$status_after" != "401" ]; then
+    echo "deploy.sh: ROLLBACK DID NOT RESTORE AUTH — $PROD_ALIAS still returns $status_after. Go to the Vercel dashboard NOW." >&2
+    exit 1
+  fi
+  echo "deploy.sh: rollback verified — $PROD_ALIAS returns 401 again."
+}
+
+if [ "${1:-}" = "--rollback" ]; then
+  rollback_and_verify
+  exit 0
+fi
+
+# --- hard precondition: refuse to deploy this folder without its own auth
+# middleware present, regardless of what's already live on Vercel. This is
+# exactly the check that would have caught the 11 Aug incident. ---
+if [ ! -f "$HERE/middleware.js" ]; then
+  echo "deploy.sh: ABORTED — $HERE/middleware.js is missing. Refusing to deploy the PII app without auth middleware present." >&2
+  exit 1
+fi
+
+if [ ! -f "$SRC" ]; then
+  echo "deploy.sh: $SRC not found — run python3 scripts/matchmaker/build.py first" >&2
+  exit 1
+fi
+
+cp "$SRC" "$HERE/index.html"
+echo "deploying PII artifact to crestbrick-matchmaker-private — deploy/middleware.js confirmed present, MM_USER/MM_PASS must already be set on the Vercel project"
+
+cd "$HERE"
+OUT="$(vercel --prod --yes 2>&1 | tee /dev/stderr)"
+URL="$(printf '%s\n' "$OUT" | grep -Eo 'https://[A-Za-z0-9.-]+\.vercel\.app' | tail -1)"
+
+if [ -z "$URL" ]; then
+  echo "deploy.sh: could not parse a deployed URL from vercel output above — verify auth manually before sharing any link" >&2
+  exit 1
+fi
+
+# --- hash suffixed deployment URL: either the app's own middleware answers
+# 401 directly, or platform SSO intercepts first with a 302 to its sso-api.
+# Both mean auth is intact; anything else is a real failure. ---
+HASH_INFO="$($CURL -o /dev/null -w '%{http_code} %{redirect_url}' "$URL")"
+HASH_STATUS="${HASH_INFO%% *}"
+HASH_LOCATION="${HASH_INFO#* }"
+if [ "$HASH_STATUS" = "401" ]; then
+  echo "deploy.sh: deployment URL $URL returns 401 (app auth active)."
+elif [ "$HASH_STATUS" = "302" ] && printf '%s' "$HASH_LOCATION" | grep -q 'vercel.com/sso-api'; then
+  echo "deploy.sh: deployment URL $URL returns 302 to Vercel SSO ($HASH_LOCATION) — platform protection active."
+else
+  echo "deploy.sh: FAILED — deployment URL $URL returned HTTP $HASH_STATUS (location: ${HASH_LOCATION:-none}), expected 401 or a 302 to vercel.com/sso-api." >&2
+  echo "deploy.sh: AUTH MAY BE OFF on this deployment — check the Vercel dashboard immediately, this artifact has tenant/landlord PII." >&2
+  exit 1
+fi
+
+# --- production alias: this is the URL that actually matters, and the one
+# platform SSO does NOT cover. It must be 401, full stop. ---
+STATUS="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS")"
+if [ "$STATUS" = "200" ]; then
+  echo "############################################################" >&2
+  echo "# DEPLOY.SH: PRODUCTION ALIAS IS UNAUTHENTICATED (HTTP 200) #" >&2
+  echo "# $PROD_ALIAS is serving the PII app with NO AUTH WALL.     #" >&2
+  echo "# Rolling back immediately.                                 #" >&2
+  echo "############################################################" >&2
+  rollback_and_verify
+  exit 1
+fi
+if [ "$STATUS" != "401" ]; then
+  echo "deploy.sh: FAILED — $PROD_ALIAS returned HTTP $STATUS, expected 401 (auth wall). Not the plain 200 that triggers auto rollback, but not the expected 401 either — check the Vercel dashboard before sharing any link." >&2
+  if [ "$STATUS" = "503" ]; then
+    echo "deploy.sh: a 503 here is middleware.js reporting that MM_USER/MM_PASS are not set on the Vercel project. Set both, then redeploy — do NOT treat this app as private until the alias returns 401." >&2
+  fi
+  exit 1
+fi
+
+echo "verified: $PROD_ALIAS returns 401 (auth wall active) — safe to share only with Winfred's own MM_USER/MM_PASS"
+
+# --- the CRM API must sit behind the SAME wall as the app. /api/crm can read and write
+# every note, stage and phone number in the CRM, so an /api route that answered without
+# auth would hand all of it out even while "/" still looked locked. This check is the
+# reason middleware.js's matcher must never be narrowed to exclude /api. ---
+API_STATUS="$($CURL -o /dev/null -w '%{http_code}' "$PROD_ALIAS/api/crm")"
+if [ "$API_STATUS" != "401" ]; then
+  echo "############################################################" >&2
+  echo "# DEPLOY.SH: THE CRM API IS NOT BEHIND THE AUTH WALL        #" >&2
+  echo "# $PROD_ALIAS/api/crm returned $API_STATUS, expected 401.   #" >&2
+  echo "# It can read and write every CRM note and phone number.    #" >&2
+  echo "# Rolling back immediately.                                 #" >&2
+  echo "############################################################" >&2
+  rollback_and_verify
+  exit 1
+fi
+echo "verified: $PROD_ALIAS/api/crm returns 401 (CRM API is behind the same wall)"
+
+# --- backend reachability. Needs credentials, so it only runs when MM_USER/MM_PASS are
+# exported locally; without them the deploy is still complete and the wall is still
+# proven, we just cannot see past it from here. Never fails the deploy: a healthy app
+# with no database is the supported local-only mode, not a broken deploy. ---
+if [ -n "${MM_USER:-}" ] && [ -n "${MM_PASS:-}" ]; then
+  HEALTH="$($CURL -u "$MM_USER:$MM_PASS" "$PROD_ALIAS/api/health")"
+  case "$HEALTH" in
+    *'"mode":"cloud"'*)             echo "verified: CRM backend is live — $HEALTH" ;;
+    *'"mode":"local-only"'*)        echo "note: DATABASE_URL is not set on the Vercel project, so the app runs local-only — CRM writes stay on each device and do not sync. Set DATABASE_URL to switch it on." ;;
+    *'"mode":"cloud-unreachable"'*) echo "WARNING: DATABASE_URL is set but the database did not answer. Every CRM write will queue on the device until it does. Response: $HEALTH" >&2 ;;
+    *)                              echo "WARNING: unexpected /api/health response: $HEALTH" >&2 ;;
+  esac
+else
+  echo "note: export MM_USER and MM_PASS before running this to also check the CRM backend's database connection."
+fi
diff --git a/scripts/matchmaker/deploy/icon.svg b/scripts/matchmaker/deploy/icon.svg
new file mode 100644
index 00000000..c359d5f7
--- /dev/null
+++ b/scripts/matchmaker/deploy/icon.svg
@@ -0,0 +1,4 @@
+<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="Matchmaker">
+  <rect width="100" height="100" rx="20" fill="#0b1220"/>
+  <polyline points="26,74 26,26 50,54 74,26 74,74" fill="none" stroke="#c9a961" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
+</svg>
diff --git a/scripts/matchmaker/deploy/lib/db.js b/scripts/matchmaker/deploy/lib/db.js
new file mode 100644
index 00000000..603bde52
--- /dev/null
+++ b/scripts/matchmaker/deploy/lib/db.js
@@ -0,0 +1,100 @@
+// Postgres access for the Matchmaker CRM layer.
+//
+// Deliberately driver-portable: plain `pg` against DATABASE_URL, so the same code
+// runs on Neon, Supabase, or Vercel Postgres. Whichever gets provisioned, use the
+// POOLED/pgbouncer connection string — a serverless function opens a connection per
+// cold start and a direct (unpooled) endpoint runs out of slots under any real use.
+//
+// When DATABASE_URL is unset the API answers 501 rather than throwing. That is not a
+// degenerate case: it is the app's supported local mode, where the client keeps every
+// CRM write in localStorage exactly as it did before the backend existed.
+import pg from "pg";
+
+const URL_ = process.env.DATABASE_URL || "";
+export const configured = !!URL_;
+
+let pool = null;
+export function db() {
+  if (!configured) throw new Error("DATABASE_URL is not set");
+  if (!pool) {
+    pool = new pg.Pool({
+      connectionString: URL_,
+      // Managed Postgres providers all terminate TLS with their own chain. Verifying it
+      // from a lambda needs the provider CA bundled, which breaks the moment the provider
+      // changes — and the connection string itself is the secret here.
+      ssl: { rejectUnauthorized: false },
+      max: 1,                       // one socket per lambda instance, not per request
+      idleTimeoutMillis: 10_000,
+      connectionTimeoutMillis: 8_000,
+    });
+    pool.on("error", () => {});     // a dropped idle socket must not kill the process
+  }
+  return pool;
+}
+
+const SCHEMA = `
+create table if not exists crm_entity (
+  key           text primary key,
+  kind          text not null,
+  ref_id        text,
+  name          text,
+  phone         text,
+  stage         text not null default 'new',
+  next_action   text,
+  next_due      date,
+  flagged       boolean not null default false,
+  contacted_on  date,
+  archived      boolean not null default false,
+  updated_at    timestamptz not null default now()
+);
+create index if not exists crm_entity_phone_idx on crm_entity(phone);
+create index if not exists crm_entity_due_idx   on crm_entity(next_due) where next_due is not null;
+
+create table if not exists crm_note (
+  id         bigserial primary key,
+  key        text not null references crm_entity(key) on delete cascade,
+  body       text not null,
+  created_at timestamptz not null default now()
+);
+create index if not exists crm_note_key_idx on crm_note(key, created_at desc);
+
+create table if not exists crm_task (
+  id         bigserial primary key,
+  key        text references crm_entity(key) on delete set null,
+  title      text not null,
+  due        date,
+  done       boolean not null default false,
+  done_at    timestamptz,
+  created_at timestamptz not null default now()
+);
+create index if not exists crm_task_open_idx on crm_task(due) where done = false;
+
+create table if not exists crm_match_status (
+  listing_id text not null,
+  tenant_id  text not null,
+  status     text not null,
+  updated_at timestamptz not null default now(),
+  primary key (listing_id, tenant_id)
+);
+
+create table if not exists crm_activity (
+  id     bigserial primary key,
+  key    text,
+  verb   text not null,
+  detail text,
+  at     timestamptz not null default now()
+);
+create index if not exists crm_activity_at_idx  on crm_activity(at desc);
+create index if not exists crm_activity_key_idx on crm_activity(key, at desc);
+`;
+
+// Idempotent, and run at most once per lambda instance rather than per request.
+// The promise is cached (not a boolean) so concurrent first requests await the same
+// migration instead of racing three CREATE TABLE statements against each other.
+let ready = null;
+export function ensureSchema() {
+  if (!ready) {
+    ready = db().query(SCHEMA).catch((e) => { ready = null; throw e; });
+  }
+  return ready;
+}
diff --git a/scripts/matchmaker/deploy/manifest.json b/scripts/matchmaker/deploy/manifest.json
new file mode 100644
index 00000000..31f7e118
--- /dev/null
+++ b/scripts/matchmaker/deploy/manifest.json
@@ -0,0 +1,11 @@
+{
+  "name": "Matchmaker",
+  "short_name": "Matchmaker",
+  "start_url": "/",
+  "display": "standalone",
+  "background_color": "#0b1220",
+  "theme_color": "#0b1220",
+  "icons": [
+    { "src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any" }
+  ]
+}
diff --git a/scripts/matchmaker/deploy/middleware.js b/scripts/matchmaker/deploy/middleware.js
new file mode 100644
index 00000000..4062f68b
--- /dev/null
+++ b/scripts/matchmaker/deploy/middleware.js
@@ -0,0 +1,32 @@
+// Basic auth wall for the PII matchmaker app. Same logic as the canonical
+// ~/crestbrick-matchmaker-private/middleware.js — this copy exists so that a
+// deploy run from THIS folder can never ship without it (see deploy.sh's hard
+// precondition). MM_USER/MM_PASS are read from the Vercel project's own env
+// vars at request time; no credentials of any kind live in this file.
+//
+// favicon.ico/manifest.json/sw.js/icon.svg are excluded from the auth wall:
+// they carry no PII and must be reachable before a Basic Auth prompt (a PWA
+// install banner and the service worker's own fetch of sw.js both happen
+// pre auth in most browsers).
+export const config = { matcher: ['/((?!favicon.ico|manifest.json|sw.js|icon.svg).*)'] };
+export default function middleware(req) {
+  const user = process.env.MM_USER, pass = process.env.MM_PASS;
+  // Fail closed when the project's env vars are missing. Without this, template
+  // interpolation of two undefined values makes the expected header the fixed,
+  // publicly derivable Basic btoa('undefined:undefined') — the wall would still
+  // answer 401 to a plain visitor (so deploy.sh's 401 check would pass and
+  // report the app as private) while anyone sending that one known header got
+  // straight in to real tenant and landlord data.
+  if (!user || !pass) {
+    return new Response('Auth is not configured on this deployment — set MM_USER and MM_PASS on the Vercel project.', {
+      status: 503, headers: { 'Cache-Control': 'no-store' }
+    });
+  }
+  const auth = req.headers.get('authorization') || '';
+  const expected = 'Basic ' + btoa(`${user}:${pass}`);
+  if (auth === expected) return;
+  return new Response('Authentication required', {
+    status: 401,
+    headers: { 'WWW-Authenticate': 'Basic realm="Crestbrick Matchmaker"' }
+  });
+}
diff --git a/scripts/matchmaker/deploy/package.json b/scripts/matchmaker/deploy/package.json
new file mode 100644
index 00000000..a497f443
--- /dev/null
+++ b/scripts/matchmaker/deploy/package.json
@@ -0,0 +1,11 @@
+{
+  "name": "crestbrick-matchmaker-private",
+  "private": true,
+  "version": "1.0.0",
+  "description": "Matchmaker PII app: static shell + CRM API. No build step on purpose — Vercel serves index.html from the root and turns api/*.js into functions. Adding a build script here would change that.",
+  "type": "module",
+  "engines": { "node": ">=20" },
+  "dependencies": {
+    "pg": "^8.13.1"
+  }
+}
diff --git a/scripts/matchmaker/deploy/robots.txt b/scripts/matchmaker/deploy/robots.txt
new file mode 100644
index 00000000..1f53798b
--- /dev/null
+++ b/scripts/matchmaker/deploy/robots.txt
@@ -0,0 +1,2 @@
+User-agent: *
+Disallow: /
diff --git a/scripts/matchmaker/deploy/sw.js b/scripts/matchmaker/deploy/sw.js
new file mode 100644
index 00000000..41226d45
--- /dev/null
+++ b/scripts/matchmaker/deploy/sw.js
@@ -0,0 +1,70 @@
+// Matchmaker service worker: cache first for "/", network fallback.
+// This app is a single file artifact (index.html at "/") behind Basic Auth —
+// nothing else on this origin needs caching, so the fetch handler ignores
+// every other path.
+//
+// CACHE_VERSION: 2 — bump this number (only this number, and the CACHE_NAME
+// string below to match) whenever this file's caching logic changes, so
+// returning visitors evict the old cache instead of running stale logic
+// forever. build.py never touches this file, so the version is hand rolled.
+//
+// PRIVACY NOTE: the cached response IS the PII artifact. deploy/vercel.json
+// sets Cache-Control: no-store on "/", but the Cache Storage API deliberately
+// ignores response cache headers — putting "/" in a cache persists tenant and
+// landlord data to that device's disk regardless of what no-store says. That
+// is inherent to installing this as an offline app (item 52) rather than a bug
+// in either file, and the compensating controls are the auth wall in front of
+// it plus the app's own masking and idle lock. Anyone reading vercel.json and
+// concluding "this response is never written to disk" would be wrong.
+const CACHE_NAME = "matchmaker-cache-v2";
+const APP_URL = "/";
+
+self.addEventListener("install", (event) => {
+  self.skipWaiting();
+  event.waitUntil(
+    caches.open(CACHE_NAME).then((cache) => cache.add(APP_URL).catch(() => {}))
+  );
+});
+
+self.addEventListener("activate", (event) => {
+  event.waitUntil(
+    caches.keys()
+      .then((names) => Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))))
+      .then(() => self.clients.claim())
+  );
+});
+
+self.addEventListener("fetch", (event) => {
+  const req = event.request;
+  if (req.method !== "GET") return;
+  const url = new URL(req.url);
+  if (url.pathname !== APP_URL) return;   // only the app shell is cached
+
+  // Cache first, but ALWAYS revalidate in the background (stale while
+  // revalidate). Plain cache first would pin the very first artifact this
+  // device ever loaded: deploy.sh ships a rebuilt index.html to the same "/",
+  // and since sw.js itself is unchanged by a data-only deploy, nothing would
+  // ever re-run install() to refresh the entry — an installed PWA would keep
+  // serving that first day's tenant data forever, with clearing site data as
+  // the only way out. Refreshing behind the response costs the user nothing
+  // (they still get the cached copy instantly, and offline still works) and
+  // the next launch picks up the newer build.
+  //
+  // `res.ok` is what keeps the auth wall from poisoning the cache: an expired
+  // Basic Auth session answers 401, and a 401 must never overwrite a good
+  // cached artifact.
+  event.respondWith(
+    caches.open(CACHE_NAME).then((cache) =>
+      cache.match(req).then((cached) => {
+        const fresh = fetch(req).then((res) => {
+          if (res && res.ok) cache.put(req, res.clone());
+          return res;
+        }).catch((e) => {
+          if (cached) return cached;   // offline with a cached copy is a success
+          throw e;
+        });
+        return cached || fresh;
+      })
+    )
+  );
+});
diff --git a/scripts/matchmaker/deploy/vercel.json b/scripts/matchmaker/deploy/vercel.json
new file mode 100644
index 00000000..9a357942
--- /dev/null
+++ b/scripts/matchmaker/deploy/vercel.json
@@ -0,0 +1,16 @@
+{
+  "headers": [
+    {
+      "source": "/",
+      "headers": [
+        { "key": "Cache-Control", "value": "no-store" },
+        { "key": "X-Content-Type-Options", "value": "nosniff" },
+        { "key": "Referrer-Policy", "value": "no-referrer" },
+        { "key": "X-Robots-Tag", "value": "noindex, nofollow, noarchive" },
+        { "key": "Strict-Transport-Security", "value": "max-age=63072000; includeSubDomains; preload" },
+        { "key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()" },
+        { "key": "Content-Security-Policy", "value": "frame-ancestors 'none'" }
+      ]
+    }
+  ]
+}
diff --git a/scripts/matchmaker/export_data.py b/scripts/matchmaker/export_data.py
index 10ee1d63..5bc60a44 100644
--- a/scripts/matchmaker/export_data.py
+++ b/scripts/matchmaker/export_data.py
@@ -2,15 +2,137 @@
 """Export ONE compact JSON for the interactive matchmaker app: available listings +
 still-looking tenants, with structured gates so the app can score matches for ALL
 available listings (not only the 5 the engine covers). No blanks lost; PII stays local."""
-import json, os, re
+import datetime, importlib.util, json, os, re, sqlite3
 
 ROOT = os.path.expanduser("~/crestbrick-consult")
+
+# ---- CEA register check for co-broke counterparties only ----------------------------
+# Winfred, 13 Aug 2026: verify the agent by CONTACT NUMBER (CEA's own anti scam advice),
+# and only for co-broke sources — an ordinary landlord is not a salesperson. Every lookup
+# is cached in ~/.claude/state/cea-phone-cache.json so a number is hit once, not per build.
+_CEA_MOD = None
+def _cea():
+    global _CEA_MOD
+    if _CEA_MOD is None:
+        p = os.path.expanduser("~/.claude/bin/cea-phone-check.py")
+        spec = importlib.util.spec_from_file_location("cea_phone_check", p)
+        _CEA_MOD = importlib.util.module_from_spec(spec)
+        spec.loader.exec_module(_CEA_MOD)
+    return _CEA_MOD
+
+_COBROKE_AGENTS = None
+def _cobroke_agent_phone(name):
+    """Phone for a co-broke agent by name, from ~/.claude/state/cobroke-agents.json."""
+    global _COBROKE_AGENTS
+    if _COBROKE_AGENTS is None:
+        try:
+            with open(os.path.expanduser("~/.claude/state/cobroke-agents.json")) as f:
+                _COBROKE_AGENTS = json.load(f).get("agents", [])
+        except Exception:
+            _COBROKE_AGENTS = []
+    n = (name or "").strip().lower()
+    if not n:
+        return ""
+    for a in _COBROKE_AGENTS:
+        an = (a.get("name") or "").strip().lower()
+        if an and (an == n or an.startswith(n) or n.startswith(an.split()[0])):
+            return re.sub(r"\D", "", a.get("jid") or a.get("phone") or "")
+    return ""
+
+def cea_check(l, source):
+    """Verify the CO-BROKE AGENT on a co-broke listing — never the landlord.
+
+    contact_label_source reads like "co-broke listing (Denise), added 28 Jul 2026": the
+    name in brackets is the counterpart agent, while the record's own phone belongs to the
+    OWNER. Checking the record phone flagged two ordinary landlords as unregistered agents
+    (Winfred, 13 Aug 2026). Never raises: an offline build degrades to 'unknown'.
+    """
+    if source != "co-broke":
+        return None
+    label = l.get("contact_label_source") or ""
+    m = re.search(r"co-?broke[^()]*\(([^)]+)\)", label, re.I)
+    agent = (m.group(1).strip() if m else "")
+    if not agent:
+        return {"status": "agent_unknown"}
+    phone = _cobroke_agent_phone(agent)
+    if not phone:
+        return {"status": "agent_unknown", "agent": agent}
+    try:
+        matches, _ = _cea().lookup(phone)
+    except Exception:
+        return {"status": "unknown", "agent": agent}
+    if not matches:
+        return {"status": "not_registered", "agent": agent}
+    r = matches[0]
+    return {"status": "active" if r.get("active") else "expired", "agent": agent,
+            "name": r.get("name", ""), "reg_no": r.get("reg_no", ""),
+            "agency": r.get("agency", ""), "valid_until": r.get("valid_until", ""),
+            "disciplinary": bool(r.get("disciplinary"))}
+
+def src_of(l):
+    return "co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"
 OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchmaker-data.json")
 land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
 ten  = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
 adem = json.load(open(os.path.join(ROOT, "_templates/area-demand.json")))
 DIST_AREA = {d["district"]: d["area"] for d in adem["districts"]}
 
+# ---- WhatsApp "story so far" -- last inbound message snippets per tenant, for the
+# matchmaker background card. Read only, one batched scan of messages.db (not one query
+# per tenant). lid/pn resolution follows the same pattern as scripts/revival_scan.py.
+MSG_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/messages.db")
+WA_DB = os.path.expanduser("~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db")
+
+def norm_phone_digits(raw):
+    d = re.sub(r"\D", "", str(raw or ""))
+    if len(d) == 8 and d[0] in "89":
+        d = "65" + d
+    return d
+
+def load_wa_story(tenants):
+    """Returns {tenant_id: [{"date": "YYYY-MM-DD", "text": snippet}, ...]} for the last
+    up to 3 inbound messages per tenant phone. Never touches outbound messages or writes
+    anything. Missing DBs or any read error -> empty dict (feature degrades silently)."""
+    phone_to_id = {}
+    for t in tenants:
+        d = norm_phone_digits(t.get("phone"))
+        if d:
+            phone_to_id[d] = t.get("id")
+    story = {}
+    if not phone_to_id or not os.path.exists(MSG_DB) or not os.path.exists(WA_DB):
+        return story
+    try:
+        wc = sqlite3.connect("file:" + WA_DB + "?mode=ro", uri=True, timeout=20)
+        lid2pn = dict(wc.execute("SELECT lid, pn FROM whatsmeow_lid_map"))
+        wc.close()
+        mc = sqlite3.connect("file:" + MSG_DB + "?mode=ro", uri=True, timeout=20)
+        rows = mc.execute(
+            "SELECT chat_jid, content, timestamp FROM messages "
+            "WHERE is_from_me=0 AND content != '' "
+            "AND chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@newsletter' "
+            "AND chat_jid NOT LIKE '120363%' AND chat_jid NOT LIKE '%@broadcast' "
+            "ORDER BY timestamp ASC").fetchall()
+        mc.close()
+    except Exception:
+        return story
+    buckets = {}
+    for jid, content, ts in rows:
+        base = (jid or "").split("@")[0]
+        pn = lid2pn.get(base, base) if (jid or "").endswith("@lid") else base
+        tid = phone_to_id.get(pn) or phone_to_id.get(base)
+        if not tid:
+            continue
+        buckets.setdefault(tid, []).append((ts, content))
+    for tid, msgs in buckets.items():
+        for ts, content in msgs[-3:]:
+            story.setdefault(tid, []).append({
+                "date": str(ts)[:10] if ts else "",
+                "text": " ".join(str(content).split())[:100],
+            })
+    return story
+
+WA_STORY = load_wa_story(ten["tenants"])
+
 def availability(l):
     # A closed status outranks offer_pending: the flag is set when an offer comes in
     # and is not always cleared once the unit closes, so checking it first
@@ -66,6 +188,38 @@ def maps_query(addr, district):
     q = addr or DIST_AREA.get(district, district or "")
     return (str(q).strip() + " Singapore") if q else ""
 
+# district inference from free-text location — ~46% of tenants have no structured
+# district/preferred_districts at all, only a preferred_location string ("Bayshore /
+# East", "Jalan Batu (Katong)"). Without this the roster sort degenerates to "no
+# district" for nearly half the list, which is what looked like everyone lumped
+# together. Match against each district's known area-name keywords as a best-effort.
+AREA_KEYWORDS = []  # [(district, keyword), ...] longest keyword first so "bukit batok" beats "batok"
+for _d, _area in DIST_AREA.items():
+    for _kw in [k.strip().lower() for k in _area.split(",") if k.strip()]:
+        AREA_KEYWORDS.append((_d, _kw))
+AREA_KEYWORDS.sort(key=lambda x: -len(x[1]))
+
+def infer_district(explicit_district, preferred_districts, preferred_location):
+    if explicit_district: return explicit_district
+    if preferred_districts: return preferred_districts[0]
+    loc = (preferred_location or "").lower()
+    if not loc: return ""
+    for d, kw in AREA_KEYWORDS:
+        if kw in loc: return d
+    return ""
+
+def commission_est(rent_min, rent_max):
+    # Rough proxy only (1 month's rent, the common SG co-broke convention) — actual
+    # terms vary per listing (0.5mth/1yr, 1mth, 1% non-exclusive etc. are all seen in
+    # follow_up notes) and aren't captured as a structured field. Label as an estimate.
+    r = rent_max or rent_min
+    return r or None
+
+HANDED_OFF_MARKERS = ["handed to", "co-broke leads", "ziing", "stepped back"]
+def handed_off(l):
+    txt = ((l.get("follow_up") or "") + " " + (l.get("status") or "")).lower()
+    return any(m in txt for m in HANDED_OFF_MARKERS)
+
 listings = []
 for l in land["landlords"]:
     av = availability(l)
@@ -80,8 +234,10 @@ for l in land["landlords"]:
         "viewing": l.get("viewing_availability") or "",
         "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
         "listing_key": l.get("listing_key") or "", "phone": l.get("phone") or "",
-        "follow_up": l.get("follow_up") or "",
-        "source": ("co-broke" if "co-broke" in (l.get("contact_label_source") or "").lower() else "own"),
+        "follow_up": l.get("follow_up") or "", "last_contact": l.get("last_contact") or "",
+        "source": src_of(l),
+        "cea": cea_check(l, src_of(l)),
+        "commission_est": commission_est(num(l.get("rent_min")), num(l.get("rent_max"))),
         "gates": {
             "max_pax": num(r.get("max_pax")),
             "lease_min": num(r.get("lease_min")) or num(r.get("lease_term")),
@@ -93,6 +249,81 @@ for l in land["landlords"]:
         "req_raw": {k: v for k, v in r.items() if v},
     })
 
+STATUS_ORDER = {"Available":0, "Offer pending":1, "Pending":2, "Taken":3, "Off market":4}
+all_landlords = []
+for l in land["landlords"]:
+    av = availability(l)
+    pd = infer_district(l.get("district") or "", [], l.get("full_address") or "")
+    all_landlords.append({
+        "id": l.get("id"), "name": l.get("landlord_name"), "availability": av,
+        "status_raw": l.get("status") or "", "sort": STATUS_ORDER.get(av, 9),
+        "district": l.get("district") or "", "primary_district": pd, "address": l.get("full_address") or "",
+        "map_query": maps_query(l.get("full_address"), l.get("district")),
+        "rent_min": (lambda a,b:(min(a,b) if a and b else a))(num(l.get("rent_min")),num(l.get("rent_max"))),
+        "rent_max": (lambda a,b:(max(a,b) if a and b else b))(num(l.get("rent_min")),num(l.get("rent_max"))),
+        "viewing": l.get("viewing_availability") or "",
+        "rooms": l.get("rooms_and_rent") or "", "property_type": l.get("property_type") or "",
+        "phone": l.get("phone") or "", "last_contact": l.get("last_contact") or "",
+        "follow_up": l.get("follow_up") or "",
+        "source": src_of(l),
+        "cea": cea_check(l, src_of(l)),
+        "commission_est": commission_est(num(l.get("rent_min")), num(l.get("rent_max"))),
+        "handed_off": handed_off(l),
+    })
+all_landlords.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))
+
+# ---- sale listings (separate track from rentals — deal_type: sale / sale-or-rent) ----
+# rent_min/rent_max are NOT usable for these: they hold mis-parsed artifacts from an
+# upstream extraction bug (e.g. LL009 rent_min/max = 490/490 when the actual asking
+# price is $505,000, per its own rooms_and_rent text). Parse the asking price from the
+# free-text field instead; keep the raw text too since parsing SG price shorthand
+# ("$505k", "505,000") from freeform notes is inherently best-effort.
+def parse_price(txt):
+    if not txt: return None
+    nums = []
+    for m in re.finditer(r"\$?\s?([\d,]{3,})\s*k\b", txt, re.I):
+        nums.append(int(m.group(1).replace(",", "")) * 1000)
+    for m in re.finditer(r"\$\s?([\d,]{5,})(?!\s*k)", txt):
+        nums.append(int(m.group(1).replace(",", "")))
+    return max(nums) if nums else None
+
+SALE_STATUS_ORDER = {"Available":0, "Pending":1, "Closed":2}
+def sale_status(status_raw):
+    st = (status_raw or "").lower()
+    if st.startswith("closed") or st.startswith("archived"): return "Closed"
+    if st in ("sale-active", "active"): return "Available"
+    return "Pending"
+
+sales = []
+for l in land["landlords"]:
+    if (l.get("deal_type") or "") not in ("sale", "sale-or-rent"): continue
+    txt = l.get("rooms_and_rent") or ""
+    ss = sale_status(l.get("status"))
+    pd = infer_district(l.get("district") or "", [], l.get("full_address") or "")
+    sales.append({
+        "id": l.get("id"), "name": l.get("landlord_name"), "sale_status": ss,
+        "sort": SALE_STATUS_ORDER.get(ss, 9), "status_raw": l.get("status") or "",
+        "district": l.get("district") or "", "primary_district": pd,
+        "address": l.get("full_address") or "", "map_query": maps_query(l.get("full_address"), l.get("district")),
+        "asking_price": parse_price(txt), "price_text": txt,
+        "property_type": l.get("property_type") or "", "phone": l.get("phone") or "",
+        "last_contact": l.get("last_contact") or "", "follow_up": l.get("follow_up") or "",
+        "source": src_of(l),
+        "cea": cea_check(l, src_of(l)),
+    })
+sales.sort(key=lambda r: (r["sort"], r["primary_district"] or "zzz", r["name"] or ""))
+
+# duplicate phone numbers — same number saved as more than one landlord/tenant record,
+# almost always a data-entry collision (dup contact, or a landlord who is also a tenant
+# elsewhere) worth a human glance rather than silently treated as two separate people.
+_phone_owners = {}
+for l in all_landlords:
+    if l["phone"]: _phone_owners.setdefault(l["phone"], []).append("LL:" + (l["name"] or l["id"]))
+for t in ten["tenants"]:
+    p = t.get("phone") or ""
+    if p: _phone_owners.setdefault(p, []).append("TN:" + (t.get("name") or t.get("id")))
+duplicate_phones = [{"phone": p, "owners": owners} for p, owners in _phone_owners.items() if len(owners) > 1]
+
 tenants = []
 for t in ten["tenants"]:
     if looking(t) != "Still looking": continue
@@ -109,19 +340,79 @@ for t in ten["tenants"]:
         "occupation": t.get("occupation") or "", "move_in": t.get("move_in_date") or "",
         "lease_months": num(t.get("lease_term_months")), "phone": t.get("phone") or "",
         "last_contact": t.get("last_contact") or "", "listing_enquired": t.get("listing_enquired") or "",
+        "wa_story": WA_STORY.get(t.get("id"), []),
     })
 
+TENANT_STATUS_ORDER = {"Still looking":0, "Found":1, "Not looking":2}
+all_tenants = []
+for t in ten["tenants"]:
+    lk = looking(t)
+    budget = num(t.get("budget")) or num(t.get("budget_max"))
+    raw_pd = ([str(x).strip() for x in t["preferred_districts"] if str(x).strip()]
+              if isinstance(t.get("preferred_districts"), list)
+              else [d.strip() for d in (t.get("preferred_districts") or "").split(",") if d.strip()])
+    pd = infer_district(t.get("district") or "", raw_pd, t.get("preferred_location") or "")
+    missing = []
+    if not budget: missing.append("budget")
+    if not t.get("move_in_date"): missing.append("move-in")
+    if not num(t.get("no_of_pax")): missing.append("pax")
+    if not num(t.get("lease_term_months")): missing.append("lease")
+    if not pd: missing.append("district")
+    all_tenants.append({
+        "id": t.get("id"), "name": t.get("name"), "looking": lk,
+        "status_raw": t.get("status") or "", "sort": TENANT_STATUS_ORDER.get(lk, 9),
+        "preferred_location": t.get("preferred_location") or "", "district": t.get("district") or "",
+        "primary_district": pd,
+        "budget": budget, "pax": num(t.get("no_of_pax")), "gender": t.get("gender") or "",
+        "nationality": t.get("nationality") or "", "occupation": t.get("occupation") or "",
+        "move_in": t.get("move_in_date") or "", "lease_months": num(t.get("lease_term_months")),
+        "phone": t.get("phone") or "", "last_contact": t.get("last_contact") or "",
+        "listing_enquired": t.get("listing_enquired") or "", "missing": missing,
+    })
+# district/location is now the PRIMARY grouping (Winfred: tenants were "all lumped
+# together" under status alone) — status and data-completeness are secondary within
+# each area group. Unmatched location falls into a final "zzz" bucket the UI labels
+# "Unspecified location".
+all_tenants.sort(key=lambda r: (r["primary_district"] or "zzz", r["sort"], len(r["missing"]) == 0, r["name"] or ""))
+
+# ---- revival board (reuse revival_board.py's own scan, do not re-implement it) ----
+# revival_board.py normally runs standalone and does
+# `from export_data import availability, infer_district, looking, num` -- that only
+# resolves when a module literally named "export_data" is already in sys.modules.
+# When this file itself runs as __main__ (exactly how build.py invokes it) no such
+# name exists yet, so alias this already-executing module in under that name before
+# importing revival_board -- avoids a second, wasteful re-execution of this whole file.
+import sys as _sys
+_sys.modules.setdefault("export_data", _sys.modules[__name__])
+import revival_board as _revival_board
+_avail_for_revival = [{
+    "id": l.get("id"), "name": l.get("landlord_name"), "district": l.get("district") or "",
+    "rent_min": num(l.get("rent_min")), "rent_max": num(l.get("rent_max")),
+} for l in land["landlords"] if availability(l) == "Available"]
+_revival_rows = _revival_board.build_rows(ten["tenants"], _avail_for_revival, datetime.date.today())
+revival = [{
+    "name": r["name"], "phone": r["phone"], "days_quiet": r["dq"], "budget": r["budget"],
+    "district": r["district"], "pax": r["pax"], "tier": r["tier"],
+    "match": ({"id": r["match"]["id"], "name": r["match"]["name"], "district": r["match"]["district"],
+               "rent_min": r["match"]["rent_min"], "rent_max": r["match"]["rent_max"]}
+              if r["match"] else None),
+} for r in _revival_rows]
+
 data = {
     "generated": "2026-07-28",
     "priority": ["availability","location","price","landlord requirements"],
-    "counts": {"available_listings": len(listings), "still_looking_tenants": len(tenants)},
+    "counts": {"available_listings": len(listings), "still_looking_tenants": len(tenants),
+               "all_landlords": len(all_landlords), "all_tenants": len(all_tenants), "sales": len(sales)},
     "districts": DIST_AREA,
     "area_demand": [{"district":d["district"],"area":d["area"],"unmatched_waiting":d.get("unmatched_waiting"),
                      "supply_gap":d.get("supply_gap"),"sourcing_priority":d.get("sourcing_priority")}
                     for d in adem["districts"]],
-    "listings": listings, "tenants": tenants,
+    "listings": listings, "tenants": tenants, "all_landlords": all_landlords,
+    "all_tenants": all_tenants, "duplicate_phones": duplicate_phones, "sales": sales,
+    "revival": revival,
 }
 json.dump(data, open(OUT, "w"), ensure_ascii=False)
 print("wrote", OUT)
 print("listings:", len(listings), "| tenants:", len(tenants))
+print("revival candidates:", len(revival))
 print("sample listing gates:", json.dumps(listings[0]["gates"], ensure_ascii=False))
diff --git a/scripts/matchmaker/revival_board.py b/scripts/matchmaker/revival_board.py
new file mode 100644
index 00000000..a6003e7b
--- /dev/null
+++ b/scripts/matchmaker/revival_board.py
@@ -0,0 +1,155 @@
+#!/usr/bin/env python3
+"""Rerunnable matchmaker lead-revival board. Read only against the matchmaker
+tenant/landlord DBs (distinct from revival_scan.py, which mines raw WhatsApp
+history). Surfaces "still looking" tenants past the lead-cutoff window alongside
+the best-fit currently available listing, for Winfred's manual review before any
+outreach. Writes exactly one file, never sends anything, never drafts messages."""
+import datetime
+import json
+import os
+import sys
+
+ROOT = os.path.expanduser("~/crestbrick-consult")
+sys.path.insert(0, os.path.join(ROOT, "scripts", "matchmaker"))
+from export_data import availability, infer_district, looking, num  # noqa: E402
+
+LEAD_CUTOFF_DAYS = 30  # feedback_lead_cutoff.md — leads quieter than this are not revival candidates
+BOARD = os.path.expanduser(
+    "~/Desktop/Real Estate Related/Winfred Brain/Deals/Matchmaker Revival board.md")
+
+
+def load():
+    ten = json.load(open(os.path.join(ROOT, "_templates/tenant-db.json")))
+    land = json.load(open(os.path.join(ROOT, "_templates/landlord-db.json")))
+    return ten["tenants"], land["landlords"]
+
+
+def days_since(datestr, today):
+    if not datestr:
+        return None
+    try:
+        y, m, d = [int(x) for x in datestr[:10].split("-")]
+        return (today - datetime.date(y, m, d)).days
+    except (ValueError, TypeError):
+        return None
+
+
+def best_match(t, avail):
+    # Only 9 listings are live citywide -- "some listing passes the budget floor" is not
+    # the same as "actually a fit", so this returns a quality TIER, not just a pick.
+    b = num(t.get("budget")) or num(t.get("budget_max"))
+    pd = t.get("preferred_districts") or []
+    td = t.get("district") or ""
+    best, best_score = None, -1
+    for l in avail:
+        if b is not None and l["rent_min"] and b < l["rent_min"] * 0.9:
+            continue  # tenant's budget can't reach this landlord's minimum ask, not a fit
+        district_ok = (pd and l["district"] in pd) or (td and td == l["district"])
+        budget_ok = b is None or not (l["rent_max"] and b > l["rent_max"] * 1.8)
+        score = (3 if district_ok else 0) + (2 if budget_ok else 0)
+        if score > best_score:
+            best_score, best = score, l
+    if best is None:
+        return None, "none"
+    if best_score >= 5:
+        return best, "good"
+    if best_score >= 2:
+        return best, "weak"
+    return None, "none"
+
+
+def fmt_phone(pn):
+    pn = (pn or "").strip()
+    if pn.startswith("65") and len(pn) == 10:
+        return "+65 " + pn[2:6] + " " + pn[6:10]
+    return pn or "no phone"
+
+
+def fmt_money(v):
+    return "$" + format(v, ",") if v else "?"
+
+
+def build_rows(tenants, avail, today):
+    rows = []
+    for t in tenants:
+        if looking(t) != "Still looking":
+            continue
+        dq = days_since(t.get("last_contact"), today)
+        if dq is not None and dq <= LEAD_CUTOFF_DAYS:
+            continue  # legitimately active per the lead cutoff, not a revival candidate
+        b = num(t.get("budget")) or num(t.get("budget_max"))
+        pd_raw = t.get("preferred_districts") or []
+        district = (t.get("district") or (pd_raw[0] if pd_raw else "")
+                    or infer_district("", pd_raw, t.get("preferred_location")))
+        match, tier = best_match(t, avail)
+        rows.append({
+            "name": t.get("name") or "no name", "phone": t.get("phone") or "",
+            "dq": dq, "budget": b, "district": district or "?",
+            "pax": t.get("no_of_pax") or "?", "match": match, "tier": tier,
+        })
+    # good matches first, then weak, then none; within each group by days quiet ascending;
+    # no-date entries sort last since we cannot judge how stale they really are
+    tier_rank = {"good": 0, "weak": 1, "none": 2}
+    rows.sort(key=lambda r: (tier_rank[r["tier"]], r["dq"] is None, r["dq"] if r["dq"] is not None else 0))
+    return rows
+
+
+def render(rows, avail_count, active_count, today):
+    lines = [
+        "---", f"generated: {today.strftime('%d %b %Y')} SGT",
+        f"lead_cutoff_days: {LEAD_CUTOFF_DAYS}", "---", "",
+        "# Matchmaker Lead Revival Board", "",
+        f"Still-looking tenants past the {LEAD_CUTOFF_DAYS} day lead cutoff, cross-checked against "
+        "currently available listings. This is a REVIEW LIST ONLY -- no messages have "
+        "been drafted or sent. 'Good match' means the district and budget both line up; "
+        "'weak match' means only budget clears (wrong or unknown area) -- worth a second "
+        "look, not a send list; 'no match' means nothing currently live fits their budget.", "",
+        f"Active leads within {LEAD_CUTOFF_DAYS} days: {active_count} (excluded, not shown here) | "
+        f"Past cutoff: {len(rows)} | Good match: {sum(1 for r in rows if r['tier']=='good')} | "
+        f"Weak match: {sum(1 for r in rows if r['tier']=='weak')} | "
+        f"No match: {sum(1 for r in rows if r['tier']=='none')} | "
+        f"Available listings scanned: {avail_count}", "",
+        "| Name | Phone | Days quiet | Budget | District wanted | Pax | Match | Best current option |",
+        "|---|---|---|---|---|---|---|---|",
+    ]
+    tier_label = {"good": "✅ good", "weak": "❓ weak", "none": "— none"}
+    for r in rows:
+        dq = f"{r['dq']}d" if r["dq"] is not None else "no date"
+        if r["match"]:
+            m = r["match"]
+            mtxt = f"{m['name'] or m['id']} · {m['district']} · {fmt_money(m['rent_min'] or m['rent_max'])}"
+        else:
+            mtxt = "none right now"
+        lines.append(f"| {r['name']} | {fmt_phone(r['phone'])} | {dq} | {fmt_money(r['budget'])} "
+                      f"| {r['district']} | {r['pax']} | {tier_label[r['tier']]} | {mtxt} |")
+    lines.append("")
+    return "\n".join(lines)
+
+
+def main():
+    tenants, landlords = load()
+    today = datetime.date.today()
+    avail = [{
+        "id": l.get("id"), "name": l.get("landlord_name"), "district": l.get("district") or "",
+        "rent_min": num(l.get("rent_min")), "rent_max": num(l.get("rent_max")),
+    } for l in landlords if availability(l) == "Available"]
+    active_count = sum(1 for t in tenants
+                        if looking(t) == "Still looking"
+                        and (days_since(t.get("last_contact"), today) or 0) <= LEAD_CUTOFF_DAYS)
+    rows = build_rows(tenants, avail, today)
+    out = render(rows, len(avail), active_count, today)
+    os.makedirs(os.path.dirname(BOARD), exist_ok=True)
+    tmp = BOARD + ".tmp"
+    with open(tmp, "w", encoding="utf-8") as f:
+        f.write(out)
+    os.replace(tmp, BOARD)
+    print(f"wrote {BOARD}", file=sys.stderr)
+    print(f"active(<={LEAD_CUTOFF_DAYS}d)={active_count} past_cutoff={len(rows)} "
+          f"good={sum(1 for r in rows if r['tier']=='good')} "
+          f"weak={sum(1 for r in rows if r['tier']=='weak')} "
+          f"none={sum(1 for r in rows if r['tier']=='none')} avail_listings={len(avail)}",
+          file=sys.stderr)
+
+
+if __name__ == "__main__":
+    main()
diff --git a/scripts/matchmaker/template.html b/scripts/matchmaker/template.html
index f67c05ea..58940c02 100644
--- a/scripts/matchmaker/template.html
+++ b/scripts/matchmaker/template.html
@@ -11,17 +11,48 @@
   --acc:#5b8cff; --grn:#2ecc71; --amb:#f5a623; --red:#ff5c6c; --chip:#223052;
 }
 @media (prefers-color-scheme:light){
-  :root{--bg:#f4f6fb;--panel:#ffffff;--panel2:#f0f3fa;--line:#dde3ef;--ink:#16203a;--mut:#5a6a88;--acc:#2f6bff;--chip:#eaf0ff;}
+  :root:not([data-theme="dark"]){--bg:#faf7f0;--panel:#ffffff;--panel2:#f3efe3;--line:#e3dcc8;--ink:#141c33;--mut:#4b5578;--acc:#2f5fdb;--chip:#eef1fb;}
 }
+:root[data-theme="light"]{--bg:#faf7f0;--panel:#ffffff;--panel2:#f3efe3;--line:#e3dcc8;--ink:#141c33;--mut:#4b5578;--acc:#2f5fdb;--chip:#eef1fb;}
+:root[data-theme="dark"]{--bg:#0f1420;--panel:#161d2e;--panel2:#1d2740;--line:#2a3550;--ink:#e8edf7;--mut:#93a1c0;--acc:#5b8cff;--chip:#223052;}
+/* ---- CRM drawer + pipeline ---- */
+.drawer-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:60;display:none}
+.drawer-bg.on{display:block}
+.drawer{position:fixed;top:0;right:0;bottom:0;width:min(460px,100%);background:var(--panel);border-left:1px solid var(--line);z-index:61;transform:translateX(100%);transition:transform .18s ease;display:flex;flex-direction:column}
+.drawer.on{transform:none}
+.drawer h3{margin:0;font-size:15px}
+.dhead{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:8px}
+.dbody{padding:12px 14px;overflow:auto;flex:1;display:flex;flex-direction:column;gap:14px}
+.dsec{display:flex;flex-direction:column;gap:7px}
+.dsec>.lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);font-weight:700}
+.dclose{margin-left:auto;background:none;border:0;color:var(--mut);font-size:22px;cursor:pointer;line-height:1;padding:0 4px}
+.stagerow{display:flex;gap:5px;flex-wrap:wrap}
+.stagepill{padding:5px 10px;border-radius:20px;border:1px solid var(--line);background:var(--panel2);cursor:pointer;font-size:12px;color:var(--ink)}
+.stagepill.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
+.note{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 10px;font-size:13px;white-space:pre-wrap;position:relative}
+.note .when{display:block;color:var(--mut);font-size:11px;margin-bottom:3px}
+.note .del{position:absolute;top:5px;right:7px;color:var(--mut);cursor:pointer;font-size:14px;line-height:1}
+.dsec textarea{background:var(--panel2);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:8px 10px;font:inherit;resize:vertical;min-height:62px;outline:none;width:100%}
+.tk{display:flex;align-items:flex-start;gap:8px;font-size:13px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:7px 9px}
+.tk.done span.t{text-decoration:line-through;color:var(--mut)}
+.tk .del{margin-left:auto;color:var(--mut);cursor:pointer}
+.tk .due{color:var(--mut);font-size:11px;display:block}
+.tk .due.over{color:var(--red);font-weight:700}
+.pipe{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;align-items:start}
+.pcol{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:9px 10px;display:flex;flex-direction:column;gap:7px}
+.pcol h4{margin:0 0 2px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut)}
+.pcard{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:7px 9px;font-size:12.5px;cursor:pointer}
+.pcard b{display:block;font-size:13px}
 *{box-sizing:border-box}
 body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
 header{position:sticky;top:0;z-index:20;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 16px}
 .hrow{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.2px}
 .sub{color:var(--mut);font-size:12px}
-.kpis{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap}
+.kpis{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap;align-items:center}
 .kpi{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:4px 10px;font-size:12px}
 .kpi b{font-size:15px}
+.themebtn{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:5px 9px;font-size:14px;cursor:pointer;color:var(--ink);line-height:1}
 .tabs{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
 .tab{padding:7px 13px;border-radius:9px;border:1px solid var(--line);background:var(--panel2);cursor:pointer;font-weight:600;font-size:13px;color:var(--ink)}
 .tab.on{background:var(--acc);color:#fff;border-color:var(--acc)}
@@ -66,39 +97,112 @@ select.mk{padding:3px 6px}
 .help{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:13px;margin-bottom:10px;line-height:1.6}
 .row.blk{opacity:.8;background:repeating-linear-gradient(45deg,transparent,transparent 9px,rgba(255,92,108,.06) 9px,rgba(255,92,108,.06) 18px)}
 .btn{min-height:34px}
+details.more{width:100%;margin-top:8px}
+details.more>summary{cursor:pointer;color:var(--mut);font-size:12px;font-weight:600;list-style:none;user-select:none;padding:4px 0}
+details.more>summary::-webkit-details-marker{display:none}
+details.more>summary:before{content:"▸ ";display:inline-block}
+details.more[open]>summary:before{content:"▾ "}
+.morebox{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid var(--line)}
+.fgroup{display:flex;gap:4px;align-items:center}
+.fgroup .lbl{font-size:11px;color:var(--mut)}
+input.num-s{width:76px}
+select#fpd{min-width:170px}
+.chipbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px}
+.chipbar .fchip{display:inline-flex;align-items:center;gap:5px;cursor:pointer;background:var(--chip);border-radius:20px;padding:2px 9px;font-size:11px;color:var(--ink)}
+.chipbar .cnt{font-size:12px;color:var(--mut);font-weight:700}
+.nm{cursor:pointer}
+.detail{margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
+.dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px 14px;font-size:12px}
+.dgrid .lbl{display:block;color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px}
+.story{margin-top:8px;font-size:12px}
+.story .lbl{display:block;color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;margin-bottom:3px}
+.story-line{padding:3px 0;border-bottom:1px solid var(--line)}
+.story-line:last-child{border-bottom:none}
+.story-line b{color:var(--mut);font-weight:600;margin-right:6px}
+.freshbanner{background:rgba(245,166,35,.14);border-bottom:1px solid rgba(245,166,35,.4);color:#d98a10;font-size:11px;padding:5px 16px;text-align:center}
+.jump-hl{box-shadow:0 0 0 2px var(--acc) inset;border-radius:11px}
+.stamp{display:inline-flex;align-items:center;gap:4px}
+.stamp .undo{cursor:pointer;opacity:.7;font-weight:800}
+.stamp .undo:hover{opacity:1}
+.sec-empty-note{color:var(--mut);font-size:12px;padding:2px 0 8px}
 </style>
 </head>
 <body>
 <header>
+  <div id="freshBanner" style="display:none"></div>
   <div class="hrow">
     <div>
       <h1>🏠 Crestbrick Rental Matchmaker</h1>
       <div class="sub" id="sub"></div>
     </div>
     <div class="kpis" id="kpis"></div>
+    <span class="chip mut" id="syncPill" title="CRM sync status">▣ local only</span>
+    <button id="themeToggle" class="themebtn" title="Toggle light/dark">🌙</button>
   </div>
   <div class="tabs" id="tabs">
-    <div class="tab on" data-v="work">⭐ Today's Worklist</div>
+    <div class="tab on" data-v="work">📋 Today</div>
+    <div class="tab" data-v="pipeline">📈 Pipeline</div>
     <div class="tab" data-v="listing">🏠 By Listing</div>
     <div class="tab" data-v="tenant">🙋 By Tenant</div>
+    <div class="tab" data-v="landlords">🏢 Landlords</div>
+    <div class="tab" data-v="alltenants">🙋‍♀️ All Tenants</div>
+    <div class="tab" data-v="sales">🏡 For Sale</div>
+    <div class="tab" data-v="revival">♻️ Revival</div>
   </div>
   <div class="filters">
-    <input id="q" placeholder="Search tenant, landlord, district, area, postal…">
-    <select id="fd"><option value="">All districts</option></select>
+    <input id="q" placeholder="Search tenant, landlord, district, area, postal, occupation…">
+    <select id="fd"><option value="">District: all</option></select>
     <select id="fv">
       <option value="">All verdicts</option>
       <option value="QUALIFIED">Qualified</option>
       <option value="NEEDS_INFO">Needs info</option>
       <option value="BLOCKED">Has conflict</option>
     </select>
-    <input id="fr" type="number" placeholder="Max rent $" style="width:120px">
-    <label class="tog"><input type="checkbox" id="fc"> hide cold &gt;5d</label>
+    <select id="fs"><option value="">All statuses</option></select>
+    <label class="tog"><input type="checkbox" id="fdead" checked> hide cold (30d+)</label>
     <label class="tog"><input type="checkbox" id="fh"> hide actioned</label>
-    <button id="clr" class="tab">↺ Clear filters</button>
+    <button id="exp" class="tab">⬇ Export list</button>
+    <button id="clr" class="tab">↺ Clear all</button>
   </div>
+  <details class="more" id="moreFilters">
+    <summary>☰ More filters</summary>
+    <div class="morebox">
+      <div class="fgroup"><span class="lbl">Tenant budget $</span><input id="fbmin" type="number" class="num-s" placeholder="min"><span class="lbl">to</span><input id="fbmax" type="number" class="num-s" placeholder="max"></div>
+      <select id="fgender"><option value="">Any gender</option><option value="female">Female</option><option value="male">Male</option><option value="mixed">Couple / mixed</option></select>
+      <select id="fpax"><option value="">Any pax</option><option value="1">1 pax</option><option value="2">2 pax</option><option value="3+">3 plus pax</option></select>
+      <select id="fpass"><option value="">Any pass type</option></select>
+      <select id="fethnat"><option value="">Any nationality/ethnicity</option></select>
+      <select id="flease">
+        <option value="">Any lease term</option>
+        <option value="u6">Under 6 months</option>
+        <option value="6to11">6 to 11 months</option>
+        <option value="12">12 months</option>
+        <option value="24p">24 months plus</option>
+      </select>
+      <select id="fmovein">
+        <option value="">Any move in</option>
+        <option value="this">Move in this month</option>
+        <option value="next">Move in next month</option>
+        <option value="later">Move in later</option>
+        <option value="unknown">Move in unknown</option>
+      </select>
+      <div class="fgroup"><span class="lbl">Tenant's preferred districts</span><select id="fpd" multiple size="5"></select></div>
+      <select id="ffresh"><option value="">Any recency</option><option value="7">Contacted within 7 days</option><option value="14">Contacted within 14 days</option><option value="30">Contacted within 30 days</option></select>
+      <select id="fptype"><option value="">Any property type</option></select>
+      <div class="fgroup"><span class="lbl">Listing rent $</span><input id="frbmin" type="number" class="num-s" placeholder="min"><span class="lbl">to</span><input id="frbmax" type="number" class="num-s" placeholder="max"></div>
+      <select id="fsort">
+        <option value="recency" selected>Sort tenants: recency</option>
+        <option value="budget">Sort tenants: budget high to low</option>
+        <option value="movein">Sort tenants: move in soonest</option>
+      </select>
+    </div>
+  </details>
+  <div class="chipbar" id="presetBar"></div>
+  <div class="chipbar" id="activeFilters"></div>
 </header>
 <main>
   <div id="work"></div>
+  <div id="pipeline" style="display:none"></div>
   <div id="listing" class="split" style="display:none">
     <div class="rail" id="lrail"></div>
     <div class="panel" id="lpanel"><div class="empty">Pick a listing on the left to see ranked tenants.</div></div>
@@ -107,27 +211,46 @@ select.mk{padding:3px 6px}
     <div class="rail" id="trail"></div>
     <div class="panel" id="tpanel"><div class="empty">Pick a tenant on the left to see matching available listings.</div></div>
   </div>
+  <div id="landlords" style="display:none"></div>
+  <div id="alltenants" style="display:none"></div>
+  <div id="sales" style="display:none"></div>
+  <div id="revival" style="display:none"></div>
   <div class="legend" id="legend"></div>
 </main>
+<div class="drawer-bg" id="drawerBg"></div>
+<aside class="drawer" id="drawer" role="dialog" aria-label="CRM record"></aside>
 <script>const DATA = __DATA__;</script>
 <script>
 const $=s=>document.querySelector(s), el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
+const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 const TODAY=new Date(DATA.generated+"T00:00:00");
 const AREA=DATA.districts||{};
+const SOURCING={}; (DATA.area_demand||[]).forEach(d=>{ if(d.sourcing_priority) SOURCING[d.district]=d.sourcing_priority; });
 const ADJ={D1:["D2","D4","D6","D7"],D2:["D1","D3","D4"],D3:["D2","D4","D5","D10"],D4:["D1","D2","D3","D5"],D5:["D3","D4","D10","D21","D22"],D6:["D1","D7","D9"],D7:["D1","D6","D8","D14"],D8:["D7","D9","D11","D12","D13"],D9:["D6","D8","D10","D11"],D10:["D3","D5","D9","D11","D21"],D11:["D8","D9","D10","D12","D20"],D12:["D8","D11","D13","D20"],D13:["D8","D12","D14","D19","D20"],D14:["D7","D13","D15","D16","D19"],D15:["D14","D16"],D16:["D14","D15","D17","D18"],D17:["D16","D18"],D18:["D16","D17","D19"],D19:["D13","D14","D18","D20","D28"],D20:["D11","D12","D13","D19","D26","D28"],D21:["D5","D10","D23"],D22:["D5","D21","D23"],D23:["D22","D24","D25","D26"],D24:["D23","D25"],D25:["D23","D24","D26","D27"],D26:["D20","D23","D25","D28"],D27:["D25","D26","D28"],D28:["D19","D20","D26","D27"]};
+// Preferred district filter groups -- fixed literal groups as specified: East D15-18,
+// West D5/21-23, North D25-28, Northeast D19/20, Central = every other district (D1-4,
+// D6-14, D24). D24 sits in Central here by that literal definition, not geography.
+const REGION_DISTRICTS={East:["D15","D16","D17","D18"],West:["D5","D21","D22","D23"],North:["D25","D26","D27","D28"],Northeast:["D19","D20"]};
+(function(){ const assigned=new Set(Object.values(REGION_DISTRICTS).flat()); const all=Array.from({length:28},(_,i)=>"D"+(i+1)); REGION_DISTRICTS.Central=all.filter(d=>!assigned.has(d)); })();
 const days=d=>{ if(!d) return null; const x=new Date(d+"T00:00:00"); if(isNaN(x)) return null; return Math.round((TODAY-x)/864e5); };
-const fname=n=>(n||"there").split(/[ ,(]/)[0];
+// a saved name of "" or literally "Unknown" (the DB's own placeholder for an unresolved
+// contact) both mean "no real name on file" -- treat them the same everywhere.
+const isRealName=n=>{ const s=String(n||"").trim(); return !!s && s.toLowerCase()!=="unknown"; };
+const dispName=n=>isRealName(n) ? n : "(no name saved)";
+const fname=n=>isRealName(n) ? String(n).trim().split(/[ ,(]/)[0] : "";
+const greet=n=>{ const fn=fname(n); return fn ? ("Hi "+fn+", ") : "Hi, "; };
 const rentTxt=l=> l.rent_min&&l.rent_max&&l.rent_min!==l.rent_max?("$"+l.rent_min+" to $"+l.rent_max): l.rent_min?("$"+l.rent_min): l.rent_max?("$"+l.rent_max):"rent TBC";
 const areaName=l=> l.address || AREA[l.district] || l.district || "";
+const DUP_PHONES=new Set((DATA.duplicate_phones||[]).map(d=>d.phone).filter(Boolean));
 
 // ---- matching engine (covers ALL available listings) ----
 function score(l,t){
   const flags=[]; let hard=false;
   const b = t.budget || t.budget_max || null;
-  // budget 0..30
+  // budget 0..30 — landlord's minimum ask is a hard floor, no grace period
   let sb=15;
   if(b!=null){
-    if(l.rent_min&&b<l.rent_min){ sb=4; flags.push("over landlord's min ($"+l.rent_min+")"); if(b<l.rent_min*0.9) hard=true; }
+    if(l.rent_min&&b<l.rent_min){ sb=4; flags.push("under landlord's min ($"+l.rent_min+")"); hard=true; }
     else if(l.rent_max&&b>l.rent_max*1.6){ sb=14; flags.push("budget well above room (may want whole unit)"); }
     else sb=30;
   } else flags.push("budget unknown");
@@ -144,19 +267,29 @@ function score(l,t){
   // move-in 0..15
   let sm=8; const di=days(t.move_in);
   if(t.move_in){ if(di!=null){ if(di<=35&&di>=-40) sm=15; else if(di<=95) sm=10; else sm=6; } }
-  // freshness 0..15
-  let sf=2; const dc=days(t.last_contact);
-  if(dc!=null){ if(dc<=7) sf=15; else if(dc<=30) sf=10; else if(dc<=90) sf=5; else sf=2; }
+  // freshness 0..15 — weaker side of tenant/landlord contact recency (a stale landlord
+  // is just as likely to have moved on as a stale tenant, so this isn't tenant-only)
+  const freshScore=d=> d==null?2: d<=7?15: d<=30?10: d<=90?5:2;
+  const dc=days(t.last_contact), dl=days(l.last_contact);
+  const sf=Math.min(freshScore(dc), freshScore(dl));
   // hard gates -> flags
   const g=l.gates;
-  if(g.gender==="female_only" && /^m/i.test(t.gender||"")){ flags.push("landlord: female only"); hard=true; }
-  if(g.gender==="male_only" && /^f/i.test(t.gender||"")){ flags.push("landlord: male only"); hard=true; }
+  const tg=(t.gender||"").trim();
+  let needsInfo=false;
+  if(g.gender==="female_only"){
+    if(/^m/i.test(tg)){ flags.push("landlord: female only"); hard=true; }
+    else if(!tg){ flags.push("landlord: female only — tenant gender not on file, confirm before offering"); needsInfo=true; }
+  }
+  if(g.gender==="male_only"){
+    if(/^f/i.test(tg)){ flags.push("landlord: male only"); hard=true; }
+    else if(!tg){ flags.push("landlord: male only — tenant gender not on file, confirm before offering"); needsInfo=true; }
+  }
   const eth=(t.ethnicity||"").toLowerCase(), er=g.ethnicity||{rule:"any",races:[]};
   if(er.rule==="exclude" && er.races.some(r=>eth.includes(r))){ flags.push("landlord excludes "+er.races.join("/")); hard=true; }
   if(er.rule==="only" && er.races.length && !er.races.some(r=>eth.includes(r))){ flags.push("landlord wants "+er.races.join("/")+" only"); hard=true; }
   if(g.max_pax!=null && t.pax!=null && t.pax>g.max_pax){ flags.push("pax "+t.pax+" > max "+g.max_pax); hard=true; }
   const total=sb+sl+sle+sm+sf;
-  let verdict = hard? "BLOCKED" : (b==null || sl<=6) ? "NEEDS_INFO" : "QUALIFIED";
+  let verdict = hard? "BLOCKED" : (needsInfo || b==null || sl<=6) ? "NEEDS_INFO" : "QUALIFIED";
   return {total,parts:{budget:sb,location:sl,lease:sle,movein:sm,fresh:sf},flags,verdict,dc};
 }
 // build all matches
@@ -167,35 +300,397 @@ for(const m of MATCHES){ (byListing[m.l.id]=byListing[m.l.id]||[]).push(m); (byT
 for(const k in byListing) byListing[k].sort((a,b)=>b.s.total-a.s.total);
 for(const k in byTenant) byTenant[k].sort((a,b)=>b.s.total-a.s.total);
 
-// ---- status (localStorage) ----
-const skey=(l,t)=>"cbk_"+l+"_"+t;
-const getS=(l,t)=>localStorage.getItem(skey(l,t))||"";
-const setS=(l,t,v)=>{ v?localStorage.setItem(skey(l,t),v):localStorage.removeItem(skey(l,t)); render(); };
+// ---- CRM store -------------------------------------------------------------------
+// The durable half of the app. Everything Winfred RECORDS (stage, notes, tasks, flags,
+// contacted stamps, match verdicts) lives here and syncs to Postgres via /api/crm.
+// Everything the app KNOWS (names, phones, budgets, last message dates) still comes from
+// DATA, is rebuilt by build.py, and is never written back to. Keeping those two apart is
+// what stops a nightly rebuild and the app from overwriting each other.
+//
+// Offline first on purpose. Every write lands in localStorage and renders immediately,
+// then queues for the server; the queue survives a reload, so a write made on the MRT
+// with no signal is still there when the connection returns. If DATABASE_URL was never
+// set the API answers 501 and the app simply stays local, exactly as it behaved before
+// this backend existed — that is a supported mode, not a failure.
+const CRM=(function(){
+  const LKEY="cbk_crm_v1", QKEY="cbk_crm_queue_v1", MKEY="cbk_crm_migrated_v1";
+  const API="/api/crm";
+  let S={entities:{},notes:[],tasks:[],match:{},activity:[]};
+  let queue=[], mode="local", lastErr="", timer=null, retryTimer=null, backoff=0, booted=false, nextTmp=-1;
+  const RETRY_MIN=5000, RETRY_MAX=60000;
+
+  const j=(k,d)=>{ try{ const v=localStorage.getItem(k); return v?JSON.parse(v):d; }catch(e){ return d; } };
+  const save=()=>{ try{ localStorage.setItem(LKEY,JSON.stringify(S)); localStorage.setItem(QKEY,JSON.stringify(queue)); }catch(e){} };
+  const today=()=>new Date().toISOString().slice(0,10);
+
+  // A person, not a row. Keyed by phone whenever there is one so the same human matches
+  // across their tenant row, their background card and the roster — the same reason the
+  // old contacted stamps were phone keyed. Rows with no phone fall back to kind:id.
+  // Two records that genuinely share a phone (the app's own duplicate-phone warning)
+  // therefore share one CRM record; that is the same WhatsApp contact, so it is correct.
+  function keyOf(s){ if(!s) return null; const p=normPhone(s.phone); return p?("phone:"+p):(s.id?(s.kind||"person")+":"+s.id:null); }
+  function ent(s){ const k=keyOf(s); return k?(S.entities[k]||null):null; }
+  function ensure(k,s){ return S.entities[k]||(S.entities[k]={key:k,kind:(s&&s.kind)||"person",ref_id:s&&s.id,name:s&&s.name,phone:s&&s.phone,stage:"new",flagged:false,archived:false}); }
+
+  function push(op){ queue.push(op); save(); schedule(); }
+  function schedule(){ if(mode==="local") return; clearTimeout(timer); timer=setTimeout(flush,600); }
+  // A failed flush has to keep retrying by itself. The 'online' event is not enough: the
+  // realistic outage is the backend or the database being unreachable while the browser
+  // still believes it is online, and without this the queue would sit untouched until the
+  // next write or the next reload — which for a note typed just before locking the phone
+  // could be days.
+  function retryLater(){
+    clearTimeout(retryTimer);
+    backoff = backoff ? Math.min(backoff*2, RETRY_MAX) : RETRY_MIN;
+    retryTimer=setTimeout(()=>{ if(mode==="local") return; queue.length?flush():resync(); }, backoff);
+  }
+  // Re-GET when there is nothing to send. Recovers the pill from "offline" once the
+  // backend is reachable again, and picks up anything written on Winfred's other device.
+  async function resync(){
+    try{
+      const r=await fetch(API,{headers:{"Accept":"application/json"}});
+      if(r.status===501){ mode="local"; paint(); return; }
+      if(!r.ok) throw new Error("HTTP "+r.status);
+      adopt(await r.json()); mode="cloud"; lastErr=""; backoff=0;
+      save(); paint(); if(window.render) render();
+    }catch(e){ mode="offline"; lastErr=String(e&&e.message||e); paint(); retryLater(); }
+  }
+
+  async function flush(){
+    if(mode==="local"||!queue.length||mode==="syncing") return;
+    const sending=queue.slice(0,200), prev=mode;
+    mode="syncing"; paint();
+    try{
+      const r=await fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ops:sending})});
+      // 501 = the deployment has no DATABASE_URL. Stop trying, but KEEP the queue: if this
+      // is a misconfiguration rather than a deliberate local-only deploy, discarding it
+      // here would throw away real writes that the next correct deploy would have taken.
+      if(r.status===501){ mode="local"; save(); paint(); return; }
+      if(!r.ok) throw new Error("HTTP "+r.status);
+      adopt(await r.json());
+      queue=queue.slice(sending.length); mode="cloud"; lastErr=""; backoff=0; clearTimeout(retryTimer);
+      save(); paint(); if(queue.length) schedule(); else if(window.render) render();
+    }catch(e){
+      // Keep the queue. It is persisted, so the writes are not lost — they go out on the
+      // retry below, on the next write, on the next boot, or when the browser fires
+      // 'online', whichever comes first.
+      mode = prev==="syncing" ? "offline" : prev; if(mode!=="local") mode="offline";
+      lastErr=String(e&&e.message||e); paint(); retryLater();
+    }
+  }
+
+  // Server snapshot becomes the truth for everything already acknowledged; anything still
+  // in the local queue is re-applied on top so an in flight write does not visibly revert.
+  function adopt(d){
+    const e={}; (d.entities||[]).forEach(x=>e[x.key]=x);
+    const m={}; (d.match||[]).forEach(x=>m[x.listing_id+"|"+x.tenant_id]=x.status);
+    S={entities:e,notes:d.notes||[],tasks:d.tasks||[],match:m,activity:d.activity||[]};
+    queue.forEach(replay);
+  }
+  function replay(o){
+    if(o.op==="entity"&&o.key){ const x=ensure(o.key,o); Object.assign(x,o.patch||{}); }
+    else if(o.op==="match"&&o.listing_id){ const k=o.listing_id+"|"+o.tenant_id; o.status?S.match[k]=o.status:delete S.match[k]; }
+  }
+
+  // One time import of the pre-backend localStorage keys so no stamp, flag or mark that
+  // Winfred already made is lost the day the CRM ships.
+  function migrate(){
+    if(localStorage.getItem(MKEY)) return;
+    const ops=[];
+    const idIndex={};
+    const reg=(kind,arr)=>(arr||[]).forEach(o=>{ if(o&&o.id!=null) idIndex[String(o.id)]={kind,id:o.id,name:o.name,phone:o.phone}; });
+    reg("listing",DATA.listings); reg("tenant",DATA.tenants); reg("landlord",DATA.all_landlords);
+    reg("tenant",DATA.all_tenants); reg("sale",DATA.sales);
+    for(let i=0;i<localStorage.length;i++){
+      const k=localStorage.key(i); if(!k||!k.startsWith("cbk_")) continue;
+      const v=localStorage.getItem(k);
+      if(k.startsWith("cbk_contacted_")){
+        const phone=k.slice(14); if(!phone||!/^\d{6,}$/.test(phone)) continue;
+        ops.push({op:"entity",key:"phone:"+phone,kind:"person",phone:phone,patch:{contacted_on:v}});
+      } else if(k.startsWith("cbk_flag_")){
+        const id=k.slice(9), s=idIndex[id]; if(!s) continue;
+        ops.push(Object.assign({op:"entity",key:keyOf(s),kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone},{patch:{flagged:true}}));
+      } else if(/^cbk_[^_]+_[^_]+$/.test(k)&&v&&!["cbk_theme","cbk_filters_v1"].includes(k)){
+        const parts=k.slice(4).split("_"); if(parts.length!==2) continue;
+        ops.push({op:"match",listing_id:parts[0],tenant_id:parts[1],status:v});
+      }
+    }
+    ops.forEach(o=>{ if(o.op==="entity"){ const x=ensure(o.key,o); Object.assign(x,o.patch); } else replay(o); });
+    queue=queue.concat(ops);
+    localStorage.setItem(MKEY,today()); save();
+  }
+
+  function paint(){
+    const el=document.getElementById("syncPill"); if(!el) return;
+    const n=queue.length;
+    const map={
+      local:   ["local only","mut","Saved on this device only — no DATABASE_URL is set on the Vercel project, so nothing syncs."],
+      cloud:   [n?("syncing "+n):"synced", n?"a":"g", n?"Sending "+n+" pending change(s).":"All changes saved to the cloud."],
+      syncing: ["syncing…","a","Sending changes."],
+      offline: ["offline · "+n+" queued","r","Cannot reach the CRM backend"+(lastErr?" ("+lastErr+")":"")+". Your changes are saved on this device and will send automatically."],
+    };
+    const [txt,cls,tip]=map[mode]||map.local;
+    el.className="chip "+cls; el.textContent=(mode==="cloud"&&!n?"☁ ":mode==="local"?"▣ ":"⟳ ")+txt; el.title=tip;
+  }
+
+  return {
+    get mode(){ return mode; }, get pending(){ return queue.length; }, paint, flush, keyOf,
+    async boot(){
+      if(booted) return; booted=true;
+      S=j(LKEY,S); queue=j(QKEY,[]);
+      if(!S.entities) S.entities={}; if(!S.match) S.match={};
+      migrate();
+      paint();
+      try{
+        const r=await fetch(API,{headers:{"Accept":"application/json"}});
+        if(r.status===501){ mode="local"; }
+        else if(r.ok){ adopt(await r.json()); mode="cloud"; save(); }
+        else throw new Error("HTTP "+r.status);
+      }catch(e){ mode="offline"; lastErr=String(e&&e.message||e); }
+      paint(); if(window.render) render();
+      if(mode!=="local"&&queue.length) flush();
+      else if(mode==="offline") retryLater();   // reachable again later even with nothing queued
+      window.addEventListener("online",()=>{ if(mode==="offline"){ backoff=0; flush(); } });
+      // Coming back to the app is the moment a stalled queue most wants a retry, and it
+      // costs nothing when there is nothing pending.
+      document.addEventListener("visibilitychange",()=>{
+        if(document.visibilityState==="visible"&&mode!=="local"&&queue.length){ backoff=0; flush(); }
+      });
+    },
+    entity:ent,
+    stage(s){ const e=ent(s); return e?(e.stage||"new"):"new"; },
+    setStage(s,v){ const k=keyOf(s); if(!k) return; Object.assign(ensure(k,s),{stage:v});
+      push({op:"entity",key:k,kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone,patch:{stage:v}}); save(); },
+    setPlan(s,action,due){ const k=keyOf(s); if(!k) return; Object.assign(ensure(k,s),{next_action:action||null,next_due:due||null});
+      push({op:"entity",key:k,kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone,patch:{next_action:action||null,next_due:due||null}}); save(); },
+    contacted(phone){ const e=ent({phone}); return e&&e.contacted_on||null; },
+    setContacted(phone,name){ const p=normPhone(phone); if(!p) return; const k="phone:"+p;
+      Object.assign(ensure(k,{kind:"person",phone:p,name}),{contacted_on:today()});
+      push({op:"entity",key:k,kind:"person",phone:p,name:name||null,patch:{contacted_on:today()}}); save(); },
+    clearContacted(phone){ const p=normPhone(phone); if(!p) return; const k="phone:"+p;
+      if(S.entities[k]) S.entities[k].contacted_on=null;
+      push({op:"entity",key:k,kind:"person",phone:p,patch:{contacted_on:null}}); save(); },
+    flagged(s){ const e=ent(s); return !!(e&&e.flagged); },
+    toggleFlag(s){ const k=keyOf(s); if(!k) return; const e=ensure(k,s); e.flagged=!e.flagged;
+      push({op:"entity",key:k,kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone,patch:{flagged:e.flagged}}); save(); },
+    matchStatus(l,t){ return S.match[l+"|"+t]||""; },
+    setMatchStatus(l,t,v){ v?S.match[l+"|"+t]=v:delete S.match[l+"|"+t];
+      push({op:"match",listing_id:l,tenant_id:t,status:v||""}); save(); },
+    notes(s){ const k=keyOf(s); return k?S.notes.filter(n=>n.key===k):[]; },
+    addNote(s,body){ const k=keyOf(s); if(!k||!body) return; ensure(k,s);
+      S.notes.unshift({id:nextTmp--,key:k,body,created_at:new Date().toISOString()});
+      push({op:"note",key:k,kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone,body}); save(); },
+    delNote(id){ S.notes=S.notes.filter(n=>n.id!==id); if(id>0) push({op:"note_delete",id}); save(); },
+    tasks(s){ if(!s) return S.tasks.slice(); const k=keyOf(s); return S.tasks.filter(t=>t.key===k); },
+    addTask(s,title,due){ if(!title) return; const k=s?keyOf(s):null; if(k) ensure(k,s);
+      S.tasks.push({id:nextTmp--,key:k,title,due:due||null,done:false,created_at:new Date().toISOString()});
+      push(Object.assign({op:"task",title,due:due||null},k?{key:k,kind:s.kind,ref_id:s.id,name:s.name,phone:s.phone}:{})); save(); },
+    toggleTask(id){ const t=S.tasks.find(x=>x.id===id); if(!t) return; t.done=!t.done;
+      if(id>0) push({op:"task",id,title:t.title,due:t.due,done:t.done}); save(); },
+    delTask(id){ S.tasks=S.tasks.filter(x=>x.id!==id); if(id>0) push({op:"task_delete",id}); save(); },
+    activity(s){ if(!s) return S.activity.slice(); const k=keyOf(s); return S.activity.filter(a=>a.key===k); },
+    all(){ return Object.values(S.entities); },
+  };
+})();
+const STAGE_LABELS={new:"New",contacted:"Contacted",qualified:"Qualified",viewing_set:"Viewing set",viewed:"Viewed",offer:"Offer",closed_won:"Closed won",closed_lost:"Lost",dormant:"Dormant"};
+const STAGE_ORDER=["new","contacted","qualified","viewing_set","viewed","offer","closed_won","closed_lost","dormant"];
+const subjOf=(kind,o)=>({kind,id:o.id,name:o.name,phone:o.phone});
+
+// ---- status (now CRM backed; same call signature the renderers already use) ----
+const getS=(l,t)=>CRM.matchStatus(l,t);
+const setS=(l,t,v)=>{ CRM.setMatchStatus(l,t,v); render(); };
+
+// ---- contacted stamping -- tapping ANY wa.me draft link stamps that phone number
+// "contacted [date]" (device-local only, never touches the databases). Keyed by phone
+// digits since the same person can appear as a tenant row, a background card, and a
+// roster row -- one stamp should cover all of them.
+const todayISO=()=>new Date().toISOString().slice(0,10);
+const getContacted=phone=>CRM.contacted(phone);
+const setContacted=(phone,name)=>CRM.setContacted(phone,name);
+const clearContacted=phone=>CRM.clearContacted(phone);
+const isContactedToday=phone=>getContacted(phone)===todayISO();
+function contactedChip(phone){
+  const d=getContacted(phone);
+  if(!d) return "";
+  return '<span class="chip a stamp">contacted '+d+'<b class="undo" data-undo-contact="'+esc(phone)+'" title="Undo">×</b></span>';
+}
 
 // ---- WA draft ----
+// re-engagement draft for the background card -- anchored to what they originally asked
+// about, not the currently matched room, so it reads naturally days/weeks later.
+function pickWeekday(){
+  const names=["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
+  let d=new Date(TODAY.getTime()+2*864e5);
+  if(d.getDay()===6) d=new Date(d.getTime()+864e5); // skip Saturday, already offered as the alt slot
+  return names[d.getDay()];
+}
+// A specific time offer converts far better than an open ask (Winfred's own data: 96.6%
+// vs 30.7%) -- so every draft below offers a concrete slot. When the room itself has a
+// locked viewing time on file, use that; otherwise fall back to two fixed generic slots
+// rather than an open "when works for you" ask, since even a generic specific offer
+// beats an open one.
+function viewingLooksLocked(v){
+  const s=(v||"").toLowerCase().trim();
+  if(!s) return false;
+  const hasDay=/\b(mon|tue|wed|thu|fri|sat|sun)\w*\b/.test(s) || /\b\d{1,2}\/\d{1,2}\b/.test(s) || /\b\d{4}-\d{2}-\d{2}\b/.test(s);
+  const hasTime=/\d{1,2}(:\d{2})?\s*(am|pm)\b/.test(s) || /\b\d{3,4}\s*h(rs)?\b/.test(s);
+  // Winfred's own convention: a genuinely locked slot is marked "confirmed" right in
+  // the note (e.g. "(confirmed 7 Aug 2026)") -- treat that as decisive even if an
+  // unrelated word like "anytime" also appears in the same free text field, since these
+  // notes often cram in something else too (e.g. door access instructions).
+  if(/\bconfirmed\b/.test(s) && hasDay) return true;
+  if(/\b(tbc|flexible|any\s*time|anytime|call|arrange|check|ask|pending|let me know)\b/.test(s)) return false;
+  return hasDay && hasTime;
+}
+function viewingSlotText(l){ return (l && l.viewing && viewingLooksLocked(l.viewing)) ? l.viewing.trim() : null; }
+function fixedTimeOptions(){ return pickWeekday()+" 7pm or Sat 3pm"; }
 function draft(l,t){
-  const fn=fname(t.name), area=(AREA[l.district]||l.district||"the area"), rt=rentTxt(l);
-  const mv=t.move_in?(" around "+t.move_in):"";
+  const area=(AREA[l.district]||l.district||"the area"), rt=rentTxt(l);
+  const slot=viewingSlotText(l);
   if(!t.budget && !t.budget_max)
-    return "Hi "+fn+", a room just opened in "+area+" ("+l.district+") at about "+rt+" a month. Whats your budget and when are you looking to move in so i can send you the right details";
-  return "Hi "+fn+", i have a room that fits what you are looking for in "+area+" ("+l.district+"), "+rt+" a month, can move in"+mv+". Free to view this week";
+    return greet(t.name)+"a room just opened in "+area+" ("+l.district+") at about "+rt+" a month. Whats your budget and when are you looking to move in so i can send you the right details";
+  return greet(t.name)+"i have a room that fits what you are looking for in "+area+" ("+l.district+"), "+rt+" a month. Viewing this "+(slot||fixedTimeOptions())+", can you make it";
+}
+function storyDraft(t,l){
+  const listing=t.listing_enquired||"your enquiry";
+  const slot=viewingSlotText(l);
+  return greet(t.name)+"about the room you asked on "+listing+": a unit that fits just opened up. Viewing this "+(slot||fixedTimeOptions())+", can you make it";
 }
 const normPhone=raw=>{let p=(raw||"").replace(/[^0-9]/g,"");if(p.length===8&&/^[89]/.test(p))p="65"+p;return p;};
 const waLink=(l,t)=>{const p=normPhone(t.phone);return p?("https://wa.me/"+p+"?text="+encodeURIComponent(draft(l,t))):"";};
 const waPlain=(phone,msg)=>{const p=normPhone(phone);return p?("https://wa.me/"+p+(msg?"?text="+encodeURIComponent(msg):"")):"";};
 const mapLink=l=>"https://www.google.com/maps/search/?api=1&query="+encodeURIComponent(l.map_query||areaName(l));
 
+// ---- new filter helpers (budget/gender/pax/pass/nationality-ethnicity/lease/move-in/
+// preferred districts/recency) -- all scoped to the matching tenant pool (DATA.tenants,
+// the 210 "still looking" records) and DATA.listings, matching the data shape the filter
+// spec was written against. The separate All Tenants / Landlords rosters (DATA.all_tenants
+// / DATA.all_landlords) don't carry ethnicity/pass_type/budget_min/max at all, so these
+// are intentionally not wired into those two roster views.
+function tenantBudgetRange(t){
+  let lo=t.budget_min, hi=t.budget_max;
+  if(lo==null) lo = t.budget!=null ? t.budget : hi;
+  if(hi==null) hi = t.budget!=null ? t.budget : lo;
+  if(lo!=null && hi!=null && lo>hi){ const tmp=lo; lo=hi; hi=tmp; }
+  return [lo,hi];
+}
+function listingRentRange(l){
+  let lo=l.rent_min, hi=l.rent_max;
+  if(lo==null) lo=hi;
+  if(hi==null) hi=lo;
+  return [lo,hi];
+}
+function genderBucket(raw){
+  let t=(raw||"").trim().toLowerCase();
+  if(!t) return "";
+  const hasFemale=/\bfemales?\b/.test(t) || /\bgirls?\b/.test(t);
+  const stripped=t.replace(/females?/g,"");
+  const hasMale=/\bmales?\b/.test(stripped) || /\bboys?\b/.test(stripped) || /\bmen\b/.test(stripped);
+  if(t.includes("couple") || t.includes("mixed") || (hasFemale && hasMale)) return "mixed";
+  if(hasFemale) return "female";
+  if(hasMale) return "male";
+  return "";
+}
+function paxBucket(p){ if(p==null) return ""; return p>=3?"3+":String(p); }
+function leaseBucket(lm){ if(lm==null) return ""; if(lm<6) return "u6"; if(lm<12) return "6to11"; if(lm<24) return "12"; return "24p"; }
+function leaseLabel(code){ return {"u6":"under 6 months","6to11":"6 to 11 months","12":"12 months","24p":"24 months plus"}[code]||code; }
+function parseMoveIn(s){
+  if(!s) return null;
+  let m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
+  if(m) return new Date(+m[1], +m[2]-1, +m[3]);
+  m=/^(\d{4})-(\d{2})$/.exec(s);
+  if(m) return new Date(+m[1], +m[2]-1, 1);
+  const d=new Date(s);
+  return isNaN(d) ? null : d;
+}
+function moveInBucket(s){
+  const d=parseMoveIn(s);
+  if(!d) return "unknown";
+  const ty=TODAY.getFullYear(), tm=TODAY.getMonth();
+  const diff=(d.getFullYear()-ty)*12+(d.getMonth()-tm);
+  if(diff<=0) return "this"; // this month or already overdue -- most urgent, bucketed together
+  if(diff===1) return "next";
+  return "later";
+}
+function moveInBadge(t){
+  const b=moveInBucket(t.move_in);
+  if(b==="this") return '<span class="chip r">🔴 move in this month</span>';
+  if(b==="next") return '<span class="chip a">🟡 move in next month</span>';
+  return "";
+}
+function passBudget(t,bmin,bmax){
+  if(bmin==null && bmax==null) return true;
+  const [lo,hi]=tenantBudgetRange(t);
+  if(lo==null && hi==null) return false;
+  const ulo=bmin==null?-Infinity:bmin, uhi=bmax==null?Infinity:bmax;
+  const tlo=lo==null?-Infinity:lo, thi=hi==null?Infinity:hi;
+  return tlo<=uhi && thi>=ulo;
+}
+function passDistrictFilter(t,selected){
+  if(!selected || !selected.length) return true;
+  const pds=new Set([t.district, ...(t.preferred_districts||[])].filter(Boolean));
+  return selected.some(d=>pds.has(d));
+}
+function passFresh(dc,freshSel,hideDead){
+  if(hideDead && dc!=null && dc>30) return false;
+  if(freshSel){ const n=parseInt(freshSel,10); if(dc==null || dc>n) return false; }
+  return true;
+}
+function passTenantExtra(t,dc){
+  const f=F();
+  if(!passBudget(t,f.bmin,f.bmax)) return false;
+  if(f.gender && genderBucket(t.gender)!==f.gender) return false;
+  if(f.pax && paxBucket(t.pax)!==f.pax) return false;
+  if(f.pass && (t.pass_type||"").trim()!==f.pass) return false;
+  if(f.ethnat){ const ln=(t.nationality||"").toLowerCase(), le=(t.ethnicity||"").toLowerCase(); if(ln!==f.ethnat.toLowerCase() && le!==f.ethnat.toLowerCase()) return false; }
+  if(f.lease && leaseBucket(t.lease_months)!==f.lease) return false;
+  if(f.movein && moveInBucket(t.move_in)!==f.movein) return false;
+  if(!passDistrictFilter(t,f.pd)) return false;
+  if(!passFresh(dc,f.fresh,f.dead)) return false;
+  return true;
+}
+function passListingExtra(l){
+  const f=F();
+  if(f.ptype && (l.property_type||"")!==f.ptype) return false;
+  if(f.rbmin!=null || f.rbmax!=null){
+    const [lo,hi]=listingRentRange(l);
+    if(lo==null && hi==null) return false;
+    const ulo=f.rbmin==null?-Infinity:f.rbmin, uhi=f.rbmax==null?Infinity:f.rbmax;
+    const tlo=lo==null?-Infinity:lo, thi=hi==null?Infinity:hi;
+    if(!(tlo<=uhi && thi>=ulo)) return false;
+  }
+  if(f.s && l.availability!==f.s) return false;
+  return true;
+}
+function tenantSortComparator(mode){
+  if(mode==="budget") return (a,b)=>{ const ah=(a.budget??a.budget_max??-1), bh=(b.budget??b.budget_max??-1); return bh-ah; };
+  if(mode==="movein") return (a,b)=>{ const da=parseMoveIn(a.move_in), db=parseMoveIn(b.move_in); const va=da?da.getTime():Infinity, vb=db?db.getTime():Infinity; return va-vb; };
+  return (a,b)=>{ const da=days(a.last_contact), db=days(b.last_contact); return (da==null?1e9:da)-(db==null?1e9:db); };
+}
+
 // ---- filters/state ----
-let view="work", curL=null, curT=null;
-const F=()=>({q:$("#q").value.trim().toLowerCase(),d:$("#fd").value,v:$("#fv").value,r:parseInt($("#fr").value)||0,cold:$("#fc").checked,hide:$("#fh").checked});
+let view="work", curL=null, curT=null, VCOUNT=0;
+function parseNumOrNull(v){ v=(v||"").trim(); if(v==="") return null; const n=parseInt(v,10); return isNaN(n)?null:n; }
+const F=()=>({
+  q:$("#q").value.trim().toLowerCase(),d:$("#fd").value,v:$("#fv").value,s:$("#fs").value,
+  r:parseInt(($("#frbmax")||{}).value)||0,hide:$("#fh").checked,   // single rent-max source; the duplicate top bar "Max rent $" was removed 13 Aug 2026
+  bmin:parseNumOrNull($("#fbmin").value),bmax:parseNumOrNull($("#fbmax").value),
+  gender:$("#fgender").value,pax:$("#fpax").value,pass:$("#fpass").value,ethnat:$("#fethnat").value,
+  lease:$("#flease").value,movein:$("#fmovein").value,
+  pd:Array.from($("#fpd").selectedOptions).map(o=>o.value),
+  fresh:$("#ffresh").value,dead:$("#fdead").checked,
+  ptype:$("#fptype").value,rbmin:parseNumOrNull($("#frbmin").value),rbmax:parseNumOrNull($("#frbmax").value),
+  sort:$("#fsort").value,
+});
 function passFilter(m){
   const f=F();
   if(f.d && m.l.district!==f.d) return false;
   if(f.v && m.s.verdict!==f.v) return false;
   if(f.r && m.l.rent_min && m.l.rent_min>f.r) return false;
-  if(f.cold && m.s.dc!=null && m.s.dc>5) return false;
   if(f.hide && getS(m.l.id,m.t.id)) return false;
-  if(f.q){ const hay=(m.t.name+" "+m.l.name+" "+m.l.district+" "+(AREA[m.l.district]||"")+" "+m.l.address+" "+(m.t.preferred_location||"")+" "+(m.t.phone||"")).toLowerCase(); if(!hay.includes(f.q)) return false; }
+  if(!passTenantExtra(m.t,m.s.dc)) return false;
+  if(!passListingExtra(m.l)) return false;
+  if(f.q){ const hay=(m.t.name+" "+m.l.name+" "+m.l.district+" "+(AREA[m.l.district]||"")+" "+m.l.address+" "+(m.t.preferred_location||"")+" "+(m.t.phone||"")+" "+(m.t.occupation||"")+" "+(m.t.listing_enquired||"")).toLowerCase(); if(!hay.includes(f.q)) return false; }
   return true;
 }
 const vchip=v=> v==="QUALIFIED"?'<span class="chip g">✅ Good fit</span>': v==="NEEDS_INFO"?'<span class="chip a">❓ Ask a question</span>':'<span class="chip r">⛔ Not a fit</span>';
@@ -203,17 +698,70 @@ function scoreBar(p){
   const seg=[["#2ecc71",p.budget],["#5b8cff",p.location],["#f5a623",p.lease],["#9b8cff",p.movein],["#7a8aa8",p.fresh]];
   return '<span class="sb" title="budget '+p.budget+' · location '+p.location+' · lease '+p.lease+' · move-in '+p.movein+' · fresh '+p.fresh+'">'+seg.map(s=>'<i style="width:'+(s[1])+'px;background:'+s[0]+'"></i>').join('')+'</span>';
 }
-function coldChip(dc){ if(dc==null) return '<span class="chip">no contact date</span>'; if(dc<=5) return '<span class="chip g">heard '+dc+'d ago</span>'; if(dc<=30) return '<span class="chip a">cold '+dc+'d</span>'; return '<span class="chip r">stale '+dc+'d</span>'; }
+// HOT/WARM/COLD tiers -- cold/dead only past 30 days quiet (Winfred's rule, widened
+// from 5 days on 12 Aug). Landlord/listing/sale rows use the same coldChip function and
+// get the same tiering; nothing here hides those rows, it only colors a badge.
+// CEA register status for a co-broke counterparty, verified by contact number at build
+// time. null for our own landlords — they are not salespersons and are never checked.
+// CEA status of the co-broke AGENT who brought the listing (the name in
+// contact_label_source), verified by contact number at build time. The record's own phone
+// is the OWNER's and is never checked — they are not a salesperson.
+function ceaChip(r){
+  const c = r && r.cea; if(!c) return '';
+  const who = c.agent ? esc(c.agent) : 'agent';
+  const t = c.reg_no ? (c.name+' · '+c.reg_no+' · '+c.agency+' · valid to '+c.valid_until) : '';
+  if(c.status==='active')
+    return '<span class="chip g" title="'+t+'">✅ '+who+' · CEA '+c.reg_no+'</span>'
+      + (c.disciplinary?'<span class="chip r" title="Disciplinary actions on record">⚠ disciplinary</span>':'');
+  if(c.status==='expired')
+    return '<span class="chip r" title="'+t+'">❌ '+who+' · CEA EXPIRED '+c.valid_until+'</span>';
+  if(c.status==='not_registered')
+    return '<span class="chip r" title="No registered salesperson at this agent\'s contact number. Verify before sharing client info or splitting commission.">⚠ '+who+' NOT on CEA register</span>';
+  if(c.status==='agent_unknown')
+    return '<span class="chip a" title="Co-broke listing but the counterpart agent is not identified in cobroke-agents.json, so no CEA check was possible.">'+who+' not identified</span>';
+  return '<span class="chip a" title="Register lookup unavailable at build time">CEA unchecked</span>';
+}
+function coldChip(dc){ if(dc==null) return '<span class="chip">no contact date</span>'; if(dc<=7) return '<span class="chip g">heard '+dc+'d ago</span>'; if(dc<=30) return '<span class="chip a">quiet '+dc+'d</span>'; return '<span class="chip r">cold '+dc+'d</span>'; }
+// stale listing alarm -- no viewing time locked in AND we know the landlord has gone
+// quiet for over a week. Requires a known last_contact date (dc!=null) so a listing that
+// simply has no contact date recorded yet isn't wrongly flagged as stale.
+function isStaleAlarm(l){ const dc=days(l.last_contact); return !l.viewing && dc!=null && dc>7; }
+
+function tenantDetail(t){
+  const dc=days(t.last_contact);
+  const [lo,hi]=tenantBudgetRange(t);
+  const budgetLine = lo!=null||hi!=null ? (lo===hi||hi==null?("$"+(lo??hi)):lo==null?("$"+hi):("$"+lo+" to $"+hi)) : "budget unknown";
+  const story=(t.wa_story||[]);
+  const storyHtml = story.length
+    ? story.map(s=>'<div class="story-line">'+(s.date?'<b>'+esc(s.date)+'</b>':'')+esc(s.text)+'</div>').join("")
+    : '<div class="story-line mut">no chat history found</div>';
+  return '<div class="detail">'+
+    '<div class="dgrid">'+
+      '<div><span class="lbl">Occupation</span>'+esc(t.occupation||"?")+'</div>'+
+      '<div><span class="lbl">Nationality / pass</span>'+esc(t.nationality||"?")+' · '+esc(t.pass_type||"?")+'</div>'+
+      '<div><span class="lbl">Pax</span>'+esc(t.pax||"?")+'</div>'+
+      '<div><span class="lbl">Source</span>'+esc(t.listing_enquired||"not on file")+'</div>'+
+      '<div><span class="lbl">Budget</span>'+esc(budgetLine)+'</div>'+
+      '<div><span class="lbl">Move in</span>'+esc(t.move_in||"?")+'</div>'+
+      '<div><span class="lbl">Days quiet</span>'+(dc==null?"no contact date":dc+"d")+'</div>'+
+    '</div>'+
+    '<div class="story"><span class="lbl">Story so far</span>'+storyHtml+'</div>'+
+    '<div class="acts">'+
+      (t.phone?'<a class="btn w" data-wa-phone="'+esc(t.phone)+'" target="_blank" href="'+waPlain(t.phone,storyDraft(t,(byTenant[t.id]&&byTenant[t.id][0])?byTenant[t.id][0].l:null))+'">💬 WhatsApp: offer a viewing slot</a>':'')+
+      crmBtn("tenant",t)+
+    '</div>'+
+  '</div>';
+}
 
 function matchRow(m,showListing){
   const l=m.l,t=m.t,s=m.s, st=getS(l.id,t.id);
-  const blocked=s.verdict==="BLOCKED", cold=s.dc!=null&&s.dc>5, stale=s.dc!=null&&s.dc>30;
-  const waLabel= stale?('WhatsApp (stale '+s.dc+'d, double check)'): cold?('WhatsApp (cold '+s.dc+'d, double check)'):'WhatsApp draft';
+  const blocked=s.verdict==="BLOCKED", cold=s.dc!=null&&s.dc>30;
+  const waLabel= cold?('WhatsApp (quiet '+s.dc+'d, double check)'):'WhatsApp draft';
   const markSel='<select class="btn mk" data-mk="1"><option value="">Mark…</option><option'+(st=="Contacted"?" selected":"")+'>Contacted</option><option'+(st=="Viewing booked"?" selected":"")+'>Viewing booked</option><option'+(st=="Not interested"?" selected":"")+'>Not interested</option><option value="__clr">Clear</option></select>';
   const row=el("div","row"+(st?" done":"")+(blocked?" blk":""));
   const head = showListing
-    ? '<span class="nm">'+t.name+'</span> <span class="mut">→ '+l.name+' · '+l.district+' · '+rentTxt(l)+'</span>'+(l.availability==="Offer pending"?' <span class="chip a">offer pending, hold</span>':'')
-    : '<span class="nm">'+t.name+'</span> <span class="mut">'+(t.pass_type||'')+' '+(t.nationality||'')+'</span>';
+    ? '<span class="nm">'+dispName(t.name)+'</span> <span class="mut">→ '+dispName(l.name)+' · '+l.district+' · '+rentTxt(l)+'</span>'+(l.availability==="Offer pending"?' <span class="chip a">offer pending, hold</span>':'')
+    : '<span class="nm">'+dispName(t.name)+'</span> <span class="mut">'+(t.pass_type||'')+' '+(t.nationality||'')+'</span>';
   row.innerHTML=
     '<div class="rtop">'+head+' '+vchip(s.verdict)+scoreBar(s.parts)+'<span class="sc">'+s.total+'</span></div>'+
     '<div class="rtop" style="margin-top:5px">'+
@@ -222,61 +770,398 @@ function matchRow(m,showListing){
       '<span class="chip">lease '+(t.lease_months||'?')+'mo</span>'+
       '<span class="chip">move '+(t.move_in||'?')+'</span>'+
       '<span class="chip">'+(t.district||'?')+(t.preferred_location?' · '+t.preferred_location.slice(0,28):'')+'</span>'+
-      coldChip(s.dc)+ (st?'<span class="chip a">'+st+'</span>':'')+
+      coldChip(s.dc)+ moveInBadge(t) + (DUP_PHONES.has(t.phone)?'<span class="chip a">⚠ duplicate phone on file</span>':'') + (st?'<span class="chip a">'+st+'</span>':'') + contactedChip(t.phone)+
     '</div>'+
     (blocked
-      ? '<div class="gap" style="color:#ff6b78;font-style:normal">⛔ Do not offer this room to '+fname(t.name)+' — '+(s.flags[0]||'landlord requirement conflict')+'</div>'
+      ? '<div class="gap" style="color:#ff6b78;font-style:normal">⛔ Do not offer this room to '+dispName(t.name)+' — '+(s.flags[0]||'landlord requirement conflict')+'</div>'
         +'<div class="acts"><a class="btn" target="_blank" href="'+mapLink(l)+'">Map</a>'+markSel+'</div>'
       : (s.flags.length?'<div class="gap">⚑ '+s.flags.join(' · ')+'</div>':'')
-        +(t.phone?'<div class="mk" style="margin-top:6px">→ you will message <b>'+t.name+'</b> · '+t.phone+'</div>':'')
+        +(t.phone?'<div class="mk" style="margin-top:6px">→ you will message <b>'+dispName(t.name)+'</b> · '+t.phone+'</div>':'')
         +'<div class="acts">'
-          +(t.phone? '<a class="btn'+(cold?'"':' w"')+(cold?' style="background:#f5a623;color:#3a2600;border-color:#f5a623"':'')+' target="_blank" href="'+waLink(l,t)+'">'+waLabel+'</a>':'<span class="btn mut">no phone on file</span>')
+          +(t.phone? '<a class="btn'+(cold?'"':' w"')+(cold?' style="background:#f5a623;color:#3a2600;border-color:#f5a623"':'')+' data-wa-phone="'+esc(t.phone)+'" target="_blank" href="'+waLink(l,t)+'">'+waLabel+'</a>':'<span class="btn mut">no phone on file</span>')
           +'<button class="btn" data-copy="1">Copy draft</button>'
           +(t.phone?'<a class="btn" href="tel:'+t.phone+'">Call</a>':'')
           +'<a class="btn" target="_blank" href="'+mapLink(l)+'">Map</a>'
           +markSel
+          +crmBtn("tenant",t)
         +'</div>');
   const cp=row.querySelector('[data-copy]'); if(cp) cp.onclick=()=>{navigator.clipboard.writeText(draft(l,t));cp.textContent="Copied ✓";};
   row.querySelector('[data-mk]').onchange=e=>{const v=e.target.value;setS(l.id,t.id, v==="__clr"?"":v);};
+  // background card -- tap the tenant name to expand occupation/pass/budget/move-in/
+  // days quiet plus the last few inbound WhatsApp snippets and a one tap re-engage link
+  const dtl=el("div","","");
+  dtl.innerHTML=tenantDetail(t);
+  dtl.style.display="none";
+  row.appendChild(dtl);
+  const nmEl=row.querySelector(".nm");
+  if(nmEl){
+    nmEl.title="Tap for background";
+    nmEl.onclick=(e)=>{ e.stopPropagation(); dtl.style.display = dtl.style.display==="none" ? "block":"none"; };
+  }
   return row;
 }
 
+function freshBadge(){
+  const age=Math.round((new Date()-TODAY)/864e5);
+  const cls= age<=1?"g": age===2?"a":"r";
+  return '<div class="kpi"><span class="chip '+cls+'" style="margin:0" title="Data generated '+DATA.generated+'">🕐 data '+(age<=0?"today":age+"d old")+'</span></div>';
+}
+function updateStatusOptions(){
+  const sel=$("#fs"); const cur=sel.value;
+  let opts=[];
+  if(view==="landlords") opts=["Available","Offer pending","Pending","Taken","Off market"];
+  else if(view==="alltenants") opts=["Still looking","Found","Not looking"];
+  else if(view==="sales") opts=["Available","Pending","Closed"];
+  else if(view==="listing") opts=["Available","Offer pending"];
+  sel.innerHTML='<option value="">All statuses</option>'+opts.map(o=>'<option'+(o===cur?" selected":"")+'>'+o+'</option>').join('');
+  sel.style.display = opts.length ? "" : "none";
+}
+function renderChips(){
+  const f=F();
+  const chips=[];
+  const add=(label,onX)=>chips.push({label,onX});
+  if(f.q) add('Search "'+f.q+'"', ()=>{$("#q").value="";});
+  if(f.d) add("District "+f.d, ()=>{$("#fd").value="";});
+  if(f.v) add(f.v==="QUALIFIED"?"Qualified":f.v==="NEEDS_INFO"?"Needs info":"Has conflict", ()=>{$("#fv").value="";});
+  if(f.s) add("Status "+f.s, ()=>{$("#fs").value="";});
+  if(f.r) add("Max rent $"+f.r, ()=>{$("#frbmax").value="";});
+  if(f.hide) add("Hide actioned", ()=>{$("#fh").checked=false;});
+  if(f.bmin!=null||f.bmax!=null) add("Budget "+(f.bmin??"any")+" to "+(f.bmax??"any"), ()=>{$("#fbmin").value="";$("#fbmax").value="";});
+  if(f.gender) add("Gender "+f.gender, ()=>{$("#fgender").value="";});
+  if(f.pax) add("Pax "+f.pax, ()=>{$("#fpax").value="";});
+  if(f.pass) add("Pass "+f.pass, ()=>{$("#fpass").value="";});
+  if(f.ethnat) add("Nationality/ethnicity "+f.ethnat, ()=>{$("#fethnat").value="";});
+  if(f.lease) add("Lease "+leaseLabel(f.lease), ()=>{$("#flease").value="";});
+  if(f.movein) add("Move in "+f.movein, ()=>{$("#fmovein").value="";});
+  if(f.pd && f.pd.length) add("Districts "+f.pd.length+" selected", ()=>{Array.from($("#fpd").options).forEach(o=>o.selected=false);});
+  if(f.fresh) add("Contacted within "+f.fresh+"d", ()=>{$("#ffresh").value="";});
+  if(f.dead) add("Hide cold (30d+)", ()=>{$("#fdead").checked=false;});
+  if(f.ptype) add("Type "+f.ptype, ()=>{$("#fptype").value="";});
+  if(f.rbmin!=null||f.rbmax!=null) add("Rent "+(f.rbmin??"any")+" to "+(f.rbmax??"any"), ()=>{$("#frbmin").value="";$("#frbmax").value="";});
+  if(f.sort && f.sort!=="recency") add("Sort "+f.sort, ()=>{$("#fsort").value="recency";});
+  const box=$("#activeFilters"); box.innerHTML="";
+  box.appendChild(el("span","cnt",VCOUNT+" showing"));
+  chips.forEach(c=>{
+    const chip=el("span","fchip",esc(c.label)+' <b style="opacity:.7">×</b>');
+    chip.onclick=()=>{ c.onX(); saveFilters(); render(); };
+    box.appendChild(chip);
+  });
+}
+function relTime(ageDays){
+  if(ageDays<1) return "less than a day ago";
+  if(ageDays===1) return "1 day ago";
+  return ageDays+" days ago";
+}
+function freshnessBanner(){
+  const ageH=(new Date()-TODAY)/36e5;
+  if(ageH<24) return "";
+  return "⏱ data from "+relTime(Math.floor(ageH/24))+", rebuild to refresh";
+}
+// ---- CRM drawer -------------------------------------------------------------------
+// One component for every record type. Rows carry the subject on data attributes rather
+// than a closure so the button survives render() rebuilding the DOM underneath it.
+let CUR_SUBJ=null;
+function crmBtn(kind,o){
+  const e=CRM.entity(subjOf(kind,o)), st=e&&e.stage&&e.stage!=="new"?STAGE_LABELS[e.stage]:null;
+  const n=e?CRM.notes(subjOf(kind,o)).length:0;
+  return '<button class="btn" data-crm="'+esc(kind)+'" data-crm-id="'+esc(o.id)+'" data-crm-name="'+esc(o.name||"")+
+    '" data-crm-phone="'+esc(o.phone||"")+'">🗂 '+(st?esc(st):"CRM")+(n?" · "+n+"📝":"")+'</button>';
+}
+function openCRM(s){ CUR_SUBJ=s; $("#drawerBg").classList.add("on"); $("#drawer").classList.add("on"); renderDrawer(); }
+function closeCRM(){ CUR_SUBJ=null; $("#drawerBg").classList.remove("on"); $("#drawer").classList.remove("on"); render(); }
+function renderDrawer(){
+  const s=CUR_SUBJ; if(!s) return;
+  const d=$("#drawer"), e=CRM.entity(s)||{}, cur=e.stage||"new";
+  const notes=CRM.notes(s), tasks=CRM.tasks(s), acts=CRM.activity(s);
+  const overdue=t=>!t.done&&t.due&&t.due<todayISO();
+  d.innerHTML=
+    '<div class="dhead"><div><h3>'+esc(dispName(s.name))+'</h3>'+
+      '<div class="sub">'+esc(s.kind)+(s.phone?' · '+esc(s.phone):'')+(CRM.keyOf(s)?'':' · <b>no phone or id — cannot save</b>')+'</div></div>'+
+      '<button class="dclose" id="dClose" title="Close">×</button></div>'+
+    '<div class="dbody">'+
+      '<div class="dsec"><span class="lbl">Stage</span><div class="stagerow">'+
+        STAGE_ORDER.map(k=>'<span class="stagepill'+(k===cur?" on":"")+'" data-stage="'+k+'">'+STAGE_LABELS[k]+'</span>').join("")+
+      '</div></div>'+
+      '<div class="dsec"><span class="lbl">Next action</span>'+
+        '<input id="dAction" placeholder="e.g. confirm Friday viewing slot" value="'+esc(e.next_action||"")+'">'+
+        '<input id="dDue" type="date" value="'+esc(e.next_due||"")+'"></div>'+
+      '<div class="dsec"><span class="lbl">Notes ('+notes.length+')</span>'+
+        '<textarea id="dNote" placeholder="What happened? Saved with today\'s date."></textarea>'+
+        '<button class="btn" id="dAddNote">+ Add note</button>'+
+        notes.map(n=>'<div class="note"><span class="when">'+esc((n.created_at||"").slice(0,10))+'</span>'+
+          esc(n.body)+'<span class="del" data-delnote="'+n.id+'" title="Delete">×</span></div>').join("")+
+      '</div>'+
+      '<div class="dsec"><span class="lbl">Tasks</span>'+
+        '<input id="dTask" placeholder="Task, then Enter">'+
+        '<input id="dTaskDue" type="date">'+
+        (tasks.length?tasks.map(t=>'<label class="tk'+(t.done?" done":"")+'"><input type="checkbox" data-task="'+t.id+'"'+(t.done?" checked":"")+'>'+
+          '<span class="t">'+esc(t.title)+(t.due?'<span class="due'+(overdue(t)?" over":"")+'">due '+esc(t.due)+'</span>':'')+'</span>'+
+          '<span class="del" data-deltask="'+t.id+'">×</span></label>').join(""):'<div class="mut" style="font-size:12px">No tasks yet.</div>')+
+      '</div>'+
+      (acts.length?'<div class="dsec"><span class="lbl">History</span>'+
+        acts.slice(0,20).map(a=>'<div style="font-size:12px;color:var(--mut)">'+esc((a.at||"").slice(0,10))+' — '+esc(a.verb)+(a.detail?': '+esc(a.detail.slice(0,80)):'')+'</div>').join("")+'</div>':'')+
+    '</div>';
+  $("#dClose").onclick=closeCRM;
+  d.querySelectorAll("[data-stage]").forEach(p=>p.onclick=()=>{ CRM.setStage(s,p.dataset.stage); renderDrawer(); CRM.paint(); });
+  const commitPlan=()=>CRM.setPlan(s,$("#dAction").value.trim(),$("#dDue").value||null);
+  $("#dAction").onchange=commitPlan; $("#dDue").onchange=commitPlan;
+  const addNote=()=>{ const v=$("#dNote").value.trim(); if(!v) return; CRM.addNote(s,v); renderDrawer(); CRM.paint(); };
+  $("#dAddNote").onclick=addNote;
+  $("#dNote").onkeydown=ev=>{ if(ev.key==="Enter"&&(ev.metaKey||ev.ctrlKey)){ ev.preventDefault(); addNote(); } };
+  $("#dTask").onkeydown=ev=>{ if(ev.key!=="Enter") return; const v=$("#dTask").value.trim(); if(!v) return;
+    CRM.addTask(s,v,$("#dTaskDue").value||null); renderDrawer(); CRM.paint(); };
+  d.querySelectorAll("[data-task]").forEach(c=>c.onchange=()=>{ CRM.toggleTask(parseInt(c.dataset.task,10)); renderDrawer(); CRM.paint(); });
+  d.querySelectorAll("[data-deltask]").forEach(x=>x.onclick=()=>{ CRM.delTask(parseInt(x.dataset.deltask,10)); renderDrawer(); CRM.paint(); });
+  d.querySelectorAll("[data-delnote]").forEach(x=>x.onclick=()=>{ CRM.delNote(parseInt(x.dataset.delnote,10)); renderDrawer(); CRM.paint(); });
+}
+
+// ---- Pipeline tab -----------------------------------------------------------------
+// Deliberately shows only records Winfred has actually touched. A column per stage over
+// all 216 tenants would be a list of people he has never spoken to, which is the roster
+// he already has two tabs for.
+function renderPipeline(){
+  const box=$("#pipeline"); box.innerHTML="";
+  box.appendChild(el("div","help","📈 <b>Pipeline</b> — every record you have given a stage, a note or a task. "+
+    (CRM.mode==="local"
+      ? "⚠️ No cloud backend is configured, so this is saved on <b>this device only</b> — it will not appear on your phone and it is lost if you clear this browser."
+      : "This is the part of the app that is saved to your CRM database and survives a rebuild; the roster tabs are rebuilt from the rental databases each night.")+
+    " Tap any card to reopen its CRM record."));
+  const f=F();
+  const touched=CRM.all().filter(e=>e.stage&&e.stage!=="new"||e.next_action||CRM.notes(e).length||CRM.tasks(e).length);
+  const openTasks=CRM.tasks().filter(t=>!t.done);
+  const due=openTasks.filter(t=>t.due&&t.due<=todayISO());
+  const plans=CRM.all().filter(e=>e.next_due&&e.next_due<=todayISO());
+
+  if(due.length||plans.length){
+    const b=el("div","");
+    b.appendChild(subHeader("🔔 Due now ("+(due.length+plans.length)+")"));
+    due.forEach(t=>{ const e=t.key?CRM.all().find(x=>x.key===t.key):null;
+      const r=el("div","row","<div class='rtop'><span class='nm'>"+esc(t.title)+"</span><span class='chip r'>due "+esc(t.due)+"</span>"+
+        (e?"<span class='chip'>"+esc(dispName(e.name))+"</span>":"")+"</div>");
+      if(e) r.onclick=()=>openCRM(e); b.appendChild(r); });
+    plans.forEach(e=>{ const r=el("div","row","<div class='rtop'><span class='nm'>"+esc(dispName(e.name))+"</span><span class='chip a'>"+esc(e.next_action||"follow up")+"</span><span class='chip r'>"+esc(e.next_due)+"</span></div>");
+      r.onclick=()=>openCRM(e); b.appendChild(r); });
+    box.appendChild(b);
+  }
+
+  if(!touched.length){
+    box.appendChild(el("div","empty","Nothing in the pipeline yet. Open any tenant, landlord or listing and tap 🗂 CRM to set a stage, leave a note or add a task."));
+    VCOUNT=0; return;
+  }
+  const q=(f.q||"").toLowerCase();
+  const grid=el("div","pipe");
+  STAGE_ORDER.filter(k=>k!=="new").forEach(k=>{
+    let rows=touched.filter(e=>(e.stage||"new")===k);
+    if(q) rows=rows.filter(e=>((e.name||"")+" "+(e.phone||"")+" "+(e.kind||"")).toLowerCase().includes(q));
+    if(!rows.length) return;
+    const col=el("div","pcol","<h4>"+STAGE_LABELS[k]+" ("+rows.length+")</h4>");
+    rows.forEach(e=>{
+      const nt=CRM.notes(e).length, tk=CRM.tasks(e).filter(t=>!t.done).length;
+      const c=el("div","pcard","<b>"+esc(dispName(e.name))+"</b><span class='mut'>"+esc(e.kind||"")+(e.phone?" · "+esc(e.phone):"")+"</span>"+
+        (e.next_action?"<div class='mut' style='margin-top:3px'>→ "+esc(e.next_action)+(e.next_due?" ("+esc(e.next_due)+")":"")+"</div>":"")+
+        (nt||tk?"<div class='mut' style='margin-top:3px'>"+(nt?nt+"📝 ":"")+(tk?tk+"☑":"")+"</div>":""));
+      c.onclick=()=>openCRM(e);
+      col.appendChild(c);
+    });
+    grid.appendChild(col);
+  });
+  box.appendChild(grid.children.length?grid:el("div","empty","No pipeline records match the current search."));
+  VCOUNT=touched.length;
+}
+
 function render(){
+  if(window.renderPresets) window.renderPresets();
   $("#sub").textContent="Priority: availability → location → price → landlord requirements   ·   data "+DATA.generated;
-  $("#kpis").innerHTML='<div class="kpi"><b>'+DATA.listings.length+'</b> available listings</div><div class="kpi"><b>'+DATA.tenants.length+'</b> still looking</div><div class="kpi"><b>'+MATCHES.filter(m=>m.s.verdict==="QUALIFIED").length+'</b> qualified matches</div>';
-  ["work","listing","tenant"].forEach(v=>$("#"+v).style.display = v===view?(v==="work"?"block":"grid"):"none");
+  $("#kpis").innerHTML='<div class="kpi"><b>'+DATA.listings.length+'</b> available listings</div><div class="kpi"><b>'+DATA.tenants.length+'</b> still looking</div><div class="kpi"><b>'+MATCHES.filter(m=>m.s.verdict==="QUALIFIED").length+'</b> qualified matches</div><div class="kpi"><b>'+(DATA.all_landlords||[]).length+'</b> landlords tracked</div><div class="kpi"><b>'+(DATA.all_tenants||[]).length+'</b> tenants tracked</div><div class="kpi"><b>'+(DATA.sales||[]).length+'</b> sale listings</div>'+freshBadge();
+  const fb=freshnessBanner(); const fbEl=$("#freshBanner");
+  if(fbEl){
+    if(fb){ fbEl.textContent=fb; fbEl.className="freshbanner"; fbEl.style.display="block"; }
+    else { fbEl.textContent=""; fbEl.className=""; fbEl.style.display="none"; }
+  }
+  ["work","pipeline","listing","tenant","landlords","alltenants","sales","revival"].forEach(v=>$("#"+v).style.display = v===view?(v==="listing"||v==="tenant"?"grid":"block"):"none");
   document.querySelectorAll("#tabs .tab").forEach(t=>t.classList.toggle("on",t.dataset.v===view));
+  updateStatusOptions();
+  CRM.paint();
   if(view==="work") renderWork();
+  if(view==="pipeline") renderPipeline();
   if(view==="listing") renderListingRail();
   if(view==="tenant") renderTenantRail();
-  $("#legend").innerHTML="Score = budget 30 + location 25 + lease 15 + move-in 15 + freshness 15. ⚑ flags are landlord gates (gender, ethnicity, pax) or budget/lease gaps — a red Conflict still shows so you can judge, it is not auto hidden. "+
-    "WhatsApp opens a pre-filled draft you send yourself (never auto-sent). Cold &gt;5d and stale leads are marked; re-engage only when a unit opened near their area. Mark status is saved on this device only and never edits the databases. PDPA: keep this file private.";
+  if(view==="landlords") renderLandlordsRoster();
+  if(view==="alltenants") renderAllTenantsRoster();
+  if(view==="sales") renderSalesRoster();
+  if(view==="revival") renderRevival();
+  renderChips();
+  $("#legend").innerHTML="Score = budget 30 + location 25 + lease 15 + move-in 15 + freshness 15 (weaker of tenant/landlord contact recency). ⚑ flags are landlord gates (gender, ethnicity, pax) or budget/lease gaps — a red Conflict still shows so you can judge, it is not auto hidden. "+
+    "WhatsApp opens a pre-filled draft you send yourself (never auto-sent). Cold/dead means over 30 days quiet (Winfred's rule); re-engage only when a unit opened near their area. Tap a tenant's name for their background card and chat history. "+
+    (CRM.mode==="local"
+      ? "Stages, notes, tasks and marks are saved on this device only — no cloud backend is configured."
+      : "Stages, notes, tasks and marks sync to your private CRM database and survive a rebuild.")+
+    " Nothing here ever edits the rental databases. PDPA: keep this file private.";
 }
 function helpStrip(){
   const real=new Date(); const age=Math.round((real-TODAY)/864e5);
   const stale = age>2 ? '<div class="wm" style="background:rgba(245,166,35,.16);border-color:rgba(245,166,35,.5)">⚠️ This data is '+age+' days old (from '+DATA.generated+'). Ask Winfred to refresh it so you are working today\'s rooms and tenants.</div>' : '';
-  return el("div","",'<div class="help"><b>How to use:</b> ① Pick the top match &nbsp; ② Read the details and any ⚑ flag &nbsp; ③ Tap <b>WhatsApp</b> to open a ready message — <b>you</b> press send. This tool never messages anyone by itself and never changes your databases.</div>'+stale);
+  return el("div","",'<div class="help"><b>How to use:</b> ① Pick the top match &nbsp; ② Read the details and any ⚑ flag &nbsp; ③ Tap <b>WhatsApp</b> to open a ready message — <b>you</b> press send. Tap a tenant\'s name for their background card. This tool never messages anyone by itself and never changes your databases.</div>'+stale);
+}
+// ---- Today tab helpers -- search/district apply where they make sense, the other
+// match-only filters (verdict/rent/etc.) don't have an obvious meaning against a bare
+// listing or tenant row so they're left to the Listing/Tenant tabs where they belong.
+function passListingSearch(l){
+  const f=F();
+  if(f.d && l.district!==f.d) return false;
+  if(f.q){ const hay=(l.name+" "+l.district+" "+(AREA[l.district]||"")+" "+(l.address||"")+" "+(l.follow_up||"")).toLowerCase(); if(!hay.includes(f.q)) return false; }
+  return true;
+}
+function passTenantSearch(t){
+  const f=F();
+  if(f.d && t.district!==f.d && !(t.preferred_districts||[]).includes(f.d)) return false;
+  if(f.q){ const hay=(t.name+" "+(t.preferred_location||"")+" "+(t.district||"")+" "+(t.phone||"")+" "+(t.occupation||"")).toLowerCase(); if(!hay.includes(f.q)) return false; }
+  return true;
+}
+function oldestFirst(dc){ return dc==null?-1:dc; } // unknown last_contact sinks to the bottom -- can't judge how overdue it really is
+function followUpRows(){
+  return DATA.listings.filter(l=>l.follow_up&&l.follow_up.trim()).filter(passListingSearch)
+    .sort((a,b)=>oldestFirst(days(b.last_contact))-oldestFirst(days(a.last_contact)));
+}
+function viewingNeedsConfirmRows(){
+  return DATA.listings.filter(l=>l.availability==="Available").filter(l=>!viewingLooksLocked(l.viewing)).filter(passListingSearch)
+    .sort((a,b)=>oldestFirst(days(b.last_contact))-oldestFirst(days(a.last_contact)));
+}
+function urgentTenantRows(){
+  return DATA.tenants.filter(t=>{
+    if(moveInBucket(t.move_in)!=="this") return false;
+    const dc=days(t.last_contact);
+    if(dc!=null && dc>30) return false; // cold, excluded everywhere per the 30 day rule
+    if(isContactedToday(t.phone)) return false; // deprioritize anyone already stamped today
+    return (byTenant[t.id]||[]).some(m=>m.s.verdict==="QUALIFIED");
+  }).filter(passTenantSearch)
+    .sort((a,b)=>{ const da=days(a.last_contact), db=days(b.last_contact); return (da==null?1e9:da)-(db==null?1e9:db); });
+}
+function topFreshMatches(n){
+  const seen=new Set(), out=[];
+  const rows=MATCHES.filter(m=>m.s.verdict!=="BLOCKED"&&m.l.availability!=="Offer pending")
+    .filter(m=>m.s.dc==null||m.s.dc<=30)
+    .filter(m=>!isContactedToday(m.t.phone))
+    .filter(passFilter)
+    .sort((a,b)=>b.s.total-a.s.total);
+  for(const m of rows){ if(!seen.has(m.t.id)){ seen.add(m.t.id); out.push(m); if(out.length>=n) break; } }
+  return out;
+}
+function landlordChaseRows(){
+  return DATA.listings.filter(l=>l.last_contact).map(l=>{
+    const dc=days(l.last_contact);
+    const nq=(byListing[l.id]||[]).filter(m=>m.s.verdict==="QUALIFIED"&&(m.s.dc==null||m.s.dc<=30)).length;
+    return {l,dc,nq};
+  }).filter(x=>x.nq>0).filter(x=>passListingSearch(x.l))
+    .sort((a,b)=> b.nq-a.nq || (b.dc-a.dc))
+    .slice(0,8);
+}
+function todayListingRow(l, contextFn){
+  const dc=days(l.last_contact);
+  const row=el("div","row");
+  row.dataset.recid="listing_"+l.id;
+  row.style.cursor="pointer";
+  row.innerHTML=
+    '<div class="rtop"><span class="nm">'+esc(dispName(l.name))+'</span> <span class="mut">'+esc(l.district)+' · '+esc(rentTxt(l))+'</span>'+
+      (isStaleAlarm(l)?'<span class="chip r">⚠ stale, no viewing set</span>':'')+coldChip(dc)+'</div>'+
+    '<div class="gap">'+esc(contextFn(l))+'</div>';
+  row.onclick=()=>{ view="listing"; curL=l.id; render(); };
+  return row;
+}
+// Winfred, 13 Aug 2026: show where they actually want to live, not the district code.
+// District stays as the fallback for the tenants whose location was never captured.
+function tenantWhere(t){
+  const loc=(t.preferred_location||"").trim();
+  const d=(t.district||"").trim();
+  if(!loc) return d || "location not captured";
+  const short = loc.length>44 ? loc.slice(0,44).replace(/[ ,]+$/,'')+"…" : loc;
+  return d ? short+" · "+d : short;      // district stays, but at the end of the line
+}
+function todayTenantRow(t, contextFn){
+  const dc=days(t.last_contact);
+  const row=el("div","row");
+  row.dataset.recid="tenant_"+t.id;
+  row.style.cursor="pointer";
+  row.innerHTML=
+    '<div class="rtop"><span class="nm">'+esc(dispName(t.name))+'</span> <span class="mut">budget '+(t.budget||t.budget_max||'?')+' · '+esc(tenantWhere(t))+'</span>'+coldChip(dc)+contactedChip(t.phone)+'</div>'+
+    '<div class="gap">'+esc(contextFn(t))+'</div>';
+  row.onclick=()=>{ view="tenant"; curT=t.id; render(); };
+  return row;
+}
+function todayLandlordChaseRow(x){
+  const row=el("div","row");
+  row.dataset.recid="listing_"+x.l.id;
+  row.style.cursor="pointer";
+  row.innerHTML=
+    '<div class="rtop"><span class="nm">'+esc(dispName(x.l.name))+'</span> <span class="mut">'+esc(x.l.district)+'</span>'+coldChip(x.dc)+'</div>'+
+    '<div class="gap">quiet '+x.dc+'d · '+x.nq+' waiting tenant'+(x.nq===1?'':'s')+'</div>';
+  row.onclick=()=>{ view="listing"; curL=x.l.id; render(); };
+  return row;
 }
 function renderWork(){
   const box=$("#work"); box.innerHTML="";
   box.appendChild(helpStrip());
-  const seen=new Set();
-  const rows=MATCHES.filter(m=>m.s.verdict!=="BLOCKED"&&m.l.availability!=="Offer pending").filter(passFilter).sort((a,b)=>b.s.total-a.s.total);
-  const prim=[]; for(const m of rows){ if(!seen.has(m.t.id)){seen.add(m.t.id);prim.push(m);} }
-  const list=prim.slice(0,25);
-  box.appendChild(el("div","wm","⭐ Top "+list.length+" to action today — highest fit first, one line per tenant (their best available room). Rooms that are not a fit are hidden here."));
-  if(!list.length){ box.appendChild(el("div","empty","No matches with these filters. Tap ↺ Clear filters.")); return; }
-  list.forEach(m=>box.appendChild(matchRow(m,true)));
+  const fu=followUpRows(), vc=viewingNeedsConfirmRows(), ur=urgentTenantRows(), fresh=topFreshMatches(5), chase=landlordChaseRows();
+  VCOUNT=fu.length+vc.length+ur.length+fresh.length;
+  box.appendChild(el("div","wm","📋 Action queue, in order: owed follow ups → viewings needing confirmation → urgent tenants → top fresh matches → landlords to chase. Empty sections are hidden."));
+  if(!fu.length && !vc.length && !ur.length && !fresh.length && !chase.length){
+    box.appendChild(el("div","empty","Nothing needs action right now with these filters. Tap ↺ Clear all."));
+    return;
+  }
+  if(fu.length){
+    box.appendChild(sectionHeader("Owed follow ups ("+fu.length+")"));
+    fu.forEach(l=>box.appendChild(todayListingRow(l, l2=>"📝 "+(l2.follow_up||"").slice(0,140))));
+  }
+  if(vc.length){
+    box.appendChild(sectionHeader("Viewings needing confirmation ("+vc.length+")"));
+    vc.forEach(l=>box.appendChild(todayListingRow(l, l2=>l2.viewing?("current note: "+l2.viewing.slice(0,120)):"no viewing time on file — lock one in")));
+  }
+  if(ur.length){
+    box.appendChild(sectionHeader("Urgent tenants — move in this month ("+ur.length+")"));
+    // Grouped by region so a viewing run can be planned in one trip rather than reading
+    // 19 scattered rows (Winfred, 13 Aug 2026). Region order is by group size, biggest
+    // first — that is where a single afternoon of viewings pays off most.
+    const groups={};
+    ur.forEach(t=>{ const g=regionOf(t); (groups[g]=groups[g]||[]).push(t); });
+    Object.keys(groups)
+      .sort((a,b)=> groups[b].length-groups[a].length || a.localeCompare(b))
+      .forEach(g=>{
+        box.appendChild(subHeader(g+" ("+groups[g].length+")"));
+        groups[g].forEach(t=>box.appendChild(
+          todayTenantRow(t, t2=>"qualified match ready · move in "+(t2.move_in||"this month"))));
+      });
+  }
+  if(fresh.length){
+    box.appendChild(sectionHeader("Top fresh matches"));
+    fresh.forEach(m=>box.appendChild(matchRow(m,true)));
+  }
+  if(chase.length){
+    box.appendChild(sectionHeader("Landlords to chase ("+chase.length+")"));
+    chase.forEach(x=>box.appendChild(todayLandlordChaseRow(x)));
+  }
 }
+let SORT_BY_COMMISSION=false; // persists across re-renders -- the checkbox itself is
+// recreated every render() (rail.innerHTML is cleared), so its own .checked can't be
+// the source of truth or it resets to unchecked on every redraw.
 function renderListingRail(){
   const rail=$("#lrail"); rail.innerHTML="";
-  const ls=[...DATA.listings].sort((a,b)=> (a.district||"z").localeCompare(b.district||"z") || (a.rent_min||9999)-(b.rent_min||9999));
+  let ls=[...DATA.listings].filter(passListingExtra);
+  VCOUNT=ls.length;
+  if(SORT_BY_COMMISSION) ls.sort((a,b)=>(b.commission_est||0)-(a.commission_est||0));
+  else ls.sort((a,b)=> (a.district||"z").localeCompare(b.district||"z") || (a.rent_min||9999)-(b.rent_min||9999));
+  const totalPipeline = ls.reduce((sum,l)=>sum+(l.commission_est||0),0);
+  rail.appendChild(el("div","mut","💰 Total pipeline est. $"+totalPipeline.toLocaleString()+" across "+ls.length+" listings"));
+  const sortRow=el("label","tog",'<input type="checkbox" id="lsortCommission"'+(SORT_BY_COMMISSION?" checked":"")+'> sort by commission');
+  sortRow.style.marginBottom="6px";
+  rail.appendChild(sortRow);
+  $("#lsortCommission").onchange=(e)=>{ SORT_BY_COMMISSION=e.target.checked; renderListingRail(); };
   ls.forEach(l=>{
     const q=byListing[l.id]||[]; const nq=q.filter(m=>m.s.verdict==="QUALIFIED").length;
     const c=el("div","lc"+(curL===l.id?" on":""));
-    c.innerHTML='<div class="t">'+l.name+' '+(l.availability==="Offer pending"?'<span class="chip a">offer pending</span>':'')+(l.source==="co-broke"?'<span class="chip">co-broke</span>':'')+'</div>'+
+    c.dataset.recid="listing_"+l.id;
+    c.innerHTML='<div class="t">'+dispName(l.name)+' '+(l.availability==="Offer pending"?'<span class="chip a">offer pending</span>':'')+(l.source==="co-broke"?'<span class="chip">co-broke</span>'+ceaChip(l):'')+(isStaleAlarm(l)?'<span class="chip r">⚠ stale, no viewing set</span>':'')+'</div>'+
       '<div class="m">'+l.district+' · '+rentTxt(l)+' · '+(l.address||AREA[l.district]||'')+'</div>'+
-      '<div class="m"><span class="chip g">'+nq+' qualified</span> <span class="chip">'+q.length+' candidates</span></div>';
+      '<div class="m"><span class="chip g">'+nq+' qualified</span> <span class="chip">'+q.length+' candidates</span> '+coldChip(days(l.last_contact))+'</div>';
     c.onclick=()=>{curL=l.id;renderListingRail();renderListingPanel(l);};
     rail.appendChild(c);
   });
@@ -285,33 +1170,39 @@ function renderListingRail(){
 function renderListingPanel(l){
   const p=$("#lpanel"); const g=l.gates;
   const req=[]; if(g.max_pax)req.push("max "+g.max_pax+"pax"); if(g.lease_min)req.push(g.lease_min+"mo min"); if(g.gender!=="any")req.push(g.gender.replace("_"," ")); if(g.ethnicity.rule!=="any"&&g.ethnicity.rule!=="note")req.push(g.ethnicity.rule+" "+g.ethnicity.races.join("/")); if(g.pets)req.push("pets "+g.pets); if(g.smoking)req.push("smoke "+g.smoking); if(g.cooking)req.push("cooking "+g.cooking.slice(0,22));
-  let h='<div class="phead"><div><div class="big">'+l.name+' · '+rentTxt(l)+'</div>'+
-    '<div class="mut">'+l.district+' · '+(l.address||'')+' · '+(l.property_type||'')+'</div>'+
-    '<div class="chips">'+req.map(r=>'<span class="chip">'+r+'</span>').join('')+'</div>'+
+  let h='<div class="phead"><div><div class="big">'+dispName(l.name)+' · '+rentTxt(l)+(l.commission_est?' <span class="chip">💰 ~$'+l.commission_est+' est.</span>':'')+'</div>'+
+    '<div class="mut">'+l.district+' · '+(l.address||'')+' · '+(l.property_type||'')+(SOURCING[l.district]?' · 📈 '+SOURCING[l.district]:'')+'</div>'+
+    '<div class="chips">'+req.map(r=>'<span class="chip">'+r+'</span>').join('')+
+      (g.ethnicity.rule==="note"?'<span class="chip a">⚠ unclear ethnicity req: "'+(g.ethnicity.raw||'')+'"</span>':'')+'</div>'+
     (l.rooms?'<div class="mut" style="margin-top:6px">'+l.rooms+'</div>':'')+
     (l.viewing?'<div class="chips"><span class="chip g">🕐 viewing: '+l.viewing+'</span></div>':'')+
-    (l.availability==="Offer pending"?'<div class="chips"><span class="chip a">⚠ offer pending, hold new offers</span></div>':'')+'</div>'+
+    (l.availability==="Offer pending"?'<div class="chips"><span class="chip a">⚠ offer pending, hold new offers</span></div>':'')+
+    (isStaleAlarm(l)?'<div class="chips"><span class="chip r">⚠ stale, no viewing set</span></div>':'')+'</div>'+
     '<div>'+
     '<a class="btn" target="_blank" href="'+mapLink(l)+'">📍 Map</a> '+
-    (l.phone?'<a class="btn w" target="_blank" href="'+waPlain(l.phone,"Hi "+fname(l.name)+", checking on the room at "+(l.address||AREA[l.district]||"your unit")+", is it still available and when can tenants view")+'">WhatsApp landlord</a> <a class="btn" href="tel:'+l.phone+'">Call landlord</a>':'')+
+    (l.phone?'<a class="btn w" data-wa-phone="'+esc(l.phone)+'" target="_blank" href="'+waPlain(l.phone,greet(l.name)+"checking on the room at "+(l.address||AREA[l.district]||"your unit")+", is it still available and when can tenants view")+'">WhatsApp landlord</a> <a class="btn" href="tel:'+l.phone+'">Call landlord</a>':'')+
     '</div></div>';
   p.innerHTML=h;
   const q=(byListing[l.id]||[]).filter(passFilterListing);
+  VCOUNT=q.length;
   if(!q.length){ p.appendChild(el("div","empty","No tenants match the current filters for this listing.")); return; }
   q.slice(0,60).forEach(m=>p.appendChild(matchRow(m,false)));
 }
-function passFilterListing(m){ const f=F(); if(f.v&&m.s.verdict!==f.v)return false; if(f.cold&&m.s.dc!=null&&m.s.dc>5)return false; if(f.hide&&getS(m.l.id,m.t.id))return false; if(f.q){const hay=(m.t.name+" "+m.t.preferred_location+" "+m.t.district).toLowerCase();if(!hay.includes(f.q))return false;} return true; }
+function passFilterListing(m){ const f=F(); if(f.v&&m.s.verdict!==f.v)return false; if(f.hide&&getS(m.l.id,m.t.id))return false; if(!passTenantExtra(m.t,m.s.dc))return false; if(f.q){const hay=(m.t.name+" "+m.t.preferred_location+" "+m.t.district+" "+(m.t.phone||"")+" "+(m.t.occupation||"")+" "+(m.t.listing_enquired||"")).toLowerCase();if(!hay.includes(f.q))return false;} return true; }
 function renderTenantRail(){
   const rail=$("#trail"); rail.innerHTML="";
   const f=F();
   let ts=[...DATA.tenants];
-  if(f.q) ts=ts.filter(t=>(t.name+" "+t.preferred_location+" "+t.district+" "+(t.nationality||"")+" "+(t.phone||"")).toLowerCase().includes(f.q));
-  ts.sort((a,b)=> (byTenant[b.id]?.[0]?.s.total||0)-(byTenant[a.id]?.[0]?.s.total||0));
+  if(f.q) ts=ts.filter(t=>(t.name+" "+t.preferred_location+" "+t.district+" "+(t.nationality||"")+" "+(t.phone||"")+" "+(t.occupation||"")+" "+(t.listing_enquired||"")).toLowerCase().includes(f.q));
+  ts=ts.filter(t=>passTenantExtra(t,days(t.last_contact)));
+  VCOUNT=ts.length;
+  ts.sort(tenantSortComparator(f.sort||"recency"));
   ts.slice(0,120).forEach(t=>{
     const best=byTenant[t.id]?.[0]; const nq=(byTenant[t.id]||[]).filter(m=>m.s.verdict==="QUALIFIED").length;
     const c=el("div","lc"+(curT===t.id?" on":""));
-    c.innerHTML='<div class="t">'+t.name+'</div><div class="m">'+(t.district||t.preferred_location||'?')+' · budget '+(t.budget||t.budget_max||'?')+' · '+(t.pax||'?')+'pax</div>'+
-      '<div class="m"><span class="chip g">'+nq+' fit</span> '+(best?'<span class="chip">top '+best.s.total+'</span>':'')+'</div>';
+    c.dataset.recid="tenant_"+t.id;
+    c.innerHTML='<div class="t">'+dispName(t.name)+'</div><div class="m">'+(t.district||t.preferred_location||'?')+' · budget '+(t.budget||t.budget_max||'?')+' · '+(t.pax||'?')+'pax</div>'+
+      '<div class="m"><span class="chip g">'+nq+' fit</span> '+(best?'<span class="chip">top '+best.s.total+'</span>':'')+' '+coldChip(days(t.last_contact))+contactedChip(t.phone)+'</div>';
     c.onclick=()=>{curT=t.id;renderTenantRail();renderTenantPanel(t);};
     rail.appendChild(c);
   });
@@ -319,20 +1210,492 @@ function renderTenantRail(){
 }
 function renderTenantPanel(t){
   const p=$("#tpanel");
-  p.innerHTML='<div class="phead"><div><div class="big">'+t.name+'</div><div class="mut">'+(t.preferred_location||t.district||'')+' · budget '+(t.budget||t.budget_max||'?')+' · '+(t.pax||'?')+'pax · lease '+(t.lease_months||'?')+'mo · move '+(t.move_in||'?')+'</div>'+
-    '<div class="chips"><span class="chip">'+(t.gender||'?')+'</span><span class="chip">'+(t.ethnicity||'?')+'</span><span class="chip">'+(t.nationality||'?')+'</span><span class="chip">'+(t.pass_type||'?')+'</span><span class="chip">'+(t.occupation||'?')+'</span>'+coldChip(days(t.last_contact))+'</div></div></div>';
-  const q=(byTenant[t.id]||[]).filter(m=>{const f=F();if(f.v&&m.s.verdict!==f.v)return false;if(f.d&&m.l.district!==f.d)return false;if(f.r&&m.l.rent_min&&m.l.rent_min>f.r)return false;return true;});
+  p.innerHTML='<div class="phead"><div><div class="big">'+dispName(t.name)+'</div><div class="mut">'+(t.preferred_location||t.district||'')+' · budget '+(t.budget||t.budget_max||'?')+' · '+(t.pax||'?')+'pax · lease '+(t.lease_months||'?')+'mo · move '+(t.move_in||'?')+'</div>'+
+    '<div class="chips"><span class="chip">'+(t.gender||'?')+'</span><span class="chip">'+(t.ethnicity||'?')+'</span><span class="chip">'+(t.nationality||'?')+'</span><span class="chip">'+(t.pass_type||'?')+'</span><span class="chip">'+(t.occupation||'?')+'</span>'+coldChip(days(t.last_contact))+'</div></div></div>'+
+    tenantDetail(t);
+  const q=(byTenant[t.id]||[]).filter(m=>{const f=F();if(f.v&&m.s.verdict!==f.v)return false;if(f.d&&m.l.district!==f.d)return false;if(f.r&&m.l.rent_min&&m.l.rent_min>f.r)return false;if(!passListingExtra(m.l))return false;return true;});
+  VCOUNT=q.length;
   if(!q.length){ p.appendChild(el("div","empty","No available listing fits this tenant right now.")); return; }
   q.forEach(m=>p.appendChild(matchRow(m,true)));
 }
 
+function statusChip(av){
+  if(av==="Available") return '<span class="chip g">🟢 Available</span>';
+  if(av==="Offer pending") return '<span class="chip a">🟡 Offer pending</span>';
+  if(av==="Pending") return '<span class="chip">⚪ Pending intake</span>';
+  if(av==="Taken") return '<span class="chip r">🔴 Taken</span>';
+  return '<span class="chip mut">⚫ Off market</span>';
+}
+// Takes the landlord record, not a bare id: the CRM keys a person by phone when there is
+// one, so it needs the phone to find the same record the roster and the drawer share.
+const isFlagged=l=>CRM.flagged(subjOf("landlord",l));
+const toggleFlag=l=>{ CRM.toggleFlag(subjOf("landlord",l)); render(); };
+function dupBanner(){
+  const dups=DATA.duplicate_phones||[];
+  if(!dups.length) return "";
+  return '<div class="wm">⚠ '+dups.length+' phone number(s) shared across more than one record — likely a duplicate or data-entry collision: '+
+    dups.map(d=>d.owners.join(" = ")).join(" · ")+'</div>';
+}
+function copyBtn(label, rows, box){
+  const b=el("button","btn",label);
+  b.onclick=()=>{ navigator.clipboard.writeText(rows.join("\n")); b.textContent="Copied ✓ ("+rows.length+")"; setTimeout(()=>b.textContent=label,2000); };
+  box.appendChild(b);
+  return b;
+}
+function renderLandlordsRoster(){
+  const box=$("#landlords"); box.innerHTML="";
+  box.appendChild(el("div","help","🏢 <b>Full landlord roster</b> — every landlord in the database, every status. This is the same data Claude Code reads; nothing here is filtered for matching. Sorted live → pending → closed. 🚩 Flag wrong lets you mark a status you know is stale so you can send the corrections back."));
+  box.innerHTML+=dupBanner();
+  const f=F();
+  let ls=(DATA.all_landlords||[]).slice();
+  if(f.d) ls=ls.filter(l=>l.primary_district===f.d);
+  if(f.s) ls=ls.filter(l=>l.availability===f.s);
+  if(f.q){ const hay=l=>(l.name+" "+l.district+" "+(AREA[l.district]||"")+" "+l.address+" "+(l.phone||"")).toLowerCase(); ls=ls.filter(l=>hay(l).includes(f.q)); }
+  VCOUNT=ls.length;
+
+  const actionsRow=el("div","","");
+  actionsRow.style.cssText="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px";
+  const stale=ls.filter(l=>l.availability==="Available"&&l.phone&&(days(l.last_contact)==null||days(l.last_contact)>14));
+  copyBtn("📋 Copy stale check-in list ("+stale.length+")",
+    stale.map(l=>dispName(l.name)+" | "+l.phone+" | "+waPlain(l.phone,greet(l.name)+"just checking — is the room at "+(l.address||AREA[l.district]||"your unit")+" still available?")),
+    actionsRow);
+  const flagged=ls.filter(l=>isFlagged(l));
+  copyBtn("🚩 Copy flagged list ("+flagged.length+")", flagged.map(l=>l.id+" "+dispName(l.name)+" — marked wrong by Winfred"), actionsRow);
+  box.appendChild(actionsRow);
+
+  if(!ls.length){ box.appendChild(el("div","empty","No landlords match the current search/district/status filter.")); return; }
+  ls.forEach(l=>{
+    const dc=days(l.last_contact), flagged=isFlagged(l);
+    const row=el("div","row"+(flagged?" done":""));
+    row.dataset.recid="alllandlord_"+l.id;
+    row.innerHTML=
+      '<div class="rtop"><span class="nm" style="cursor:default">'+dispName(l.name)+'</span> '+statusChip(l.availability)+
+        (l.source==="co-broke"?'<span class="chip">co-broke</span>'+ceaChip(l):'')+
+        (l.handed_off?'<span class="chip a">🤝 handed off</span>':'')+
+        (l.commission_est?'<span class="chip">💰 ~$'+l.commission_est+' est.</span>':'')+
+        (DUP_PHONES.has(l.phone)?'<span class="chip a">⚠ duplicate phone on file</span>':'')+
+        (flagged?'<span class="chip r">🚩 flagged wrong</span>':'')+ contactedChip(l.phone)+
+      '</div>'+
+      '<div class="rtop" style="margin-top:5px">'+
+        '<span class="chip">'+(l.district||'?')+(l.property_type?' · '+l.property_type:'')+'</span>'+
+        '<span class="chip">'+rentTxt(l)+'</span>'+
+        (l.address?'<span class="chip">'+l.address.slice(0,40)+'</span>':'')+
+        (dc!=null?coldChip(dc):'<span class="chip">no contact date</span>')+
+      '</div>'+
+      (l.rooms?'<div class="gap">'+l.rooms.slice(0,140)+'</div>':'')+
+      (l.follow_up?'<div class="gap">📝 '+l.follow_up.slice(0,160)+'</div>':'')+
+      // Taken/Off market landlords get no contact action — some of these are explicit
+      // "never re-engage" drops (declined clients, handed to co-broke). Map is harmless
+      // and stays; WhatsApp/Call are the outbound-contact risk, so they're suppressed here.
+      (l.availability==="Taken"||l.availability==="Off market"
+        ? '<div class="acts"><a class="btn" target="_blank" href="'+mapLink(l)+'">📍 Map</a> <span class="btn mut">'+l.availability.toLowerCase()+' — no contact action</span><button class="btn" data-flag="1">'+(flagged?"↺ Unflag":"🚩 Flag wrong")+'</button>'+crmBtn("landlord",l)+'</div>'
+        : '<div class="acts">'+
+            '<a class="btn" target="_blank" href="'+mapLink(l)+'">📍 Map</a>'+
+            (l.phone?' <a class="btn w" data-wa-phone="'+esc(l.phone)+'" target="_blank" href="'+waPlain(l.phone,greet(l.name)+"checking on the room at "+(l.address||AREA[l.district]||"your unit")+", is it still available")+'">WhatsApp landlord</a> <a class="btn" href="tel:'+l.phone+'">Call</a>':' <span class="btn mut">no phone on file</span>')+
+            '<button class="btn" data-flag="1">'+(flagged?"↺ Unflag":"🚩 Flag wrong")+'</button>'+crmBtn("landlord",l)+''+
+          '</div>');
+    row.querySelector("[data-flag]").onclick=()=>toggleFlag(l);
+    box.appendChild(row);
+  });
+}
+// Region for grouping. Prefers the tenant's own district, then their preferred districts,
+// so someone with no district but "West (Jurong)" written down still lands in West.
+const _D2R={}; Object.keys(REGION_DISTRICTS).forEach(r=>REGION_DISTRICTS[r].forEach(d=>_D2R[d]=r));
+function regionOf(t){
+  const cands=[t.district, ...(t.preferred_districts||[])].filter(Boolean);
+  for(const d of cands){ if(_D2R[d]) return _D2R[d]; }
+  const loc=(t.preferred_location||"").trim().toLowerCase();
+  if(!loc) return "Location not captured";
+  if(/\b(east|bedok|tampines|changi|katong|siglap|simei|pasir ris|eunos|marine parade)\b/.test(loc)) return "East";
+  if(/\b(west|jurong|clementi|boon lay|pioneer|bukit batok|lakeside|choa chu kang|tengah)\b/.test(loc)) return "West";
+  if(/\b(north|woodlands|yishun|sembawang|admiralty|khatib|canberra)\b/.test(loc)) return "North";
+  if(/\b(serangoon|hougang|punggol|sengkang|kovan|ang mo kio|amk|lew lian|cherryhill|nex)\b/.test(loc)) return "Northeast";
+  if(/\b(town|orchard|central|novena|newton|river valley|tanjong pagar|smu|cbd|bugis|lavender|farrer park)\b/.test(loc)) return "Central";
+  // last resort: match the free text against the district area names the app already
+  // carries, so a place name we never hardcoded still lands in the right region
+  for(const d in AREA){
+    if(!_D2R[d]) continue;
+    // AREA values look like "Tampines, Pasir Ris, Simei" — match any one place name,
+    // not the whole string, or this never fires.
+    for(const part of String(AREA[d]||"").toLowerCase().split(/[,/]/)){
+      const a=part.trim();
+      if(a.length>3 && loc.includes(a)) return _D2R[d];
+    }
+  }
+  // they DID tell us where they want to live, we just cannot place it — that is a very
+  // different problem from having no location at all, so never merge the two buckets.
+  return "Other areas";
+}
+function subHeader(text){
+  const h=el("div","","<b>"+esc(text)+"</b>");
+  h.style.cssText="margin:10px 0 4px;font-size:12px;color:var(--ink);opacity:.75;letter-spacing:.2px";
+  return h;
+}
+function sectionHeader(text){
+  const h=el("div","","<b>"+text+"</b>");
+  h.style.cssText="margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--line);color:var(--mut);font-size:13px;letter-spacing:.3px;text-transform:uppercase";
+  return h;
+}
+function tenantRow(t){
+  const dc=days(t.last_contact);
+  const LOOK_CHIP={"Still looking":'<span class="chip g">🟢 Still looking</span>',"Found":'<span class="chip">✅ Found a place</span>',"Not looking":'<span class="chip mut">⚫ Not looking</span>'};
+  const row=el("div","row");
+  row.dataset.recid="alltenant_"+t.id;
+  row.innerHTML=
+    '<div class="rtop"><span class="nm" style="cursor:default">'+dispName(t.name)+'</span> '+(LOOK_CHIP[t.looking]||"")+
+      (t.missing.length?'<span class="chip a">⚠ missing '+t.missing.join("/")+'</span>':'')+
+      (DUP_PHONES.has(t.phone)?'<span class="chip a">⚠ duplicate phone on file</span>':'')+ contactedChip(t.phone)+
+    '</div>'+
+    '<div class="rtop" style="margin-top:5px">'+
+      '<span class="chip">'+(t.district||t.preferred_location||'?')+'</span>'+
+      '<span class="chip">budget '+(t.budget||'?')+'</span>'+
+      '<span class="chip">'+(t.pax||'?')+'pax</span>'+
+      '<span class="chip">move '+(t.move_in||'?')+'</span>'+
+      (dc!=null?coldChip(dc):'<span class="chip">no contact date</span>')+
+    '</div>'+
+    (t.listing_enquired?'<div class="gap">enquired: '+t.listing_enquired+'</div>':'')+
+    (t.looking==="Still looking"
+      ? '<div class="acts"><a class="btn" target="_blank" href="'+mapLink({map_query:(t.district?AREA[t.district]:t.preferred_location)})+'">📍 Area</a>'+
+          (t.phone?' <a class="btn w" data-wa-phone="'+esc(t.phone)+'" target="_blank" href="'+waPlain(t.phone,greet(t.name)+"checking in on your room search — still looking? Let me know your latest budget/move-in date and I will send matches.")+'">WhatsApp</a> <a class="btn" href="tel:'+t.phone+'">Call</a>':' <span class="btn mut">no phone on file</span>')+
+          crmBtn("tenant",t)+
+        '</div>'
+      : '<div class="acts"><span class="btn mut">'+t.looking.toLowerCase()+' — no contact action</span>'+crmBtn("tenant",t)+'</div>');
+  return row;
+}
+function renderAllTenantsRoster(){
+  const box=$("#alltenants"); box.innerHTML="";
+  box.appendChild(el("div","help","🙋‍♀️ <b>Full tenant roster</b>, grouped by area — every tenant, every status (still looking, found a place, no longer looking). Tenants with no usable location on file sit in their own group at the end rather than being mixed in. Missing key info (budget, move-in, pax, lease) is flagged inline."));
+  const f=F();
+  let ts=(DATA.all_tenants||[]).slice();
+  if(f.d) ts=ts.filter(t=>t.primary_district===f.d);
+  if(f.s) ts=ts.filter(t=>t.looking===f.s);
+  if(f.q){ const hay=t=>(t.name+" "+t.district+" "+(t.preferred_location||"")+" "+(t.phone||"")).toLowerCase(); ts=ts.filter(t=>hay(t).includes(f.q)); }
+  VCOUNT=ts.length;
+  if(!ts.length){ box.appendChild(el("div","empty","No tenants match the current search/district/status filter.")); return; }
+  const cmp=tenantSortComparator(f.sort||"recency");
+  ts.sort((a,b)=> (a.primary_district||"zzz").localeCompare(b.primary_district||"zzz") || cmp(a,b));
+  let shown=0, groupsShown=0;
+  let curGroup=null;
+  for(const t of ts){
+    if(shown>=400) break;
+    const g=t.primary_district||"";
+    if(g!==curGroup){
+      curGroup=g; groupsShown++;
+      const label = g ? (g+" — "+(AREA[g]||"")) : "Unspecified location";
+      const n=ts.filter(x=>(x.primary_district||"")===g).length;
+      box.appendChild(sectionHeader(label+" ("+n+")"));
+    }
+    box.appendChild(tenantRow(t));
+    shown++;
+  }
+  if(ts.length>400) box.appendChild(el("div","mut","showing first 400 of "+ts.length+" — narrow with search/district/status filters"));
+}
+function saleStatusChip(st){
+  if(st==="Available") return '<span class="chip g">🟢 Available</span>';
+  if(st==="Pending") return '<span class="chip a">⚪ Pending</span>';
+  return '<span class="chip mut">⚫ Closed</span>';
+}
+function renderSalesRoster(){
+  const box=$("#sales"); box.innerHTML="";
+  box.appendChild(el("div","help","🏡 <b>Sale listings</b> — landlord contacts flagged as a sale deal (not a rental), separate track. Small list by design: most of Winfred's book is rentals. Asking price is parsed best-effort from freeform notes ($505k etc.) — the raw note is always shown too since parsing SG price shorthand isn't perfect."));
+  const f=F();
+  let ss=(DATA.sales||[]).slice();
+  if(f.d) ss=ss.filter(s=>s.primary_district===f.d);
+  if(f.s) ss=ss.filter(s=>s.sale_status===f.s);
+  if(f.q){ const hay=s=>(s.name+" "+s.district+" "+s.address+" "+(s.phone||"")).toLowerCase(); ss=ss.filter(s=>hay(s).includes(f.q)); }
+  VCOUNT=ss.length;
+  if(!ss.length){ box.appendChild(el("div","empty","No sale listings match the current filters.")); return; }
+  ss.forEach(s=>{
+    const dc=days(s.last_contact);
+    const row=el("div","row");
+    row.dataset.recid="sale_"+s.id;
+    row.innerHTML=
+      '<div class="rtop"><span class="nm" style="cursor:default">'+dispName(s.name)+'</span> '+saleStatusChip(s.sale_status)+
+        (s.source==="co-broke"?'<span class="chip">co-broke</span>'+ceaChip(s):'')+ contactedChip(s.phone)+
+      '</div>'+
+      '<div class="rtop" style="margin-top:5px">'+
+        '<span class="chip">'+(s.district||'?')+(s.property_type?' · '+s.property_type:'')+'</span>'+
+        '<span class="chip">'+(s.asking_price?'asking ~$'+s.asking_price.toLocaleString():'price TBC')+'</span>'+
+        (s.address?'<span class="chip">'+s.address.slice(0,40)+'</span>':'')+
+        (dc!=null?coldChip(dc):'<span class="chip">no contact date</span>')+
+      '</div>'+
+      (s.price_text?'<div class="gap">'+s.price_text.slice(0,140)+'</div>':'')+
+      (s.follow_up?'<div class="gap">📝 '+s.follow_up.slice(0,160)+'</div>':'')+
+      (s.sale_status==="Closed"
+        ? '<div class="acts"><a class="btn" target="_blank" href="'+mapLink(s)+'">📍 Map</a> <span class="btn mut">closed — no contact action</span>'+crmBtn("sale",s)+'</div>'
+        : '<div class="acts">'+
+            '<a class="btn" target="_blank" href="'+mapLink(s)+'">📍 Map</a>'+
+            (s.phone?' <a class="btn w" data-wa-phone="'+esc(s.phone)+'" target="_blank" href="'+waPlain(s.phone,greet(s.name)+"checking in on the sale at "+(s.address||AREA[s.district]||"your unit")+" — still on the market?")+'">WhatsApp</a> <a class="btn" href="tel:'+s.phone+'">Call</a>':' <span class="btn mut">no phone on file</span>')+
+            crmBtn("sale",s)+
+          '</div>');
+    box.appendChild(row);
+  });
+}
+
+// ---- revival tab -- reuses revival_board.py's own scan (exported into DATA.revival by
+// export_data.py, not recomputed here). It has no EV/priority score of its own, only a
+// good/weak/none match tier, so rows are ranked by tier then by days quiet, not by EV.
+function revivalTierChip(t){
+  if(t==="good") return '<span class="chip g">✅ good match waiting</span>';
+  if(t==="weak") return '<span class="chip a">❓ weak match</span>';
+  return '<span class="chip mut">— no match right now</span>';
+}
+function revivalDraft(r){
+  if(r.match) return greet(r.name)+"following up on your room search — a unit just opened in "+(r.match.district||"the area")+". Viewing this "+fixedTimeOptions()+", can you make it";
+  return greet(r.name)+"checking in — are you still looking for a room? Let me know your latest budget and move in date and I will send you options";
+}
+function renderRevival(){
+  const box=$("#revival"); box.innerHTML="";
+  box.appendChild(el("div","help","♻️ <b>Revival board</b> — still looking tenants past the 30 day lead cutoff, cross checked against currently available listings. This reuses revival_board.py's own matching (not the main score() engine above), so there is no 0 to 100 score here, only a good/weak/none match tier. Review list only, nothing sends itself."));
+  const rows=DATA.revival||[];
+  if(!rows.length){ box.appendChild(el("div","empty","No revival candidates — every still looking tenant is within the 30 day cutoff.")); return; }
+  const f=F();
+  let rs=rows.slice();
+  if(f.q){ const hay=r=>(r.name+" "+(r.district||"")+" "+(r.phone||"")).toLowerCase(); rs=rs.filter(r=>hay(r).includes(f.q)); }
+  if(f.d) rs=rs.filter(r=>r.district===f.d);
+  VCOUNT=rs.length;
+  if(!rs.length){ box.appendChild(el("div","empty","No revival candidates match the current search/district filter.")); return; }
+  rs.forEach((r,i)=>{
+    const snippet = r.match
+      ? "best option: "+(isRealName(r.match.name)?r.match.name:(r.match.id||"(no name saved)"))+" · "+(r.match.district||"")+" · "+(r.match.rent_min||r.match.rent_max?("$"+(r.match.rent_min||r.match.rent_max)):"rent TBC")
+      : "no current listing fits";
+    const row=el("div","row");
+    row.dataset.recid="revival_"+i;
+    row.innerHTML=
+      '<div class="rtop"><span class="sc" style="min-width:24px">#'+(i+1)+'</span><span class="nm" style="cursor:default">'+esc(dispName(r.name))+'</span>'+revivalTierChip(r.tier)+contactedChip(r.phone)+'</div>'+
+      '<div class="rtop" style="margin-top:5px">'+
+        '<span class="chip">'+esc(r.district||'?')+'</span>'+
+        '<span class="chip">budget '+(r.budget||'?')+'</span>'+
+        '<span class="chip">'+(r.pax||'?')+'pax</span>'+
+        (r.days_quiet!=null?'<span class="chip r">quiet '+r.days_quiet+'d</span>':'<span class="chip">no contact date</span>')+
+      '</div>'+
+      '<div class="gap">'+esc(snippet)+'</div>'+
+      (r.phone
+        ? '<div class="acts"><a class="btn w" data-wa-phone="'+esc(r.phone)+'" target="_blank" href="'+waPlain(r.phone,revivalDraft(r))+'">WhatsApp</a> <a class="btn" href="tel:'+esc(r.phone)+'">Call</a></div>'
+        : '<div class="acts"><span class="btn mut">no phone on file</span></div>');
+    box.appendChild(row);
+  });
+}
+
+// ---- theme (sun/moon toggle, persisted) ----
+function applyTheme(t){
+  if(t) document.documentElement.setAttribute("data-theme",t);
+  else document.documentElement.removeAttribute("data-theme");
+  const btn=$("#themeToggle");
+  if(btn){
+    const systemLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
+    const effective = t || (systemLight ? "light":"dark");
+    btn.textContent = effective==="light" ? "☀️" : "🌙";
+    btn.title = effective==="light" ? "Switch to dark mode" : "Switch to light mode";
+  }
+}
+let THEME = localStorage.getItem("cbk_theme") || "";
+applyTheme(THEME);
+
+// ---- filter persistence ----
+const FILTER_IDS=["q","fd","fv","fs","fr","fh","fbmin","fbmax","fgender","fpax","fpass","fethnat","flease","fmovein","ffresh","fdead","fptype","frbmin","frbmax","fsort"];
+function saveFilters(){
+  const state={};
+  FILTER_IDS.forEach(id=>{ const e=$("#"+id); if(!e) return; state[id] = e.type==="checkbox" ? e.checked : e.value; });
+  const pdEl=$("#fpd"); if(pdEl) state.fpd=Array.from(pdEl.selectedOptions).map(o=>o.value);
+  localStorage.setItem("cbk_filters_v1", JSON.stringify(state));
+}
+function loadFilters(){
+  let state=null;
+  try{ state=JSON.parse(localStorage.getItem("cbk_filters_v1")||"null"); }catch(e){}
+  if(!state) return;
+  FILTER_IDS.forEach(id=>{ const e=$("#"+id); if(!e || !(id in state)) return; if(e.type==="checkbox") e.checked=!!state[id]; else e.value=state[id]; });
+  // migrate phase 1's two redundant cold controls into the single merged "hide cold
+  // (30d+)" toggle -- if either the old top bar "hide cold >30d" (fc) or the "more
+  // filters" "hide dead leads" (fdead) was ever turned on for this browser, keep it on.
+  if(state.fc===true){ const fd=$("#fdead"); if(fd) fd.checked=true; }
+  const pdEl=$("#fpd");
+  if(pdEl && state.fpd){ const set=new Set(state.fpd); Array.from(pdEl.options).forEach(o=>o.selected=set.has(o.value)); }
+}
+
 // init
 (function(){
-  const ds=[...new Set(DATA.listings.map(l=>l.district).filter(Boolean))].sort();
+  const ds=[...new Set([
+    ...DATA.listings.map(l=>l.district),
+    ...(DATA.all_landlords||[]).map(l=>l.primary_district),
+    ...(DATA.all_tenants||[]).map(t=>t.primary_district),
+    ...(DATA.sales||[]).map(s=>s.primary_district),
+  ].filter(Boolean))].sort();
   ds.forEach(d=>{const o=el("option");o.value=d;o.textContent=d+" "+(AREA[d]||"");$("#fd").appendChild(o);});
+
+  const ptypes=[...new Set(DATA.listings.map(l=>l.property_type).filter(Boolean))].sort();
+  ptypes.forEach(p=>{const o=el("option");o.value=p;o.textContent=p;$("#fptype").appendChild(o);});
+
+  (function(){
+    const seen=new Map();
+    DATA.tenants.forEach(t=>{const v=(t.pass_type||"").trim(); if(!v) return; const k=v.toLowerCase(); if(!seen.has(k)) seen.set(k,v);});
+    Array.from(seen.values()).sort((a,b)=>a.localeCompare(b)).forEach(v=>{const o=el("option");o.value=v;o.textContent=v;$("#fpass").appendChild(o);});
+  })();
+
+  (function(){
+    const seen=new Map();
+    DATA.tenants.forEach(t=>{ [t.nationality,t.ethnicity].forEach(v=>{ v=(v||"").trim(); if(!v) return; const k=v.toLowerCase(); if(!seen.has(k)) seen.set(k,v); }); });
+    Array.from(seen.values()).sort((a,b)=>a.localeCompare(b)).forEach(v=>{const o=el("option");o.value=v;o.textContent=v;$("#fethnat").appendChild(o);});
+  })();
+
+  (function(){
+    const sel=$("#fpd");
+    Object.keys(REGION_DISTRICTS).forEach(region=>{
+      const og=document.createElement("optgroup"); og.label=region;
+      REGION_DISTRICTS[region].forEach(d=>{
+        const o=document.createElement("option"); o.value=d; o.textContent=d+(AREA[d]?(" "+AREA[d]):"");
+        og.appendChild(o);
+      });
+      sel.appendChild(og);
+    });
+  })();
+
+  loadFilters();
+
   document.querySelectorAll("#tabs .tab").forEach(t=>t.onclick=()=>{view=t.dataset.v;render();});
-  ["q","fd","fv","fr","fc","fh"].forEach(id=>$("#"+id).addEventListener("input",render));
-  $("#clr").onclick=()=>{["q","fr","fd","fv"].forEach(id=>$("#"+id).value="");$("#fc").checked=false;$("#fh").checked=false;render();};
+  FILTER_IDS.forEach(id=>{ const e=$("#"+id); if(!e) return; const ev=(e.tagName==="SELECT"||e.type==="checkbox")?"change":"input"; e.addEventListener(ev,()=>{ACTIVE_PRESET=null;saveFilters();render();}); });
+  const pdEl=$("#fpd"); if(pdEl) pdEl.addEventListener("change",()=>{ACTIVE_PRESET=null;saveFilters();render();});
+
+  // ---- phone paste lookup -- 8+ digits typed/pasted into search normalizes and jumps
+  // to the matching person across every roster (tenants, listings, all_tenants,
+  // all_landlords, sales), on top of the normal text search which already runs via the
+  // generic FILTER_IDS listener above.
+  function phoneDigitsOf(s){ return String(s||"").replace(/\D/g,""); }
+  function phoneMatchesQuery(recPhone,qDigits){
+    const rd=phoneDigitsOf(recPhone); if(!rd||qDigits.length<8) return false;
+    return rd.slice(-8)===qDigits.slice(-8);
+  }
+  function findPhoneMatch(qDigits){
+    for(const t of DATA.tenants) if(phoneMatchesQuery(t.phone,qDigits)) return {kind:"tenant",id:t.id};
+    for(const l of DATA.listings) if(phoneMatchesQuery(l.phone,qDigits)) return {kind:"listing",id:l.id};
+    for(const t of (DATA.all_tenants||[])) if(phoneMatchesQuery(t.phone,qDigits)) return {kind:"alltenant",id:t.id};
+    for(const l of (DATA.all_landlords||[])) if(phoneMatchesQuery(l.phone,qDigits)) return {kind:"landlord",id:l.id};
+    for(const s of (DATA.sales||[])) if(phoneMatchesQuery(s.phone,qDigits)) return {kind:"sale",id:s.id};
+    return null;
+  }
+  function jumpHighlight(recid){
+    // setTimeout rather than requestAnimationFrame -- rAF only fires while the tab is
+    // actively compositing, which a background tab or an automated check may never do.
+    setTimeout(()=>{
+      const node=document.querySelector('[data-recid="'+CSS.escape(recid)+'"]');
+      if(!node) return;
+      node.scrollIntoView({block:"center",behavior:"smooth"});
+      node.classList.add("jump-hl");
+      setTimeout(()=>node.classList.remove("jump-hl"),2500);
+    },0);
+  }
+  function jumpToPhoneMatch(m){
+    if(m.kind==="tenant"){ view="tenant"; curT=m.id; render(); jumpHighlight("tenant_"+m.id); }
+    else if(m.kind==="listing"){ view="listing"; curL=m.id; render(); jumpHighlight("listing_"+m.id); }
+    else if(m.kind==="alltenant"){ view="alltenants"; render(); jumpHighlight("alltenant_"+m.id); }
+    else if(m.kind==="landlord"){ view="landlords"; render(); jumpHighlight("alllandlord_"+m.id); }
+    else if(m.kind==="sale"){ view="sales"; render(); jumpHighlight("sale_"+m.id); }
+  }
+  $("#q").addEventListener("input",()=>{
+    const digits=$("#q").value.replace(/\D/g,"");
+    if(digits.length<8) return;
+    const m=findPhoneMatch(digits);
+    if(m) jumpToPhoneMatch(m);
+  });
+
+  // ---- contacted stamping -- delegated so it survives every render() clearing the DOM.
+  // Tapping ANY wa.me draft link stamps the phone "contacted today"; tapping the small
+  // × on a stamp chip undoes it. Clicking the link itself is never prevented -- it still
+  // opens WhatsApp in a new tab, this only records the tap on this device.
+  document.addEventListener("click",e=>{
+    const u=e.target.closest("[data-undo-contact]");
+    if(u){ e.preventDefault(); e.stopPropagation(); clearContacted(u.getAttribute("data-undo-contact")); render(); return; }
+    // 🗂 CRM opens the record drawer. stopPropagation because these buttons sit inside
+    // rows whose own click switches tabs — without it, opening a record also navigates.
+    const c=e.target.closest("[data-crm]");
+    if(c){ e.preventDefault(); e.stopPropagation();
+      openCRM({kind:c.getAttribute("data-crm"),id:c.getAttribute("data-crm-id"),
+               name:c.getAttribute("data-crm-name"),phone:c.getAttribute("data-crm-phone")});
+      return; }
+    const a=e.target.closest("a[data-wa-phone]");
+    if(a){ const p=a.getAttribute("data-wa-phone"); if(p){ setContacted(p,a.getAttribute("data-wa-name")||null); render(); } }
+  });
+  $("#drawerBg").onclick=closeCRM;
+  document.addEventListener("keydown",e=>{ if(e.key==="Escape"&&CUR_SUBJ) closeCRM(); });
+  CRM.boot();
+
+  $("#themeToggle").onclick=()=>{
+    const systemLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
+    const effective = THEME || (systemLight ? "light":"dark");
+    THEME = effective==="light" ? "dark" : "light";
+    localStorage.setItem("cbk_theme", THEME);
+    applyTheme(THEME);
+  };
+
+  function resetFilters(){
+    ["q","fd","fv","fs","fbmin","fbmax","fgender","fpax","fpass","fethnat","flease","fmovein","ffresh","fptype","frbmin","frbmax"].forEach(id=>{const e=$("#"+id); if(e) e.value="";});
+    $("#fh").checked=false; $("#fdead").checked=true; $("#fsort").value="recency";
+    const pd=$("#fpd"); if(pd) Array.from(pd.options).forEach(o=>o.selected=false);
+  }
+  $("#clr").onclick=()=>{ ACTIVE_PRESET=null; resetFilters(); saveFilters(); render(); };
+
+  // ---- one tap presets ----------------------------------------------------------
+  // The jobs Winfred actually repeats, each of which used to take 3 or 4 filter changes
+  // (13 Aug 2026). A preset resets first, then sets only its own fields, so two presets
+  // can never silently stack. Tapping the active one again clears it.
+  const PRESETS=[
+    {id:"urgent", label:"🔥 Urgent this month", view:"tenant",
+     apply(){ $("#fmovein").value="this"; $("#fsort").value="movein"; }},
+    {id:"revival", label:"❄️ Cold, needs revival", view:"tenant",
+     apply(){ $("#fdead").checked=false; $("#fsort").value="recency"; }},
+    {id:"cheap", label:"💰 Under $1,300", view:"listing",
+     apply(){ $("#frbmax").value="1300"; }},
+    {id:"qualified", label:"📋 Qualified matches", view:"work",
+     apply(){ $("#fv").value="QUALIFIED"; $("#fh").checked=true; }},
+    {id:"noviewing", label:"⚠️ No viewing set", view:"listing",
+     apply(){ $("#fs").value="Available"; }},
+  ];
+  let ACTIVE_PRESET=null;
+  function applyPreset(p){
+    if(ACTIVE_PRESET===p.id){ ACTIVE_PRESET=null; resetFilters(); }
+    else { ACTIVE_PRESET=p.id; resetFilters(); p.apply(); if(p.view) view=p.view; }
+    saveFilters(); render();
+  }
+  window.renderPresets=function renderPresets(){
+    const bar=$("#presetBar"); if(!bar) return;
+    bar.innerHTML="";
+    PRESETS.forEach(p=>{
+      const c=el("span","fchip", esc(p.label));
+      if(ACTIVE_PRESET===p.id){ c.style.background="rgba(46,204,113,.16)"; c.style.color="#37c07f"; c.style.fontWeight="700"; }
+      c.onclick=()=>applyPreset(p);
+      bar.appendChild(c);
+    });
+  }
+  $("#exp").onclick=()=>{
+    const f=F(); let lines=[], label="list";
+    if(view==="landlords"){
+      label="landlords";
+      let ls=(DATA.all_landlords||[]).slice();
+      if(f.d) ls=ls.filter(l=>l.primary_district===f.d); if(f.s) ls=ls.filter(l=>l.availability===f.s);
+      if(f.q){ const hay=l=>(l.name+" "+l.district+" "+l.address).toLowerCase(); ls=ls.filter(l=>hay(l).includes(f.q)); }
+      lines=ls.map(l=>[l.id,l.name,l.availability,l.primary_district,rentTxt(l),l.phone,l.last_contact].join("\t"));
+      lines.unshift(["id","name","status","district","rent","phone","last_contact"].join("\t"));
+    } else if(view==="alltenants"){
+      label="tenants";
+      let ts=(DATA.all_tenants||[]).slice();
+      if(f.d) ts=ts.filter(t=>t.primary_district===f.d); if(f.s) ts=ts.filter(t=>t.looking===f.s);
+      if(f.q){ const hay=t=>(t.name+" "+t.district+" "+(t.preferred_location||"")).toLowerCase(); ts=ts.filter(t=>hay(t).includes(f.q)); }
+      lines=ts.map(t=>[t.id,t.name,t.looking,t.primary_district,t.budget||"",t.phone,t.last_contact].join("\t"));
+      lines.unshift(["id","name","status","district","budget","phone","last_contact"].join("\t"));
+    } else if(view==="sales"){
+      label="sale listings";
+      let ss=(DATA.sales||[]).slice();
+      if(f.d) ss=ss.filter(s=>s.primary_district===f.d); if(f.s) ss=ss.filter(s=>s.sale_status===f.s);
+      if(f.q){ const hay=s=>(s.name+" "+s.district+" "+s.address).toLowerCase(); ss=ss.filter(s=>hay(s).includes(f.q)); }
+      lines=ss.map(s=>[s.id,s.name,s.sale_status,s.district,s.asking_price||"",s.phone,s.last_contact].join("\t"));
+      lines.unshift(["id","name","status","district","asking_price","phone","last_contact"].join("\t"));
+    } else {
+      label="listings"; lines=DATA.listings.map(l=>[l.id,l.name,l.availability,l.district,rentTxt(l),l.phone].join("\t"));
+      lines.unshift(["id","name","status","district","rent","phone"].join("\t"));
+    }
+    navigator.clipboard.writeText(lines.join("\n"));
+    const b=$("#exp"); const orig=b.textContent; b.textContent="Copied "+(lines.length-1)+" "+label+" ✓"; setTimeout(()=>b.textContent=orig,2000);
+  };
   render();
 })();
 </script>
