/**
 * sg-absd-calculator
 *
 * Singapore ABSD + BSD calculator (2026 IRAS rates).
 *
 * @license MIT
 * @author Winfred Quek <winfredquekoc@gmail.com>
 *
 * NOTE — CEA disclosure:
 *   This package is published by Winfred Quek, CEA Registration No. R073319H,
 *   salesperson with Crestbrick (Singapore). It is provided for general
 *   information only. Stamp duty is set by IRAS and may change without notice.
 *   Verify current rates at https://www.iras.gov.sg before relying on output
 *   for any transaction. This is not legal, tax, or financial advice.
 */

'use strict';

/**
 * Compute Buyer's Stamp Duty (BSD) for residential property using 2026 tiers.
 * Tiers: 1% on first 180k, 2% on next 180k, 3% on next 640k, 4% on next 500k,
 *        5% on next 1.5M, 6% above 3M.
 *
 * @param {number} price purchase price in SGD
 * @returns {number} BSD in SGD (rounded to nearest dollar)
 */
function computeBsd(price) {
  if (typeof price !== 'number' || !isFinite(price) || price <= 0) return 0;
  const tiers = [
    [180000, 0.01],
    [180000, 0.02],
    [640000, 0.03],
    [500000, 0.04],
    [1500000, 0.05],
  ];
  let bsd = 0;
  let remaining = price;
  for (const [width, rate] of tiers) {
    if (remaining <= 0) break;
    const slice = Math.min(remaining, width);
    bsd += slice * rate;
    remaining -= slice;
  }
  if (remaining > 0) bsd += remaining * 0.06;
  return Math.round(bsd);
}

/**
 * ABSD rate table (2026, post-April-2023 hike).
 *
 * Citizenship codes:
 *   SC     = Singapore Citizen
 *   PR     = Singapore Permanent Resident
 *   FOR    = Foreigner
 *   ENT    = Entity (company / trust)
 *   SCFOR  = Joint purchase: SC + Foreigner (highest rate applies)
 *   SCPR   = Joint purchase: SC + PR (PR rate applies)
 *
 * propertyCount = total residential properties owned including the one being bought.
 *
 * @param {string} citizenship one of SC, PR, FOR, ENT, SCFOR, SCPR
 * @param {number} propertyCount 1 = first property; 2 = second; 3+ = third or more
 * @returns {number} ABSD rate as decimal (e.g. 0.20 for 20%)
 */
function absdRate(citizenship, propertyCount) {
  const c = parseInt(propertyCount, 10);
  if (!c || c < 1) return 0;
  switch (citizenship) {
    case 'SC':
      return c === 1 ? 0 : c === 2 ? 0.20 : 0.30;
    case 'PR':
      return c === 1 ? 0.05 : c === 2 ? 0.30 : 0.35;
    case 'FOR':
      return 0.60;
    case 'ENT':
      return 0.65;
    case 'SCFOR':
      return 0.60;
    case 'SCPR':
      return c === 1 ? 0.05 : c === 2 ? 0.30 : 0.35;
    default:
      return 0;
  }
}

/**
 * Calculate full BSD + ABSD breakdown for a Singapore residential purchase.
 *
 * @param {object} input
 * @param {number} input.price - purchase price in SGD
 * @param {string} input.citizenship - SC | PR | FOR | ENT | SCFOR | SCPR
 * @param {number} input.propertyCount - 1, 2, 3+
 * @returns {{
 *   amount: number,        // total stamp duty (BSD + ABSD)
 *   bsd: number,           // buyer's stamp duty
 *   absd: number,          // additional buyer's stamp duty
 *   rate: number,          // ABSD rate as decimal
 *   ratePercent: string,   // e.g. "20%"
 *   breakdown: object,
 * }}
 */
function calculateAbsd(input) {
  const { price, citizenship, propertyCount } = input || {};
  const bsd = computeBsd(price);
  const rate = absdRate(citizenship, propertyCount);
  const absd = Math.round((Number(price) || 0) * rate);
  return {
    amount: bsd + absd,
    bsd,
    absd,
    rate,
    ratePercent: (rate * 100).toFixed(0) + '%',
    breakdown: {
      price: Number(price) || 0,
      citizenship,
      propertyCount,
      bsd,
      absd,
      total: bsd + absd,
    },
  };
}

module.exports = {
  calculateAbsd,
  computeBsd,
  absdRate,
};
