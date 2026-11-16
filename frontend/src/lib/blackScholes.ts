/**
 * Black-Scholes pricing, Greeks, Implied Volatility (IV), and Payoff Engine
 * for Punjab AI Strategy Builder (Sensibull-style).
 */

const RISK_FREE_RATE = 0.07; // 7% Indian risk-free rate estimate

/** Cumulative standard normal distribution approximation (Abramowitz & Stegun). */
export function cdf(x: number): number {
  if (x < -7.0) return 0.0;
  if (x > 7.0) return 1.0;
  const sign = x < 0 ? -1 : 1;
  const absX = Math.abs(x) / Math.sqrt(2.0);
  const t = 1.0 / (1.0 + 0.3275911 * absX);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const erf = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-absX * absX);
  return 0.5 * (1.0 + sign * erf);
}

/** Standard normal probability density function (PDF). */
export function pdf(x: number): number {
  return (1.0 / Math.sqrt(2.0 * Math.PI)) * Math.exp(-0.5 * x * x);
}

/** Black-Scholes option price for European options. */
export function bsPrice(
  spot: number,
  strike: number,
  tYears: number,
  sigma: number,
  kind: "CE" | "PE",
  r = RISK_FREE_RATE
): number {
  if (tYears <= 1e-6 || sigma <= 1e-4) {
    return kind === "CE" ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0);
  }
  const sqT = sigma * Math.sqrt(tYears);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * sigma * sigma) * tYears) / sqT;
  const d2 = d1 - sqT;
  const discK = strike * Math.exp(-r * tYears);

  if (kind === "CE") {
    return spot * cdf(d1) - discK * cdf(d2);
  } else {
    return discK * cdf(-d2) - spot * cdf(-d1);
  }
}

export interface Greeks {
  delta: number;
  gamma: number;
  theta: number; // Theta per calendar day in currency points
  vega: number;  // Vega per 1 percentage point change in IV
  rho: number;   // Rho per 1 percentage point change in rate
}

/** Black-Scholes Greeks calculation. */
export function bsGreeks(
  spot: number,
  strike: number,
  tYears: number,
  sigma: number,
  kind: "CE" | "PE",
  r = RISK_FREE_RATE
): Greeks {
  if (tYears <= 1e-6 || sigma <= 1e-4) {
    const isITM = kind === "CE" ? spot > strike : strike > spot;
    return {
      delta: kind === "CE" ? (isITM ? 1 : 0) : (isITM ? -1 : 0),
      gamma: 0,
      theta: 0,
      vega: 0,
      rho: 0,
    };
  }

  const sqT = sigma * Math.sqrt(tYears);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * sigma * sigma) * tYears) / sqT;
  const d2 = d1 - sqT;
  const nd1 = pdf(d1);
  const discK = strike * Math.exp(-r * tYears);

  // Delta
  const delta = kind === "CE" ? cdf(d1) : cdf(d1) - 1.0;

  // Gamma (identical for Call and Put)
  const gamma = nd1 / (spot * sqT);

  // Vega (per 1% IV change, identical for Call and Put)
  const vega = (spot * nd1 * Math.sqrt(tYears)) / 100.0;

  // Theta (divided by 365 for 1-day decay)
  const term1 = -(spot * nd1 * sigma) / (2.0 * Math.sqrt(tYears));
  let thetaYear: number;
  if (kind === "CE") {
    thetaYear = term1 - r * discK * cdf(d2);
  } else {
    thetaYear = term1 + r * discK * cdf(-d2);
  }
  const theta = thetaYear / 365.0;

  // Rho (per 1% interest rate change)
  const rho = kind === "CE" ? (discK * tYears * cdf(d2)) / 100.0 : (-discK * tYears * cdf(-d2)) / 100.0;

  return { delta, gamma, theta, vega, rho };
}

/** Calculate Implied Volatility (IV) via Newton-Raphson with bisection fallback. */
export function calcIV(
  marketPrice: number,
  spot: number,
  strike: number,
  tYears: number,
  kind: "CE" | "PE",
  r = RISK_FREE_RATE
): number {
  if (tYears <= 1e-6 || marketPrice <= 0.05) return 0.16;

  // Intrinsic check
  const intrinsic = kind === "CE" ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0);
  if (marketPrice < intrinsic) return 0.16;

  let sigma = 0.2; // initial guess 20%
  for (let i = 0; i < 25; i++) {
    const price = bsPrice(spot, strike, tYears, sigma, kind, r);
    const diff = price - marketPrice;
    if (Math.abs(diff) < 0.01) return sigma;

    const greeks = bsGreeks(spot, strike, tYears, sigma, kind, r);
    const vega = greeks.vega * 100.0; // raw vega
    if (vega < 1e-5) break;

    const step = diff / vega;
    sigma = sigma - step;
    if (sigma <= 0.01) sigma = 0.01;
    if (sigma >= 3.0) sigma = 3.0;
  }

  // Bisection fallback if Newton failed
  let low = 0.01;
  let high = 2.5;
  for (let i = 0; i < 20; i++) {
    const mid = (low + high) / 2;
    const price = bsPrice(spot, strike, tYears, mid, kind, r);
    if (Math.abs(price - marketPrice) < 0.05) return mid;
    if (price < marketPrice) low = mid;
    else high = mid;
  }

  return (low + high) / 2;
}

export interface StrategyLeg {
  id: string;
  enabled: boolean;
  kind: "CE" | "PE" | "FUT";
  side: "B" | "S";
  strike: number; // 0 for FUT
  expiry: string; // ISO date YYYY-MM-DD
  lots: number;
  lotSize: number;
  price: number; // Entry price (defaults to live LTP)
  key: string;
  symbol: string;
  underlying: string;
  ltp?: number;
  iv?: number; // In decimal, e.g. 0.18 for 18%
}

export interface PayoffPoint {
  price: number;
  pctChange: number;
  pnlExpiry: number;
  pnlTargetDate: number;
  delta?: number;
}

export interface StrategyMetrics {
  maxProfit: number | null; // null = Unlimited
  maxLoss: number | null;   // null = Unlimited
  maxProfitPct: number | null;
  maxLossPct: number | null;
  riskRewardRatio: string;
  breakevens: number[];
  pop: number; // 0 to 100%
  netPremium: number; // Positive = credit, negative = debit
  isCredit: boolean;
  timeValue: number;
  intrinsicValue: number;
  marginRequired: number;
  marginAvailable: number;
  netDelta: number;
  netGamma: number;
  netTheta: number; // Daily in ₹
  netVega: number;  // Per 1% IV in ₹
  projectedPnl: number; // At target price & target date
}

/** Calculate payoff for a single leg at given underlying price and time remaining. */
export function legPayoff(
  leg: StrategyLeg,
  spotTarget: number,
  tRemainingYears: number,
  ivShift = 0,
  isAtExpiry = false
): number {
  if (!leg.enabled) return 0;
  const mult = (leg.side === "B" ? 1 : -1) * leg.lots * leg.lotSize;

  if (leg.kind === "FUT") {
    // Future payoff is linear: (S_target - entryPrice) * mult
    return (spotTarget - leg.price) * mult;
  }

  if (isAtExpiry || tRemainingYears <= 1e-6) {
    const intrinsic = leg.kind === "CE" ? Math.max(spotTarget - leg.strike, 0) : Math.max(leg.strike - spotTarget, 0);
    return (intrinsic - leg.price) * mult;
  }

  // Theoretical Black-Scholes price before expiry
  const sigma = Math.max(0.01, (leg.iv ?? 0.16) + ivShift);
  const optPrice = bsPrice(spotTarget, leg.strike, tRemainingYears, sigma, leg.kind);
  return (optPrice - leg.price) * mult;
}

/** Generate dual payoff curve points (On Expiry & On Target Date). */
export function generatePayoffCurve(
  legs: StrategyLeg[],
  currentSpot: number,
  targetDate: Date,
  ivShift = 0,
  customRange?: { min: number; max: number }
): { points: PayoffPoint[]; minPrice: number; maxPrice: number } {
  const activeLegs = legs.filter((l) => l.enabled);
  if (!activeLegs.length || currentSpot <= 0) {
    return { points: [], minPrice: currentSpot * 0.9, maxPrice: currentSpot * 1.1 };
  }

  // Determine price bounds based on strikes, futures, or default +/- 8%
  let minStrike = currentSpot;
  let maxStrike = currentSpot;
  for (const l of activeLegs) {
    if (l.strike > 0) {
      if (l.strike < minStrike) minStrike = l.strike;
      if (l.strike > maxStrike) maxStrike = l.strike;
    }
  }

  const rangeSpan = Math.max(maxStrike - minStrike, currentSpot * 0.12);
  const minPrice = customRange ? customRange.min : Math.floor(Math.min(minStrike - rangeSpan * 0.4, currentSpot * 0.92));
  const maxPrice = customRange ? customRange.max : Math.ceil(Math.max(maxStrike + rangeSpan * 0.4, currentSpot * 1.08));

  const steps = 140;
  const stepSize = (maxPrice - minPrice) / steps;
  const nowMs = Date.now();
  const targetMs = targetDate.getTime();

  const points: PayoffPoint[] = [];

  for (let i = 0; i <= steps; i++) {
    const p = Math.round((minPrice + i * stepSize) * 10) / 10;
    let pnlExp = 0;
    let pnlTgt = 0;

    for (const leg of activeLegs) {
      const expDate = new Date(leg.expiry + "T15:30:00+05:30").getTime();
      const tExpYears = Math.max(0, (expDate - nowMs) / (365 * 24 * 3600 * 1000));
      const tTgtYears = Math.max(0, (expDate - targetMs) / (365 * 24 * 3600 * 1000));

      pnlExp += legPayoff(leg, p, tExpYears, ivShift, true);
      pnlTgt += legPayoff(leg, p, tTgtYears, ivShift, false);
    }

    const pctChange = ((p - currentSpot) / currentSpot) * 100;
    points.push({
      price: p,
      pctChange: Math.round(pctChange * 100) / 100,
      pnlExpiry: Math.round(pnlExp),
      pnlTargetDate: Math.round(pnlTgt),
    });
  }

  return { points, minPrice, maxPrice };
}

/** Calculate strategy analytics metrics (Max Profit, Max Loss, Breakevens, POP, Greeks). */
export function calculateStrategyMetrics(
  legs: StrategyLeg[],
  currentSpot: number,
  targetPrice: number,
  targetDate: Date,
  ivShift = 0,
  marginAvailable = 100000
): StrategyMetrics {
  const activeLegs = legs.filter((l) => l.enabled);
  if (!activeLegs.length || currentSpot <= 0) {
    return {
      maxProfit: 0,
      maxLoss: 0,
      maxProfitPct: 0,
      maxLossPct: 0,
      riskRewardRatio: "–",
      breakevens: [],
      pop: 0,
      netPremium: 0,
      isCredit: false,
      timeValue: 0,
      intrinsicValue: 0,
      marginRequired: 0,
      marginAvailable,
      netDelta: 0,
      netGamma: 0,
      netTheta: 0,
      netVega: 0,
      projectedPnl: 0,
    };
  }

  // Net Premium, Time Value & Intrinsic Value
  let netPremium = 0;
  let totalTimeValue = 0;
  let totalIntrinsicValue = 0;
  let estimatedMargin = 0;

  let netDelta = 0;
  let netGamma = 0;
  let netTheta = 0;
  let netVega = 0;

  const nowMs = Date.now();

  for (const leg of activeLegs) {
    const mult = (leg.side === "B" ? 1 : -1) * leg.lots * leg.lotSize;
    // Premium paid or received
    // Buying costs money (negative net cash flow), Selling gets money (positive)
    const legCashflow = (leg.side === "S" ? 1 : -1) * leg.price * leg.lots * leg.lotSize;
    netPremium += legCashflow;

    const expDate = new Date(leg.expiry + "T15:30:00+05:30").getTime();
    const tYears = Math.max(0, (expDate - nowMs) / (365 * 24 * 3600 * 1000));
    const sigma = Math.max(0.01, (leg.iv ?? 0.16) + ivShift);

    if (leg.kind === "FUT") {
      netDelta += (leg.side === "B" ? 1 : -1) * leg.lots * leg.lotSize;
      // Futures margin estimate: ~11% of contract value
      estimatedMargin += currentSpot * leg.lots * leg.lotSize * 0.11;
    } else {
      const intrinsic = leg.kind === "CE" ? Math.max(currentSpot - leg.strike, 0) : Math.max(leg.strike - currentSpot, 0);
      const timeVal = Math.max(0, leg.price - intrinsic);

      totalIntrinsicValue += intrinsic * leg.lots * leg.lotSize;
      totalTimeValue += timeVal * leg.lots * leg.lotSize;

      const g = bsGreeks(currentSpot, leg.strike, tYears, sigma, leg.kind);
      netDelta += (leg.side === "B" ? 1 : -1) * g.delta * leg.lots * leg.lotSize;
      netGamma += (leg.side === "B" ? 1 : -1) * g.gamma * leg.lots * leg.lotSize;
      netTheta += (leg.side === "B" ? 1 : -1) * g.theta * leg.lots * leg.lotSize;
      netVega += (leg.side === "B" ? 1 : -1) * g.vega * leg.lots * leg.lotSize;

      if (leg.side === "B") {
        estimatedMargin += leg.price * leg.lots * leg.lotSize; // Full premium
      } else {
        // Selling margin estimate: SPAN + Exposure approx 12-14% of underlying
        estimatedMargin += currentSpot * leg.lots * leg.lotSize * 0.12;
      }
    }
  }

  // Margin benefit adjustment for hedged spreads (max loss capped)
  estimatedMargin = Math.round(Math.max(estimatedMargin * 0.7, 5000));

  // Sample wide range to find Max Profit, Max Loss, and Breakevens
  // Test from 0.4x spot to 2.0x spot
  const testMin = Math.max(1, currentSpot * 0.4);
  const testMax = currentSpot * 2.0;
  const testSteps = 800;
  const testStepSize = (testMax - testMin) / testSteps;

  let minPnl = Infinity;
  let maxPnl = -Infinity;
  const breakevens: number[] = [];

  let prevPnl: number | null = null;
  let prevPrice: number | null = null;

  for (let i = 0; i <= testSteps; i++) {
    const p = testMin + i * testStepSize;
    let pnl = 0;
    for (const leg of activeLegs) {
      pnl += legPayoff(leg, p, 0, ivShift, true);
    }

    if (pnl < minPnl) minPnl = pnl;
    if (pnl > maxPnl) maxPnl = pnl;

    // Detect zero crossings for breakevens
    if (prevPnl !== null && prevPrice !== null) {
      if ((prevPnl < 0 && pnl >= 0) || (prevPnl > 0 && pnl <= 0)) {
        // Linear interpolation
        const fraction = Math.abs(prevPnl) / (Math.abs(prevPnl) + Math.abs(pnl));
        const be = Math.round(prevPrice + fraction * (p - prevPrice));
        if (!breakevens.some((b) => Math.abs(b - be) < currentSpot * 0.005)) {
          breakevens.push(be);
        }
      }
    }
    prevPnl = pnl;
    prevPrice = p;
  }

  // Check slope at edges to detect unlimited profit or loss
  const slopeLeft =
    legPayoff(activeLegs[0], testMin + 10, 0, ivShift, true) - legPayoff(activeLegs[0], testMin, 0, ivShift, true);
  const slopeRight =
    legPayoff(activeLegs[0], testMax, 0, ivShift, true) - legPayoff(activeLegs[0], testMax - 10, 0, ivShift, true);

  // If any naked call/put/future causes unbounded slope
  let hasUnlimitedProfit = false;
  let hasUnlimitedLoss = false;

  const netFut = activeLegs.reduce((acc, l) => (l.kind === "FUT" ? acc + (l.side === "B" ? 1 : -1) * l.lots : acc), 0);
  const netCall = activeLegs.reduce(
    (acc, l) => (l.kind === "CE" ? acc + (l.side === "B" ? 1 : -1) * l.lots : acc),
    0
  );
  const netPut = activeLegs.reduce(
    (acc, l) => (l.kind === "PE" ? acc + (l.side === "B" ? 1 : -1) * l.lots : acc),
    0
  );

  if (netFut > 0 || netCall > 0) hasUnlimitedProfit = true;
  if (netFut < 0 || netCall < 0) hasUnlimitedLoss = true;
  if (netPut < 0) hasUnlimitedLoss = true;
  if (netPut > 0 && testMin <= 1) hasUnlimitedProfit = false; // Put profit is capped at strike * qty when price hits 0

  const maxProfit = hasUnlimitedProfit ? null : Math.round(maxPnl);
  const maxLoss = hasUnlimitedLoss ? null : Math.round(minPnl);

  const maxProfitPct = maxProfit !== null && estimatedMargin > 0 ? (maxProfit / estimatedMargin) * 100 : null;
  const maxLossPct = maxLoss !== null && estimatedMargin > 0 ? (maxLoss / estimatedMargin) * 100 : null;

  // Reward / Risk
  let riskRewardRatio = "–";
  if (maxProfit !== null && maxLoss !== null && maxLoss !== 0) {
    const ratio = Math.abs(maxProfit / maxLoss);
    riskRewardRatio = ratio.toFixed(2);
  } else if (hasUnlimitedProfit && maxLoss !== null) {
    riskRewardRatio = "Unlimited";
  }

  // Probability of Profit (POP)
  // Integrate standard normal probability density for all prices where pnl > 0 at expiry
  const avgIV =
    activeLegs.reduce((acc, l) => acc + (l.iv ?? 0.16), 0) / activeLegs.length + ivShift;
  const frontExp = activeLegs[0].expiry;
  const frontExpMs = new Date(frontExp + "T15:30:00+05:30").getTime();
  const tExpYears = Math.max(0.001, (frontExpMs - nowMs) / (365 * 24 * 3600 * 1000));
  const sigmaRootT = Math.max(0.005, avgIV * Math.sqrt(tExpYears));

  let profitableDensity = 0;
  let totalDensity = 0;

  for (let i = 0; i <= testSteps; i++) {
    const p = testMin + i * testStepSize;
    let pnl = 0;
    for (const leg of activeLegs) {
      pnl += legPayoff(leg, p, 0, ivShift, true);
    }

    // Lognormal density
    const z = (Math.log(p / currentSpot) - (RISK_FREE_RATE - 0.5 * avgIV * avgIV) * tExpYears) / sigmaRootT;
    const density = pdf(z);

    totalDensity += density;
    if (pnl > 0) {
      profitableDensity += density;
    }
  }

  const pop = totalDensity > 0 ? Math.round((profitableDensity / totalDensity) * 100) : 50;

  // Projected PnL at selected target price & target date
  const targetMs = targetDate.getTime();
  let projectedPnl = 0;
  for (const leg of activeLegs) {
    const expDate = new Date(leg.expiry + "T15:30:00+05:30").getTime();
    const tTgtYears = Math.max(0, (expDate - targetMs) / (365 * 24 * 3600 * 1000));
    projectedPnl += legPayoff(leg, targetPrice, tTgtYears, ivShift, false);
  }

  return {
    maxProfit,
    maxLoss,
    maxProfitPct: maxProfitPct !== null ? Math.round(maxProfitPct * 10) / 10 : null,
    maxLossPct: maxLossPct !== null ? Math.round(maxLossPct * 10) / 10 : null,
    riskRewardRatio,
    breakevens: breakevens.sort((a, b) => a - b),
    pop: Math.min(99, Math.max(1, pop)),
    netPremium: Math.round(netPremium),
    isCredit: netPremium > 0,
    timeValue: Math.round(totalTimeValue),
    intrinsicValue: Math.round(totalIntrinsicValue),
    marginRequired: estimatedMargin,
    marginAvailable,
    netDelta: Math.round(netDelta * 100) / 100,
    netGamma: Math.round(netGamma * 10000) / 10000,
    netTheta: Math.round(netTheta),
    netVega: Math.round(netVega),
    projectedPnl: Math.round(projectedPnl),
  };
}
