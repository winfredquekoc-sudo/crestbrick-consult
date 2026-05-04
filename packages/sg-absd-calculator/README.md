# sg-absd-calculator

Singapore Additional Buyer's Stamp Duty (ABSD) + Buyer's Stamp Duty (BSD) calculator. 2026 IRAS rates. Pure JavaScript, zero dependencies.

## Install

```bash
npm install sg-absd-calculator
```

## Usage

```js
const { calculateAbsd } = require('sg-absd-calculator');

// Singapore Citizen buying a second residential property at $2M
const r = calculateAbsd({
  price: 2_000_000,
  citizenship: 'SC',
  propertyCount: 2,
});

console.log(r);
// {
//   amount: 464600,        // total stamp duty (BSD + ABSD)
//   bsd: 64600,            // BSD on $2M residential
//   absd: 400000,          // ABSD: 20% of $2M
//   rate: 0.20,
//   ratePercent: '20%',
//   breakdown: { price: 2000000, citizenship: 'SC', propertyCount: 2, bsd: 64600, absd: 400000, total: 464600 }
// }
```

## API

### `calculateAbsd({ price, citizenship, propertyCount })`

Returns an object with the full BSD + ABSD breakdown.

| field         | type   | description                              |
| ------------- | ------ | ---------------------------------------- |
| `amount`      | number | Total stamp duty (BSD + ABSD), SGD       |
| `bsd`         | number | Buyer's Stamp Duty, SGD                  |
| `absd`        | number | Additional Buyer's Stamp Duty, SGD       |
| `rate`        | number | ABSD rate as a decimal (e.g. `0.20`)     |
| `ratePercent` | string | ABSD rate as a percent (e.g. `"20%"`)    |
| `breakdown`   | object | All input + output fields together       |

### Citizenship codes

| code     | meaning                                              |
| -------- | ---------------------------------------------------- |
| `SC`     | Singapore Citizen                                    |
| `PR`     | Singapore Permanent Resident                         |
| `FOR`    | Foreigner                                            |
| `ENT`    | Entity (company, trust)                              |
| `SCFOR`  | Joint purchase: SC + Foreigner — highest rate (60%)  |
| `SCPR`   | Joint purchase: SC + PR — PR rate applies            |

### `propertyCount`

The total number of residential properties owned **including** the one being purchased. So `1` means this is the buyer's only property; `2` means it's their second; `3` means third or more.

## 2026 ABSD rates

| Profile     | 1st property | 2nd property | 3rd+ property |
| ----------- | ------------ | ------------ | ------------- |
| SC          | 0%           | 20%          | 30%           |
| PR          | 5%           | 30%          | 35%           |
| Foreigner   | 60%          | 60%          | 60%           |
| Entity      | 65% + 5% non-remittable | — | —     |

## 2026 BSD tiers (residential)

| Slice                | Rate |
| -------------------- | ---- |
| First S$180,000      | 1%   |
| Next  S$180,000      | 2%   |
| Next  S$640,000      | 3%   |
| Next  S$500,000      | 4%   |
| Next  S$1,500,000    | 5%   |
| Above S$3,000,000    | 6%   |

## Tests

```bash
npm test
```

## CEA disclosure

This package is published by **Winfred Quek**, CEA Registration No. **R073319H**, salesperson with **Crestbrick** (Singapore).

It is provided for general information only. Stamp duty is set by IRAS and may change without notice. Verify current rates at <https://www.iras.gov.sg> before relying on output for any transaction.

This is **not** legal, tax, or financial advice. For a personalised review (including ABSD remission, joint-purchase nuance, and decoupling math), book the 4-Pillar Audit at <https://winfredquek.com>.

## License

MIT — see [LICENSE](./LICENSE).
