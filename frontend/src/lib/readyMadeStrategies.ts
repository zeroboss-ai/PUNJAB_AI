/**
 * Pre-built option strategies for Punjab AI Strategy Builder (Sensibull style).
 */
import type { StrategyLeg } from "./blackScholes";

export type StrategyCategory = "bullish" | "bearish" | "neutral" | "others";

export interface ReadyMadeStrategy {
  id: string;
  name: string;
  category: StrategyCategory;
  description: string;
  outlook: string;
  /** Mini SVG path data (viewBox 0 0 40 24) representing payoff profile. */
  iconSvg: string;
  createLegs: (
    strikes: number[],
    atm: number,
    expiry: string,
    nextExpiry: string,
    lotSize: number,
    underlying: string
  ) => Omit<StrategyLeg, "id" | "price" | "key" | "symbol">[];
}

export const READY_MADE_STRATEGIES: ReadyMadeStrategy[] = [
  // ──────────────────────── BULLISH ────────────────────────
  {
    id: "buy_call",
    name: "Buy Call",
    category: "bullish",
    outlook: "Strongly Bullish",
    description: "Unlimited profit if underlying rises strongly. Limited loss capped at premium paid.",
    iconSvg: "M 4 20 L 20 20 L 36 6",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "CE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },
  {
    id: "sell_put",
    name: "Sell Put",
    category: "bullish",
    outlook: "Moderately Bullish",
    description: "Earn premium if underlying stays above strike. Substantial risk if price falls.",
    iconSvg: "M 4 22 L 20 10 L 36 10",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const otmPut = idx > 1 ? strikes[idx - 1] : atm;
      return [{ enabled: true, kind: "PE", side: "S", strike: otmPut, expiry, lots: 1, lotSize, underlying }];
    },
  },
  {
    id: "bull_call_spread",
    name: "Bull Call Spread",
    category: "bullish",
    outlook: "Moderately Bullish",
    description: "Buy ATM Call, Sell OTM Call. Lower cost than buying naked call, capped profit & loss.",
    iconSvg: "M 4 20 L 16 20 L 28 8 L 36 8",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const buyK = atm;
      const sellK = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "CE", side: "B", strike: buyK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "bull_put_spread",
    name: "Bull Put Spread",
    category: "bullish",
    outlook: "Mildly Bullish to Neutral",
    description: "Sell OTM Put, Buy lower OTM Put. Net credit received, defined risk hedge.",
    iconSvg: "M 4 20 L 14 20 L 26 8 L 36 8",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const sellK = idx > 1 ? strikes[idx - 1] : atm;
      const buyK = idx > 3 ? strikes[idx - 3] : (idx > 1 ? strikes[idx - 2] : strikes[0]);
      return [
        { enabled: true, kind: "PE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "B", strike: buyK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "call_ratio_backspread",
    name: "Call Ratio Back Spread",
    category: "bullish",
    outlook: "Very Bullish with Big Move",
    description: "Sell 1 ATM Call, Buy 2 OTM Calls. Unlimited upside, small limited loss or profit if flat.",
    iconSvg: "M 4 12 L 14 12 L 22 22 L 36 4",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const sellK = atm;
      const buyK = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "CE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: buyK, expiry, lots: 2, lotSize, underlying },
      ];
    },
  },
  {
    id: "long_calendar_call",
    name: "Long Calendar with Calls",
    category: "bullish",
    outlook: "Targeted Move by Near Expiry",
    description: "Sell near-expiry ATM Call, Buy far-expiry ATM Call. Profits from theta decay of near leg.",
    iconSvg: "M 4 18 C 12 18, 16 6, 20 6 C 24 6, 28 18, 36 18",
    createLegs: (_strikes, atm, expiry, nextExpiry, lotSize, underlying) => [
      { enabled: true, kind: "CE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
      { enabled: true, kind: "CE", side: "B", strike: atm, expiry: nextExpiry || expiry, lots: 1, lotSize, underlying },
    ],
  },
  {
    id: "bull_condor",
    name: "Bull Condor",
    category: "bullish",
    outlook: "Moderately Bullish Range",
    description: "4-leg call spread with defined risk, maximum profit in target zone above current price.",
    iconSvg: "M 4 18 L 12 18 L 18 8 L 26 8 L 32 18 L 36 18",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const k1 = idx + 1 < strikes.length ? strikes[idx + 1] : atm;
      const k2 = idx + 2 < strikes.length ? strikes[idx + 2] : k1;
      const k3 = idx + 3 < strikes.length ? strikes[idx + 3] : k2;
      const k4 = idx + 4 < strikes.length ? strikes[idx + 4] : k3;
      return [
        { enabled: true, kind: "CE", side: "B", strike: k1, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: k2, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: k3, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: k4, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "bull_butterfly",
    name: "Bull Butterfly",
    category: "bullish",
    outlook: "Targeted Upside Pin",
    description: "Buy 1 lower Call, Sell 2 mid Calls, Buy 1 higher Call. Low cost, high reward at target pin.",
    iconSvg: "M 4 18 L 14 18 L 24 6 L 34 18 L 36 18",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const k1 = idx + 1 < strikes.length ? strikes[idx + 1] : atm;
      const k2 = idx + 2 < strikes.length ? strikes[idx + 2] : k1;
      const k3 = idx + 3 < strikes.length ? strikes[idx + 3] : k2;
      return [
        { enabled: true, kind: "CE", side: "B", strike: k1, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: k2, expiry, lots: 2, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: k3, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "range_forward",
    name: "Range Forward",
    category: "bullish",
    outlook: "Bullish Fence",
    description: "Buy OTM Call, Sell OTM Put. Zero or minimal upfront cost, synthetic forward with band.",
    iconSvg: "M 4 22 L 14 14 L 26 14 L 36 6",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const otmPut = idx > 1 ? strikes[idx - 1] : atm;
      const otmCall = idx + 1 < strikes.length ? strikes[idx + 1] : atm;
      return [
        { enabled: true, kind: "PE", side: "S", strike: otmPut, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: otmCall, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "long_synthetic_future",
    name: "Long Synthetic Future",
    category: "bullish",
    outlook: "Pure Bullish (Replicates Long Future)",
    description: "Buy ATM Call, Sell ATM Put. Mimics owning a future contract with 1.0 delta.",
    iconSvg: "M 4 22 L 36 4",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "CE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
      { enabled: true, kind: "PE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },

  // ──────────────────────── BEARISH ────────────────────────
  {
    id: "buy_put",
    name: "Buy Put",
    category: "bearish",
    outlook: "Strongly Bearish",
    description: "Large profit if underlying falls sharply. Limited loss capped at premium paid.",
    iconSvg: "M 4 6 L 20 20 L 36 20",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "PE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },
  {
    id: "sell_call",
    name: "Sell Call",
    category: "bearish",
    outlook: "Moderately Bearish",
    description: "Earn premium if underlying stays below strike. Unlimited risk if market rallies.",
    iconSvg: "M 4 10 L 20 10 L 36 22",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const otmCall = idx + 1 < strikes.length ? strikes[idx + 1] : atm;
      return [{ enabled: true, kind: "CE", side: "S", strike: otmCall, expiry, lots: 1, lotSize, underlying }];
    },
  },
  {
    id: "bear_put_spread",
    name: "Bear Put Spread",
    category: "bearish",
    outlook: "Moderately Bearish",
    description: "Buy ATM Put, Sell lower OTM Put. Reduces cost, defined capped profit & loss.",
    iconSvg: "M 4 8 L 12 8 L 24 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const buyK = atm;
      const sellK = idx > 2 ? strikes[idx - 2] : strikes[0];
      return [
        { enabled: true, kind: "PE", side: "B", strike: buyK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "bear_call_spread",
    name: "Bear Call Spread",
    category: "bearish",
    outlook: "Mildly Bearish to Neutral",
    description: "Sell OTM Call, Buy higher OTM Call. Net credit received, defined risk hedge.",
    iconSvg: "M 4 8 L 14 8 L 26 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const sellK = idx + 1 < strikes.length ? strikes[idx + 1] : atm;
      const buyK = idx + 3 < strikes.length ? strikes[idx + 3] : (idx + 2 < strikes.length ? strikes[idx + 2] : sellK);
      return [
        { enabled: true, kind: "CE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: buyK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "put_ratio_backspread",
    name: "Put Ratio Back Spread",
    category: "bearish",
    outlook: "Very Bearish with Big Crash",
    description: "Sell 1 ATM Put, Buy 2 OTM Puts. Massive downside profit, small profit/loss if it rallies.",
    iconSvg: "M 4 4 L 18 22 L 26 12 L 36 12",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const sellK = atm;
      const buyK = idx > 2 ? strikes[idx - 2] : strikes[0];
      return [
        { enabled: true, kind: "PE", side: "S", strike: sellK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "B", strike: buyK, expiry, lots: 2, lotSize, underlying },
      ];
    },
  },
  {
    id: "short_synthetic_future",
    name: "Short Synthetic Future",
    category: "bearish",
    outlook: "Pure Bearish (Replicates Short Future)",
    description: "Sell ATM Call, Buy ATM Put. Mimics shorting a future contract with -1.0 delta.",
    iconSvg: "M 4 4 L 36 22",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "PE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
      { enabled: true, kind: "CE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },

  // ──────────────────────── NEUTRAL ────────────────────────
  {
    id: "short_straddle",
    name: "Short Straddle",
    category: "neutral",
    outlook: "Rangebound (Low Volatility)",
    description: "Sell ATM Call + Sell ATM Put. Maximum profit if spot stays right at ATM strike.",
    iconSvg: "M 4 22 L 20 6 L 36 22",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "CE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
      { enabled: true, kind: "PE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },
  {
    id: "short_strangle",
    name: "Short Strangle",
    category: "neutral",
    outlook: "Rangebound with Cushion",
    description: "Sell OTM Call + Sell OTM Put. Wider profit zone than straddle, but lower net credit.",
    iconSvg: "M 4 22 L 14 8 L 26 8 L 36 22",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const putK = idx > 2 ? strikes[idx - 2] : strikes[0];
      const callK = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "S", strike: putK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: callK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "iron_condor",
    name: "Iron Condor",
    category: "neutral",
    outlook: "Rangebound with Defined Risk",
    description: "Sell OTM Put & Call, Buy further OTM wings. Defined maximum profit and risk.",
    iconSvg: "M 4 20 L 10 20 L 16 8 L 24 8 L 30 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const pBuy = idx > 4 ? strikes[idx - 4] : strikes[0];
      const pSell = idx > 2 ? strikes[idx - 2] : strikes[1];
      const cSell = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 2];
      const cBuy = idx + 4 < strikes.length ? strikes[idx + 4] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "B", strike: pBuy, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "S", strike: pSell, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: cSell, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: cBuy, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "iron_butterfly",
    name: "Iron Butterfly",
    category: "neutral",
    outlook: "Pin at ATM with Wings",
    description: "Sell ATM Straddle, Buy OTM wings. Defined risk version of short straddle.",
    iconSvg: "M 4 20 L 10 20 L 20 6 L 30 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const pBuy = idx > 3 ? strikes[idx - 3] : strikes[0];
      const cBuy = idx + 3 < strikes.length ? strikes[idx + 3] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "B", strike: pBuy, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: atm, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: cBuy, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "long_straddle",
    name: "Long Straddle",
    category: "neutral",
    outlook: "Big Breakout (High Volatility)",
    description: "Buy ATM Call + Buy ATM Put. Massive profit if market moves huge in either direction.",
    iconSvg: "M 4 6 L 20 22 L 36 6",
    createLegs: (_strikes, atm, expiry, _next, lotSize, underlying) => [
      { enabled: true, kind: "CE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
      { enabled: true, kind: "PE", side: "B", strike: atm, expiry, lots: 1, lotSize, underlying },
    ],
  },
  {
    id: "long_strangle",
    name: "Long Strangle",
    category: "neutral",
    outlook: "Big Breakout at Lower Cost",
    description: "Buy OTM Call + Buy OTM Put. Lower debit than straddle, needs bigger move to profit.",
    iconSvg: "M 4 6 L 14 20 L 26 20 L 36 6",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const putK = idx > 2 ? strikes[idx - 2] : strikes[0];
      const callK = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "B", strike: putK, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: callK, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "jade_lizard",
    name: "Jade Lizard",
    category: "neutral",
    outlook: "Neutral to Mildly Bullish",
    description: "Sell OTM Put + Sell Bear Call Spread. Zero upside risk if total credit exceeds spread width.",
    iconSvg: "M 4 22 L 14 8 L 26 8 L 32 14 L 36 14",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const pSell = idx > 2 ? strikes[idx - 2] : strikes[0];
      const cSell = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 2];
      const cBuy = idx + 4 < strikes.length ? strikes[idx + 4] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "S", strike: pSell, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: cSell, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: cBuy, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },

  // ──────────────────────── OTHERS ────────────────────────
  {
    id: "long_call_butterfly",
    name: "Long Call Butterfly",
    category: "others",
    outlook: "Neutral Pin at ATM",
    description: "Buy 1 lower Call, Sell 2 ATM Calls, Buy 1 higher Call. Minimal debit, peak at ATM.",
    iconSvg: "M 4 20 L 10 20 L 20 6 L 30 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const k1 = idx > 2 ? strikes[idx - 2] : strikes[0];
      const k2 = atm;
      const k3 = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "CE", side: "B", strike: k1, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: k2, expiry, lots: 2, lotSize, underlying },
        { enabled: true, kind: "CE", side: "B", strike: k3, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "long_put_butterfly",
    name: "Long Put Butterfly",
    category: "others",
    outlook: "Neutral Pin at ATM (Puts)",
    description: "Buy 1 lower Put, Sell 2 ATM Puts, Buy 1 higher Put. Minimal debit, peak at ATM.",
    iconSvg: "M 4 20 L 10 20 L 20 6 L 30 20 L 36 20",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const k1 = idx > 2 ? strikes[idx - 2] : strikes[0];
      const k2 = atm;
      const k3 = idx + 2 < strikes.length ? strikes[idx + 2] : strikes[strikes.length - 1];
      return [
        { enabled: true, kind: "PE", side: "B", strike: k1, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "S", strike: k2, expiry, lots: 2, lotSize, underlying },
        { enabled: true, kind: "PE", side: "B", strike: k3, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "covered_call",
    name: "Covered Call",
    category: "others",
    outlook: "Mildly Bullish (Cash/Future Yield)",
    description: "Buy 1 Future, Sell 1 OTM Call. Earn monthly option premium against your future position.",
    iconSvg: "M 4 22 L 24 8 L 36 8",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const cSell = idx + 2 < strikes.length ? strikes[idx + 2] : atm;
      return [
        { enabled: true, kind: "FUT", side: "B", strike: 0, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "CE", side: "S", strike: cSell, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
  {
    id: "protective_put",
    name: "Protective Put",
    category: "others",
    outlook: "Bullish with Downside Insurance",
    description: "Buy 1 Future + Buy 1 OTM Put. Downside risk completely capped, unlimited upside.",
    iconSvg: "M 4 16 L 16 16 L 36 4",
    createLegs: (strikes, atm, expiry, _next, lotSize, underlying) => {
      const idx = strikes.indexOf(atm);
      const pBuy = idx > 2 ? strikes[idx - 2] : atm;
      return [
        { enabled: true, kind: "FUT", side: "B", strike: 0, expiry, lots: 1, lotSize, underlying },
        { enabled: true, kind: "PE", side: "B", strike: pBuy, expiry, lots: 1, lotSize, underlying },
      ];
    },
  },
];
