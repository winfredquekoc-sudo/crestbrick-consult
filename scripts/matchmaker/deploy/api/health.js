// Deploy check for the CRM backend. deploy.sh calls this after every deploy so that a
// backend that cannot reach its database is reported at deploy time rather than
// discovered later as writes that silently stayed on one device.
//
// Answers 200 in all three healthy states — the body says which one:
//   configured:false          → no DATABASE_URL, app runs local-only (an intended state)
//   configured:true, db:true  → backend live
//   configured:true, db:false → DATABASE_URL set but unreachable, and `error` says why
// Reports no connection string, host, or credential.
import { db, ensureSchema, configured } from "../lib/db.js";

export default async function handler(_req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (!configured) {
    return res.status(200).json({ ok: true, configured: false, db: false, mode: "local-only" });
  }
  try {
    await ensureSchema();
    const r = await db().query(
      `select (select count(*) from crm_entity) as entities,
              (select count(*) from crm_note)   as notes,
              (select count(*) from crm_task where done = false) as open_tasks`
    );
    return res.status(200).json({ ok: true, configured: true, db: true, mode: "cloud", counts: r.rows[0] });
  } catch (e) {
    return res.status(200).json({
      ok: false, configured: true, db: false, mode: "cloud-unreachable",
      error: String((e && e.message) || e).slice(0, 300),
    });
  }
}
