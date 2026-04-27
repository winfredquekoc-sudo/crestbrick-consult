// Vercel Function: POST /api/audit
// Portfolio Strategy Audit — upgraded for the conversational 16-question flow.
// Deterministically computes summary + Winfred prep notes from the answer set.
// Also pings Telegram with a full brief.

function fmtSGD(n) {
  if (!n) return 'S$0';
  return 'S$' + Math.round(n).toLocaleString('en-SG');
}

function midpointIncome(r) {
  r = r || '';
  if (r.includes('Under S$8,000')) return 6500;
  if (r.includes('S$8,000 – S$12,000')) return 10000;
  if (r.includes('S$12,001 – S$18,000')) return 15000;
  if (r.includes('S$18,001 – S$25,000')) return 21500;
  if (r.includes('S$25,001 – S$35,000')) return 30000;
  if (r.includes('S$35,001 – S$50,000')) return 42500;
  if (r.includes('Above S$50,000')) return 60000;
  return 0;
}
function midpointDebt(r) {
  r = r || '';
  if (r.includes('Zero debt')) return 0;
  if (r.includes('Under S$2,000')) return 1000;
  if (r.includes('S$2,000 – S$4,000')) return 3000;
  if (r.includes('S$4,001 – S$6,000')) return 5000;
  if (r.includes('S$6,001 – S$8,000')) return 7000;
  if (r.includes('Above S$8,000')) return 9000;
  return 1500;
}
function midpointTarget(r) {
  r = r || '';
  if (r.includes('S$4,000 – S$6,000')) return 5000;
  if (r.includes('S$6,001 – S$9,000')) return 7500;
  if (r.includes('S$9,001 – S$15,000')) return 12000;
  if (r.includes('S$15,001 – S$25,000')) return 20000;
  if (r.includes('Above S$25,000')) return 35000;
  return 0;
}
function absdExposure(citizenship, ownership) {
  const c = citizenship || '';
  const o = ownership || '';
  const isFirst = o.includes("don't own") || o.includes('no Singapore property');
  const isThirdPlus = o.includes('2+');
  if (c.includes('Singapore Citizen')) return isFirst ? '0% (1st)' : isThirdPlus ? '30% (3rd+)' : '20% (2nd)';
  if (c.includes('Singapore Permanent Resident')) return isFirst ? '5% (1st PR)' : isThirdPlus ? '35% (3rd+ PR)' : '30% (2nd PR)';
  if (c.includes('Foreigner') || c.includes('Work Pass')) return '60% (foreigner)';
  if (c.includes('Mixed couple')) return 'Mixed — highest rate applies; remission paths exist';
  return '—';
}

function buildSummary(a) {
  const income = midpointIncome(a.monthly_income_range);
  const debt = midpointDebt(a.existing_debt_obligations);
  const tdsrRoom = Math.max(0, income * 0.55 - debt);
  const target = midpointTarget(a.target_passive_income);
  const portfolioReq = target > 0 ? target * 12 / 0.0329 : 0;

  const summary = {
    ownership: a.current_ownership_status,
    citizenship: a.citizenship_status,
    absd: absdExposure(a.citizenship_status, a.current_ownership_status),
    structure: a.purchase_structure,
    income: a.monthly_income_range,
    debt: a.existing_debt_obligations,
    tdsr_room: income > 0 ? fmtSGD(tdsrRoom) + ' / mo' : 'needs calc',
    capital: a.available_capital,
    objective: a.primary_objective,
    target_income: a.target_passive_income,
    timeline: a.wealth_timeline,
    portfolio_required: target > 0 ? fmtSGD(portfolioReq) : 'needs calc',
    risk: a.risk_tolerance_score,
    types: Array.isArray(a.property_type_openness) ? a.property_type_openness.join(' · ') : (a.property_type_openness || '—'),
    yield_exp: a.yield_expectation,
    life_stage: a.life_stage,
    legacy: a.legacy_importance,
    challenges: Array.isArray(a.key_challenges) ? a.key_challenges.join(' · ') : (a.key_challenges || '—'),
    urgency: a.urgency_timeline
  };

  const notes = [];
  const challenges = Array.isArray(a.key_challenges) ? a.key_challenges.join(' ') : (a.key_challenges || '');
  if (challenges.includes('ABSD costs')) notes.push('ABSD optimization: review decoupling, entity structures, and purchase sequencing to minimise exposure.');
  if (challenges.includes('loan eligibility') || challenges.includes('TDSR')) notes.push('TDSR maximization: review variable-income treatment, bonus averaging, guarantor structure. Multi-bank AIP strategy.');
  if (challenges.includes('overseas')) notes.push('Overseas market entry: UK (Liverpool/Manchester yield), KL (MM2H structure), Japan/Bali (niche cases).');
  if (challenges.includes('Sell current')) notes.push('Sell-first vs buy-first sequencing: BSD/ABSD timing window, bridging loan math, CPF refund.');
  if (challenges.includes('Personal name')) notes.push('Entity structure review: company 65% ABSD vs personal ownership; when entity actually wins.');
  if ((a.legacy_importance || '').includes('Critical')) notes.push('Legacy planning: trust structures for minor beneficiaries, matrimonial home remission, intergenerational transfer.');
  if ((a.current_ownership_status || '').includes('2+')) notes.push('Existing portfolio stress-test: yield, cashflow, exit sequencing across all holdings before adding another asset.');
  if ((a.primary_objective || '').includes('Diversify into overseas')) notes.push('Overseas diversification: weight, currency exposure, tax residency, exit/repatriation path.');
  if (income >= 30000 && (a.available_capital || '').includes('Above S$1,000,000')) notes.push('High-capacity profile: family-office-grade construction, commercial + shophouse carve-outs, multi-jurisdictional.');
  if (notes.length === 0) notes.push('Build the 4-Pillar Audit map (Capital / Cashflow / Progression / Protection) around the specific capital + timeline combination.');
  if (notes.length < 3) notes.push('Walk through a 10-year progression blueprint matched to timeline, with sensitivity analysis on rate + vacancy.');

  return { summary, prep_notes: notes.slice(0, 5), metrics: { income, debt, tdsr_room: Math.round(tdsrRoom), target, portfolio_required: Math.round(portfolioReq) } };
}

async function pingTelegram(body, result) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_WINFRED_CHAT_ID;
  if (!token || !chatId) return;

  const s = result.summary;
  const notes = result.prep_notes.map((n, i) => `${i+1}. ${n}`).join('\n');
  const text =
`🧭 NEW PORTFOLIO AUDIT
${body.full_name || body.name || 'Anon'} · ${body.email || ''}${body.phone ? ' · ' + body.phone : ''}
Referral: ${body.referral_source || '—'}

CURRENT POSITION
• Ownership: ${s.ownership || '—'}
• Citizenship: ${s.citizenship || '—'}
• ABSD: ${s.absd || '—'}
• Structure: ${s.structure || '—'}

FINANCIAL
• Income: ${s.income}
• Debt: ${s.debt}
• TDSR headroom: ${s.tdsr_room}
• Capital: ${s.capital}

GOALS
• Objective: ${s.objective}
• Target income: ${s.target_income}
• Timeline: ${s.timeline}
• Portfolio required: ${s.portfolio_required}

STRATEGY
• Risk: ${s.risk}
• Types: ${s.types}
• Yield: ${s.yield_exp}

CONTEXT
• Life: ${s.life_stage}
• Legacy: ${s.legacy}
• Urgency: ${s.urgency}
• Challenges: ${s.challenges}

🔥 PREP NOTES
${notes}`;

  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text })
    });
  } catch (err) { /* non-blocking */ }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'method_not_allowed' });

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (_) { body = {}; }
  }
  body = body || {};

  // Minimum viable submission
  if (!body.full_name && !body.name) return res.status(400).json({ ok: false, error: 'missing_name' });
  if (!body.email) return res.status(400).json({ ok: false, error: 'missing_email' });

  const result = buildSummary(body);
  // No per-submission Telegram ping — daily rollup at 22:00 SGT handles lead summary.
  // Marketing consent is captured in body.marketing_consent (boolean) — surfaced in daily summary + email tools.

  return res.status(200).json({ ok: true, summary: result.summary, prep_notes: result.prep_notes, metrics: result.metrics, marketing_consent: !!body.marketing_consent });
}
