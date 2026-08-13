// Matchmaker CRM API — the durable half of the app.
//
// The split this endpoint exists to preserve:
//   FACTS  (who exists, their phone, budget, last message) come from the WhatsApp and
//          CSV databases via build.py and are baked into index.html. They are read only
//          here and are regenerated on every build.
//   CRM    (stage, notes, tasks, flags, contacted stamps, match verdicts) lives in this
//          database and is the ONLY thing the app writes.
// Nothing in here ever writes back to the rental databases, so a rebuild can never be
// overwritten by the app and the app can never be clobbered by a rebuild.
//
// Auth is the project's Basic Auth middleware, which matches /api/* like everything
// else. There is no second auth check here on purpose: a request that reached this
// function already passed the wall, and a second scheme would be another thing to
// get wrong. Never loosen middleware.js's matcher to exclude /api.
import { db, ensureSchema, configured } from "../lib/db.js";

const STAGES = new Set([
  "new", "contacted", "qualified", "viewing_set", "viewed",
  "offer", "closed_won", "closed_lost", "dormant",
]);
const KINDS = new Set(["tenant", "landlord", "listing", "sale", "person"]);
const MAX_OPS = 200;

const str = (v, max) => {
  if (v == null) return null;
  const s = String(v).trim();
  return s ? s.slice(0, max) : null;
};
// Dates arrive as YYYY-MM-DD from the client. Anything else becomes null rather than
// reaching Postgres, where a malformed date aborts the whole transaction — and since the
// batch is transactional, one bad date would discard every good write sent with it.
// The shape check alone is not enough: "2026-13-99" matches the pattern and is still
// rejected by Postgres, so the calendar itself has to agree the day exists.
const date = (v) => {
  const s = String(v || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const d = new Date(s + "T00:00:00Z");
  return !isNaN(d) && d.toISOString().slice(0, 10) === s ? s : null;
};
const bool = (v) => v === true || v === "true" || v === 1;

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1_000_000) throw new Error("payload too large");
  }
  return raw ? JSON.parse(raw) : {};
}

async function snapshot(client) {
  const [entities, notes, tasks, match, activity] = await Promise.all([
    // crm_entity was the only one of these five queries with no LIMIT — notes/tasks/
    // activity are capped below at 2000/1000/300. This table holds every tenant,
    // landlord and listing key ever seen, all with names and phone numbers, so an
    // unbounded select here is the largest single PII payload this endpoint can return.
    client.query(`select key, kind, ref_id, name, phone, stage, next_action,
                         to_char(next_due,'YYYY-MM-DD') as next_due, flagged,
                         to_char(contacted_on,'YYYY-MM-DD') as contacted_on, archived
                  from crm_entity order by updated_at desc nulls last, key limit 5000`),
    // `id desc` is a tiebreaker, not decoration: now() is the transaction timestamp, so
    // two notes written in one batch share created_at exactly and would otherwise come
    // back in whatever order the planner felt like — the drawer shows these newest first.
    client.query(`select id, key, body, to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at
                  from crm_note order by created_at desc, id desc limit 2000`),
    client.query(`select id, key, title, to_char(due,'YYYY-MM-DD') as due, done,
                         to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at
                  from crm_task order by done asc, due asc nulls last, created_at desc, id desc limit 1000`),
    client.query(`select listing_id, tenant_id, status from crm_match_status`),
    client.query(`select id, key, verb, detail, to_char(at,'YYYY-MM-DD"T"HH24:MI:SSZ') as at
                  from crm_activity order by at desc, id desc limit 300`),
  ]);
  return {
    ok: true,
    entities: entities.rows,
    notes: notes.rows,
    tasks: tasks.rows,
    match: match.rows,
    activity: activity.rows,
  };
}

// Creates the entity row if it is not there yet, and only overwrites name/phone when the
// incoming build actually has one — a rebuild that loses a contact's saved name must not
// blank the name already recorded here.
async function upsertEntity(client, o) {
  const key = str(o.key, 120);
  if (!key) return null;
  const kind = KINDS.has(o.kind) ? o.kind : "person";
  await client.query(
    `insert into crm_entity (key, kind, ref_id, name, phone)
     values ($1,$2,$3,$4,$5)
     on conflict (key) do update set
       ref_id = coalesce(excluded.ref_id, crm_entity.ref_id),
       name   = coalesce(excluded.name,   crm_entity.name),
       phone  = coalesce(excluded.phone,  crm_entity.phone)`,
    [key, kind, str(o.ref_id, 80), str(o.name, 200), str(o.phone, 40)]
  );
  return key;
}

async function applyOp(client, o) {
  switch (o.op) {
    case "entity": {
      const key = await upsertEntity(client, o);
      if (!key) return 0;
      const p = o.patch || {};
      const sets = [], vals = [];
      const put = (col, val) => { sets.push(`${col} = $${vals.push(val) + 1}`); };
      if ("stage" in p && STAGES.has(p.stage)) put("stage", p.stage);
      if ("next_action" in p) put("next_action", str(p.next_action, 400));
      if ("next_due" in p) put("next_due", date(p.next_due));
      if ("flagged" in p) put("flagged", bool(p.flagged));
      if ("contacted_on" in p) put("contacted_on", date(p.contacted_on));
      if ("archived" in p) put("archived", bool(p.archived));
      if (!sets.length) return 0;
      sets.push("updated_at = now()");
      await client.query(`update crm_entity set ${sets.join(", ")} where key = $1`, [key, ...vals]);
      const verb = "stage" in p ? "stage:" + p.stage
                 : "contacted_on" in p ? (p.contacted_on ? "contacted" : "contacted:undo")
                 : "flagged" in p ? (bool(p.flagged) ? "flagged" : "unflagged")
                 : "updated";
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [key, verb, str(o.name, 200)]);
      return 1;
    }
    case "note": {
      const key = await upsertEntity(client, o);
      const body = str(o.body, 4000);
      if (!key || !body) return 0;
      await client.query(`insert into crm_note (key, body) values ($1,$2)`, [key, body]);
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,'note',$2)`,
        [key, body.slice(0, 120)]);
      return 1;
    }
    case "note_delete": {
      const id = parseInt(o.id, 10);
      if (!Number.isFinite(id)) return 0;
      await client.query(`delete from crm_note where id = $1`, [id]);
      return 1;
    }
    case "task": {
      const key = o.key ? await upsertEntity(client, o) : null;
      const id = parseInt(o.id, 10);
      if (Number.isFinite(id)) {
        await client.query(
          `update crm_task set title = coalesce($2, title), due = coalesce($3, due), done = $4,
                               done_at = case when $4 then now() else null end
           where id = $1`,
          [id, str(o.title, 300), date(o.due), bool(o.done)]
        );
        return 1;
      }
      const title = str(o.title, 300);
      if (!title) return 0;
      const r = await client.query(
        `insert into crm_task (key, title, due) values ($1,$2,$3) returning id`,
        [key, title, date(o.due)]
      );
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,'task',$2)`,
        [key, title.slice(0, 120)]);
      return r.rowCount;
    }
    case "task_delete": {
      const id = parseInt(o.id, 10);
      if (!Number.isFinite(id)) return 0;
      await client.query(`delete from crm_task where id = $1`, [id]);
      return 1;
    }
    case "match": {
      const lid = str(o.listing_id, 80), tid = str(o.tenant_id, 80);
      if (!lid || !tid) return 0;
      if (!str(o.status, 60)) {
        await client.query(`delete from crm_match_status where listing_id = $1 and tenant_id = $2`, [lid, tid]);
        return 1;
      }
      await client.query(
        `insert into crm_match_status (listing_id, tenant_id, status) values ($1,$2,$3)
         on conflict (listing_id, tenant_id) do update
           set status = excluded.status, updated_at = now()`,
        [lid, tid, str(o.status, 60)]
      );
      return 1;
    }
    default:
      return 0;
  }
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (!configured) {
    // 501 rather than 500: the client reads this as "run in local mode", not "retry".
    return res.status(501).json({
      ok: false,
      configured: false,
      error: "DATABASE_URL is not set on this Vercel project — CRM is running in local-only mode.",
    });
  }

  let client;
  try {
    await ensureSchema();
    client = await db().connect();

    if (req.method === "GET") {
      return res.status(200).json(await snapshot(client));
    }

    if (req.method === "POST") {
      const body = await readBody(req);
      const ops = Array.isArray(body.ops) ? body.ops : [];
      if (ops.length > MAX_OPS) {
        return res.status(413).json({ ok: false, error: `too many ops (max ${MAX_OPS})` });
      }
      let applied = 0;
      // One transaction for the whole batch: the client sends a queue it has already
      // applied optimistically, so a half applied batch would leave the two out of sync
      // with no way for the client to tell which half landed.
      await client.query("BEGIN");
      try {
        for (const o of ops) applied += await applyOp(client, o);
        await client.query("COMMIT");
      } catch (e) {
        await client.query("ROLLBACK");
        throw e;
      }
      // Return the fresh snapshot so a write doubles as a sync — this is what lets a
      // second device pick up the first one's changes without a separate poll.
      const snap = await snapshot(client);
      return res.status(200).json({ ...snap, applied });
    }

    res.setHeader("Allow", "GET, POST");
    return res.status(405).json({ ok: false, error: "method not allowed" });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e && e.message || e) });
  } finally {
    if (client) client.release();
  }
}
