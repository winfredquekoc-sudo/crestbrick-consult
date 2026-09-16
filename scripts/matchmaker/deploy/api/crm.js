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
import { db, ensureSchema, configured, CRM_STAGES, CRM_KINDS } from "../lib/db.js";
import { str, date, bool, validateDealFields, validateDispatchFields, validateAmbiguousFields, nextDispatchStatus } from "../lib/crm-validate.js";

// item 5/46 -- CRM_STAGES/CRM_KINDS now live in ../lib/db.js, single sourced
// with scripts/ops/log_deal.py's MM_STAGE_MAP/MM_DEAL_TYPE_MAP (see that
// file's own comment) instead of being redeclared here.
const STAGES = new Set(CRM_STAGES);
const KINDS = new Set(CRM_KINDS);
const MAX_OPS = 200;

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
  const [entities, notes, tasks, match, activity, deals, dispatch, serverTime] = await Promise.all([
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
    client.query(`select id, key, deal_type, property, price, commission_gross, commission_net,
                         cobroke_agent, cobroke_split_pct, stage,
                         to_char(otp_date,'YYYY-MM-DD') as otp_date,
                         to_char(completion_date,'YYYY-MM-DD') as completion_date,
                         to_char(deal_date,'YYYY-MM-DD') as deal_date, notes,
                         to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at
                  from crm_deal order by created_at desc limit 2000`),
    // queued, pulled and cancelled rows. A sent row has nothing left for
    // either the app or crm_pull.py to act on, so that one status is left out
    // of the payload the same way notes/tasks/activity are capped above,
    // rather than growing this endpoint's response with a table that never
    // gets pruned. cancelled has to stay in, even though nothing acts on most
    // of them either: crm_pull.py's own cleanup step needs to see a row that
    // was cancelled after already being pulled and appended, so it can remove
    // the matching item from the real morning dispatch queue before 08:00.
    client.query(`select id, tenant_id, listing_id, jid, phone, text, viewing_slot, status, device,
                         to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as created_at,
                         to_char(pulled_at,'YYYY-MM-DD"T"HH24:MI:SSZ') as pulled_at,
                         to_char(ambiguous_since,'YYYY-MM-DD"T"HH24:MI:SSZ') as ambiguous_since
                  from crm_dispatch where status in ('queued','pulled','cancelled')
                  order by created_at desc limit 2000`),
    // crm_pull.py's 7 day expiry check on a stuck pulled row has to compare
    // against a clock neither side can skew relative to the other — the
    // database's own now(), the same clock created_at/pulled_at are already
    // stamped with, not the Mac's local clock and not the Vercel lambda's
    // own process clock either.
    client.query(`select to_char(now(),'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') as t`),
  ]);
  return {
    ok: true,
    entities: entities.rows,
    notes: notes.rows,
    tasks: tasks.rows,
    match: match.rows,
    activity: activity.rows,
    deals: deals.rows,
    dispatch: dispatch.rows,
    server_time: serverTime.rows[0].t,
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
        // "returning key" is what lets the follow up queue's one tap Done/Snooze log an
        // activity row without the client having to know the task's entity key — a
        // standalone task op only ever carries id/title/due/done (see app.js CRM.snoozeTask/
        // completeTask), never the key of whatever it is linked to.
        const upd = await client.query(
          `update crm_task set title = coalesce($2, title), due = coalesce($3, due), done = $4,
                               done_at = case when $4 then now() else null end
           where id = $1
           returning key, title, due`,
          [id, str(o.title, 300), date(o.due), bool(o.done)]
        );
        const row = upd.rows[0];
        if (row && row.key) {
          const verb = bool(o.done) ? "task:done" : (o.due ? "task:snooze" : "task:updated");
          const detail = (row.title || "").slice(0, 100) + (o.due ? (" -> " + row.due) : "");
          await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
            [row.key, verb, detail]);
        }
        return 1;
      }
      const title = str(o.title, 300);
      if (!title) return 0;
      // done must ride along on the INSERT itself, not just the UPDATE branch above — a
      // task can be marked done (via CRM.completeTask/snoozeTask mutating the still
      // queued add op, see app.js) before it has ever reached the server, and without
      // this the very first snapshot after that sync would show it undone again.
      // done_at mirrors the UPDATE branch's own case/when — a task created already
      // done must not read as done with no completion timestamp.
      const r = await client.query(
        `insert into crm_task (key, title, due, done, done_at)
         values ($1,$2,$3,$4, case when $4 then now() else null end) returning id`,
        [key, title, date(o.due), bool(o.done)]
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
    case "deal": {
      const d = validateDealFields(o);
      if (!d) return 0;
      // A deal can arrive with a linked contact that has never had its own crm_entity row
      // yet (a merged in but never touched tenant/landlord/sale from DATA) — upsertEntity
      // it first, same as note/task above, or the foreign key on crm_deal.key rejects the
      // insert outright.
      const key = o.key ? await upsertEntity(client, o) : null;
      await client.query(
        `insert into crm_deal (id, key, deal_type, property, price, commission_gross,
                                commission_net, cobroke_agent, cobroke_split_pct, stage,
                                otp_date, completion_date, deal_date, notes, updated_at)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, now())
         on conflict (id) do update set
           key = excluded.key, deal_type = excluded.deal_type, property = excluded.property,
           price = excluded.price, commission_gross = excluded.commission_gross,
           commission_net = excluded.commission_net, cobroke_agent = excluded.cobroke_agent,
           cobroke_split_pct = excluded.cobroke_split_pct, stage = excluded.stage,
           otp_date = excluded.otp_date, completion_date = excluded.completion_date,
           deal_date = excluded.deal_date, notes = excluded.notes, updated_at = now()`,
        [d.id, key, d.deal_type, d.property, d.price, d.commission_gross, d.commission_net,
         d.cobroke_agent, d.cobroke_split_pct, d.stage, d.otp_date, d.completion_date,
         d.deal_date, d.notes]
      );
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [key, "deal:" + d.stage, (d.property || "deal").slice(0, 120)]);
      return 1;
    }
    case "deal_delete": {
      const id = str(o.id, 60);
      if (!id) return 0;
      await client.query(`delete from crm_deal where id = $1`, [id]);
      return 1;
    }
    // Enqueues (from the app's Mark queued action and the dispatch drawer) or
    // updates (from crm_pull.py, moving a row to pulled once it has appended
    // the draft to the real morning dispatch queue on Winfred's Mac) one
    // crm_dispatch row. Always the whole record, same upsert shape as "deal"
    // above — there is no separate patch shape for a partial update.
    case "dispatch": {
      const d = validateDispatchFields(o);
      if (!d) return 0;
      // Read the current status first so nextDispatchStatus (crm-validate.js)
      // decides the real write, not the SQL text — a pulled or sent row must
      // never regress to queued through this op, a sent row never changes
      // again, and cancelled only ever moves back to queued (a genuine
      // requeue of the same pair), never straight to pulled or sent.
      const cur = await client.query(`select status from crm_dispatch where id = $1`, [d.id]);
      const currentStatus = cur.rows[0] ? cur.rows[0].status : null;
      const nextStatus = nextDispatchStatus(currentStatus, d.status);
      await client.query(
        `insert into crm_dispatch (id, tenant_id, listing_id, jid, phone, text, viewing_slot, status, device, pulled_at)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9, case when $8 = 'pulled' then now() else null end)
         on conflict (id) do update set
           tenant_id = excluded.tenant_id, listing_id = excluded.listing_id, jid = excluded.jid,
           phone = excluded.phone, text = excluded.text, viewing_slot = excluded.viewing_slot,
           status = $8, device = coalesce(excluded.device, crm_dispatch.device),
           -- Stamped fresh every time a row newly BECOMES pulled (its prior
           -- status was something else), not only the first time ever — a
           -- row cancelled and requeued and pulled again a second time must
           -- carry the SECOND pull's timestamp, since crm_pull.py's own 7
           -- day expiry check reads this value, against this same server's
           -- clock, to decide when a still pulled row has been sitting long
           -- enough to give up on. Left untouched on a redundant reconfirmation
           -- of an already pulled row (crm_dispatch.status = 'pulled' here
           -- already), so a duplicate POST cannot quietly restart the clock.
           pulled_at = case when $8 = 'pulled' and crm_dispatch.status != 'pulled'
                            then now() else crm_dispatch.pulled_at end`,
        [d.id, d.tenant_id, d.listing_id, d.jid, d.phone, d.text, d.viewing_slot, nextStatus, d.device]
      );
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [d.tenant_id, "dispatch:" + nextStatus, (d.viewing_slot || d.text || "dispatch").slice(0, 120)]);
      return 1;
    }
    // A row the app unqueued, or a check in queue_drafts.py refused (dead lead,
    // unverifiable recency, already queued elsewhere), or crm_pull.py's own
    // append failed recovery — there is no reason column on crm_dispatch, so
    // the reason rides on the activity row instead, the same way a deal's own
    // free text lives in notes rather than a fixed column per possible field.
    // A row already sent is left alone (status != 'sent') — cancelling after
    // the message has actually gone out would only mislead whoever reads the
    // status later, not stop anything.
    case "dispatch_cancel": {
      const id = str(o.id, 60);
      if (!id) return 0;
      const upd = await client.query(
        `update crm_dispatch set status = 'cancelled' where id = $1 and status != 'sent' returning tenant_id`, [id]);
      if (!upd.rowCount) return 0;
      const tenant_id = upd.rows[0].tenant_id;
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [tenant_id || null, "dispatch:cancelled", str(o.reason, 500) || ""]);
      return upd.rowCount;
    }
    // crm_pull.py's own way of surfacing an archive based sent check it could
    // not fully trust (see MIN_SENT_ARCHIVE_AGE and the queue-file-and-
    // archive-at-once case in its own docstring). Never changes status —
    // nextDispatchStatus's transition table is untouched by this op — only
    // stamps ambiguous_since, and only the first time: a redundant POST
    // (crm_pull.py finding the same row still ambiguous on a later run
    // before it learns to skip an already stamped one, or a retried
    // request) must never reset the clock on when Winfred was first asked
    // to look at it. Guarded to a row still 'pulled' — once an operator has
    // resolved it (Sent moves it to sent, Not sent cancels it, both via the
    // app's existing dispatch/dispatch_cancel ops), a stale ambiguous op
    // arriving late must not stamp a freshly requeued or already settled row.
    case "ambiguous": {
      const a = validateAmbiguousFields(o);
      if (!a) return 0;
      const upd = await client.query(
        `update crm_dispatch set ambiguous_since = coalesce(ambiguous_since, now())
         where id = $1 and status = 'pulled' returning tenant_id`,
        [a.id]
      );
      if (!upd.rowCount) return 0;
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [upd.rows[0].tenant_id || null, "dispatch:ambiguous", "needs an operator check — may or may not have sent"]);
      return upd.rowCount;
    }
    // A plain activity log line with no other side effect — used by crm_pull.py
    // to record a completed deal import into clients.db, so that action shows
    // up in the app's own activity feed rather than only in a terminal log on
    // Winfred's Mac.
    case "activity": {
      const verb = str(o.verb, 60);
      if (!verb) return 0;
      await client.query(`insert into crm_activity (key, verb, detail) values ($1,$2,$3)`,
        [str(o.key, 120), verb, str(o.detail, 500)]);
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
