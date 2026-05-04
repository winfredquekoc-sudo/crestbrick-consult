/* Hot-lead second-visit detector. Adds to all pages.
 * Tracks visits using localStorage (no cookies, no PII). When visitor returns
 * to the same property/insight page within 30 days, fires a beacon to /api/hot-lead.
 * Vercel Function on the receiving end logs to ~/.claude/state/hot-lead-visits.jsonl
 * via webhook → laptop relay. Fully privacy-respecting (no IP, no fingerprint).
 */
(function(){
  if (typeof localStorage === 'undefined') return;
  const KEY = 'wf_visits';
  const path = window.location.pathname;
  const now = Date.now();
  let data = {};
  try { data = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  data.visits = (data.visits || []).filter(v => (now - v.t) < 30*86400000);  // last 30d
  const sameCount = data.visits.filter(v => v.p === path).length;
  data.visits.push({p: path, t: now});
  if (data.visits.length > 50) data.visits = data.visits.slice(-50);
  localStorage.setItem(KEY, JSON.stringify(data));
  // Fire beacon ONLY if 2nd visit to same path
  if (sameCount === 1) {  // current visit makes it 2 total
    try {
      const beacon = JSON.stringify({
        path,
        same_page_visits: 2,
        total_visits: data.visits.length,
        first_seen: data.visits[0].t,
        ua_fragment: (navigator.userAgent || '').slice(0, 60)
      });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/hot-lead', new Blob([beacon], {type:'application/json'}));
      } else {
        fetch('/api/hot-lead', {method:'POST', body:beacon, keepalive:true, headers:{'Content-Type':'application/json'}});
      }
    } catch(e) {}
  }
})();
