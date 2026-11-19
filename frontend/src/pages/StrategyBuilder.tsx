import { useState, useEffect, useMemo, useRef } from "react";
import { api } from "../lib/api";
import { toast, useSession, useTitle } from "../lib/app";
import { expiryLabel, daysTo, useChainSel, setChainSel, type ChainData } from "../lib/chain";
import { usePoll } from "../lib/store";
import { useWant, getQuote } from "../lib/quotes";
import { px, rupees, uuid } from "../lib/format";
import {
  calcIV,
  generatePayoffCurve,
  calculateStrategyMetrics,
  type StrategyLeg,
  type StrategyMetrics,
} from "../lib/blackScholes";
import {
  READY_MADE_STRATEGIES,
  type ReadyMadeStrategy,
  type StrategyCategory,
} from "../lib/readyMadeStrategies";
import { PayoffChart } from "../components/PayoffChart";
import { StrategyStrikeModal } from "../components/StrategyStrikeModal";
import { UnderlyingSearch } from "../components/UnderlyingSearch";
import { Empty, Modal } from "../components/ui";

const QUICK_UNDERLYINGS = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "FINNIFTY",
  "MIDCPNIFTY",
  "CRUDEOILM",
  "SILVERM",
  "GOLDM",
];

interface PositionItem {
  key: string;
  symbol: string;
  kind?: string;
  strike?: number;
  expiry?: string;
  qty: number;
  lot: number;
  buy_price: number;
  sell_price: number;
  net_price: number;
}

export function StrategyBuilder() {
  useTitle("Strategy builder");
  const sess = useSession();
  const sel = useChainSel();
  const underlying = sel.underlying || "NIFTY";

  // Fetch Option Chain for underlying
  const { data: chainData, reload: reloadChain } = usePoll<ChainData>(
    `/chain?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(sel.expiry || "")}&window=0`,
    5000
  );

  const spot = chainData?.spot ?? 0;
  const atm = chainData?.atm ?? 0;
  const expiries = chainData?.expiries ?? [];
  const selectedExpiry = chainData?.expiry || sel.expiry || expiries[0] || "";
  const lotSize = chainData?.lot ?? 1;

  // Active Strategy Legs
  const [legs, setLegs] = useState<StrategyLeg[]>([]);

  // Simulation Sliders
  const [targetPrice, setTargetPrice] = useState<number>(spot);
  const [targetDateOffset, setTargetDateOffset] = useState<number>(0); // 0 = now, 1 = expiry
  const [ivShift, setIvShift] = useState<number>(0); // in decimal, e.g. 0.02 = +2%

  // Sub-tabs in Payoff area
  const [payoffTab, setPayoffTab] = useState<"graph" | "table" | "greeks">("graph");

  // Bottom Tabs (Ready-made / Positions / Saved)
  const [bottomTab, setBottomTab] = useState<"readymade" | "positions" | "saved">("readymade");
  const [categoryFilter, setCategoryFilter] = useState<StrategyCategory>("bullish");

  // Strike Selector Modal state
  const [modalOpen, setModalOpen] = useState(false);

  // Positions for import
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [loadingPositions, setLoadingPositions] = useState(false);

  // Saved strategies
  const [savedStrategies, setSavedStrategies] = useState<{ id: string; name: string; date: string; legs: StrategyLeg[] }[]>(() => {
    try {
      const stored = localStorage.getItem("nt-saved-strategies");
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  // Trade All order execution
  const connectedAccounts = sess?.accounts && sess.accounts.length > 0 ? sess.accounts : sess?.account ? [sess.account] : [];
  const [tradeAllOpen, setTradeAllOpen] = useState(false);
  const [tradeOrderType, setTradeOrderType] = useState<"MKT" | "L">("MKT");
  const [tradeAccount, setTradeAccount] = useState<string>(() => {
    return sess?.active_account_id || (connectedAccounts[0]?.id ?? "default");
  });
  const [tradeProduct, setTradeProduct] = useState<"NRML" | "MIS">("NRML");
  const [tradingBusy, setTradingBusy] = useState(false);

  useEffect(() => {
    if (!tradeAccount || tradeAccount === "default") {
      const def = sess?.active_account_id || (connectedAccounts[0]?.id ?? "");
      if (def) setTradeAccount(def);
    }
  }, [sess?.active_account_id, connectedAccounts]);

  // Update target price default when spot first loads
  useEffect(() => {
    if (spot > 0 && (!targetPrice || targetPrice === 0)) {
      setTargetPrice(spot);
    }
  }, [spot]);

  // Subscribe quotes for all active leg keys
  const legKeys = useMemo(() => legs.map((l) => l.key).filter(Boolean), [legs]);
  useWant(legKeys);

  // Keep live LTP and calculated IV updated on active legs
  useEffect(() => {
    const updatePrices = () => {
      setLegs((curr) =>
        curr.map((l) => {
          const q = getQuote(l.key);
          const livePrice = q?.l;
          if (livePrice && livePrice > 0) {
            const expDate = new Date(l.expiry + "T15:30:00+05:30").getTime();
            const tYears = Math.max(0.0001, (expDate - Date.now()) / (365 * 24 * 3600 * 1000));
            const calculatedIv =
              l.kind !== "FUT" && spot > 0
                ? calcIV(livePrice, spot, l.strike, tYears, l.kind)
                : 0;

            return {
              ...l,
              ltp: livePrice,
              // If entry price wasn't modified yet, follow live price
              price: l.price === 0 ? livePrice : l.price,
              iv: calculatedIv > 0 ? calculatedIv : l.iv,
            };
          }
          return l;
        })
      );
    };

    const timer = setInterval(updatePrices, 1500);
    return () => clearInterval(timer);
  }, [spot]);

  // Calculate Target Date object based on slider
  const targetDate = useMemo(() => {
    const now = Date.now();
    const expMs = selectedExpiry
      ? new Date(selectedExpiry + "T15:30:00+05:30").getTime()
      : now + 86400000;
    const diff = Math.max(0, expMs - now);
    return new Date(now + diff * targetDateOffset);
  }, [selectedExpiry, targetDateOffset]);

  // Payoff curves & Analytics metrics
  const { points } = useMemo(() => {
    return generatePayoffCurve(legs, spot, targetDate, ivShift);
  }, [legs, spot, targetDate, ivShift]);

  const metrics: StrategyMetrics = useMemo(() => {
    return calculateStrategyMetrics(
      legs,
      spot,
      targetPrice || spot,
      targetDate,
      ivShift,
      100000
    );
  }, [legs, spot, targetPrice, targetDate, ivShift]);

  // Open Interest data for chart background
  const oiData = useMemo(() => {
    if (!chainData?.rows) return [];
    return chainData.rows
      .filter((r) => r.ce?.oi || r.pe?.oi)
      .map((r) => ({
        strike: r.strike,
        callOi: r.ce?.oi ?? 0,
        putOi: r.pe?.oi ?? 0,
      }));
  }, [chainData?.rows]);

  // Handlers for legs
  const handleToggleLeg = (newLeg: Omit<StrategyLeg, "id">) => {
    setLegs((prev) => {
      const idx = prev.findIndex(
        (l) => l.kind === newLeg.kind && l.strike === newLeg.strike && l.expiry === newLeg.expiry
      );
      if (idx >= 0) {
        // If clicking same side -> remove it
        if (prev[idx].side === newLeg.side) {
          return prev.filter((_, i) => i !== idx);
        }
        // If clicking opposite side -> flip side
        return prev.map((l, i) => (i === idx ? { ...l, side: newLeg.side } : l));
      }
      // Add new leg
      return [...prev, { ...newLeg, id: uuid() }];
    });
  };

  const handleRemoveLeg = (id: string) => {
    setLegs((prev) => prev.filter((l) => l.id !== id));
  };

  const handleUpdateLeg = (id: string, updates: Partial<StrategyLeg>) => {
    setLegs((prev) => prev.map((l) => (l.id === id ? { ...l, ...updates } : l)));
  };

  const handleClearAll = () => {
    setLegs([]);
  };

  const handleResetPrices = () => {
    setLegs((prev) =>
      prev.map((l) => ({
        ...l,
        price: l.ltp && l.ltp > 0 ? l.ltp : l.price,
      }))
    );
  };

  // Load a ready-made strategy
  const handleApplyReadyMade = (strat: ReadyMadeStrategy) => {
    if (!chainData || !chainData.rows.length) {
      toast("Please wait for option chain to load", "err");
      return;
    }
    const strikes = chainData.rows.map((r) => r.strike).sort((a, b) => a - b);
    const nextExp = expiries.length > 1 ? expiries[1] : selectedExpiry;

    const templates = strat.createLegs(
      strikes,
      atm,
      selectedExpiry,
      nextExp,
      lotSize,
      underlying
    );

    const built: StrategyLeg[] = [];
    for (const t of templates) {
      let ltp = spot;
      let key = `fake|${t.underlying}_${t.strike}`;
      let symbol = `${t.underlying} ${t.expiry} ${t.kind === "FUT" ? "FUT" : t.strike + " " + t.kind}`;

      if (t.kind === "FUT") {
        ltp = spot;
        key = `nse_fo|${t.underlying}FUT`;
      } else {
        const row = chainData.rows.find((r) => r.strike === t.strike);
        const legData = t.kind === "CE" ? row?.ce : row?.pe;
        if (legData) {
          ltp = legData.ltp ?? 0;
          key = legData.key;
          symbol = legData.symbol;
        }
      }

      built.push({
        ...t,
        id: uuid(),
        price: ltp > 0 ? ltp : 10,
        ltp,
        key,
        symbol,
        iv: 0.16,
      });
    }

    setLegs(built);
    toast(`Loaded ${strat.name}`);
  };

  // Fetch broker positions
  const handleLoadPositions = async () => {
    setLoadingPositions(true);
    try {
      const res = await api<{ ok: boolean; positions: PositionItem[] }>("/positions");
      if (res.ok && res.positions) {
        // Filter F&O positions for current underlying
        const foPositions = res.positions.filter(
          (p) => p.qty !== 0 && (p.key.startsWith("nse_fo|") || p.key.startsWith("mcx_fo|") || p.key.startsWith("bse_fo|"))
        );
        setPositions(foPositions);
      }
    } catch {
      toast("Could not load positions", "err");
    } finally {
      setLoadingPositions(false);
    }
  };

  const handleImportPosition = (p: PositionItem) => {
    const isBuy = p.qty > 0;
    const absQty = Math.abs(p.qty);
    const lot = p.lot || lotSize || 1;
    const lots = Math.max(1, Math.round(absQty / lot));
    const kind = (p.kind === "CE" || p.kind === "PE" ? p.kind : "FUT") as "CE" | "PE" | "FUT";

    const newLeg: StrategyLeg = {
      id: uuid(),
      enabled: true,
      kind,
      side: isBuy ? "B" : "S",
      strike: p.strike ?? 0,
      expiry: p.expiry || selectedExpiry,
      lots,
      lotSize: lot,
      price: p.net_price || p.buy_price || p.sell_price || 10,
      key: p.key,
      symbol: p.symbol,
      underlying,
      ltp: p.net_price,
      iv: 0.16,
    };

    setLegs((prev) => [...prev, newLeg]);
    toast(`Imported ${p.symbol}`);
  };

  // Save strategy to local storage
  const handleSaveStrategy = () => {
    if (!saveName.trim() || !legs.length) return;
    const newEntry = {
      id: uuid(),
      name: saveName.trim(),
      date: new Date().toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }),
      legs,
    };
    const updated = [newEntry, ...savedStrategies];
    setSavedStrategies(updated);
    try {
      localStorage.setItem("nt-saved-strategies", JSON.stringify(updated));
    } catch {
      /* ignore storage limit */
    }
    setSaveName("");
    setSaveModalOpen(false);
    toast("Strategy saved successfully");
  };

  const handleDeleteSaved = (id: string) => {
    const updated = savedStrategies.filter((s) => s.id !== id);
    setSavedStrategies(updated);
    try {
      localStorage.setItem("nt-saved-strategies", JSON.stringify(updated));
    } catch {
      /* ignore */
    }
    toast("Saved strategy removed");
  };

  const handleLoadSaved = (savedLegs: StrategyLeg[]) => {
    setLegs(savedLegs);
    toast("Loaded saved strategy");
  };

  // Execute Trade All orders
  const handleExecuteTradeAll = async () => {
    const active = legs.filter((l) => l.enabled);
    if (!active.length) {
      toast("No active legs to trade", "err");
      return;
    }

    setTradingBusy(true);
    let successCount = 0;
    let failCount = 0;
    let lastError = "";

    const resolvedAccount =
      tradeAccount && tradeAccount !== "default"
        ? tradeAccount
        : sess?.active_account_id || (connectedAccounts[0]?.id ?? "");

    for (const leg of active) {
      try {
        const body = {
          key: leg.key,
          side: leg.side,
          lots: leg.lots || 1,
          qty: (leg.lots || 1) * (leg.lotSize || 1),
          product: tradeProduct,
          order_type: tradeOrderType,
          price: tradeOrderType === "MKT" ? 0 : (Number(leg.price) || 0),
          trigger: 0,
          validity: "DAY",
          account_id: resolvedAccount,
          client_id: uuid(),
        };

        const res = await api<{ ok: boolean; order_id?: string; error?: string }>("/orders", {
          method: "POST",
          body,
        });

        if (res && res.ok !== false) {
          successCount++;
        } else {
          failCount++;
          if (res?.error) lastError = res.error;
        }
      } catch (err: any) {
        failCount++;
        lastError = err?.message || "Order rejected";
      }
    }

    setTradingBusy(false);
    setTradeAllOpen(false);

    if (failCount === 0) {
      toast(`Successfully placed ${successCount} orders!`, "info");
    } else {
      toast(lastError ? `Order failed: ${lastError}` : `Placed ${successCount} orders, ${failCount} failed. Check Orders page.`, "err");
    }
  };

  return (
    <div className="strategy-builder-page stack gap-3" style={{ padding: "16px 20px", maxWidth: 1600, margin: "0 auto" }}>
      {/* ────────────────────── TOP HEADER & CONTROLS ────────────────────── */}
      <div className="row wrap justify-between align-center gap-3 panel" style={{ padding: "12px 18px", border: "1px solid var(--line)" }}>
        <div className="row wrap gap-3 align-center">
          <div className="seg" role="group" aria-label="Quick underlyings">
            {QUICK_UNDERLYINGS.map((name) => (
              <button
                key={name}
                aria-pressed={underlying === name}
                onClick={() => setChainSel({ underlying: name })}
              >
                {name}
              </button>
            ))}
          </div>

          <UnderlyingSearch />

          <div className="row gap-2 align-center" style={{ marginLeft: 8 }}>
            <span style={{ fontSize: "18px", fontWeight: 700 }}>
              {underlying} <span style={{ color: "var(--accent)" }}>{spot ? spot.toLocaleString("en-IN") : "–"}</span>
            </span>
          </div>
        </div>

        <div className="row gap-2 align-center">
          <button
            className="btn primary"
            style={{ fontWeight: 600, gap: 6 }}
            onClick={() => setModalOpen(true)}
          >
            <span>+</span> Strikes & Futures
          </button>

          {legs.length > 0 && (
            <>
              <button className="btn" onClick={() => setSaveModalOpen(true)}>
                Save Strategy
              </button>
              <button className="btn danger" onClick={handleClearAll}>
                Clear
              </button>
            </>
          )}
        </div>
      </div>

      {/* ────────────────────── ACTIVE LEGS & PAYOFF ANALYSIS ────────────────────── */}
      {legs.length > 0 ? (
        <div
          className="strategy-builder-layout"
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(360px, 440px) 1fr",
            gap: "16px",
            alignItems: "start",
          }}
        >
          {/* LEFT: LEGS MANAGEMENT TABLE */}
          <div className="panel stack gap-3" style={{ padding: "16px", border: "1px solid var(--line)" }}>
            <div className="row justify-between align-center">
              <h3 style={{ margin: 0, fontSize: "16px" }}>
                Strategy Legs ({legs.filter((l) => l.enabled).length}/{legs.length})
              </h3>
              <div className="row gap-2">
                <button className="btn xs" onClick={handleResetPrices} title="Reset all prices to live LTP">
                  Reset Prices
                </button>
                <button className="btn xs primary" onClick={() => setModalOpen(true)}>
                  + Add Leg
                </button>
              </div>
            </div>

            {/* Order Type Selector (Market default / Limit option) */}
            <div
              className="row justify-between align-center"
              style={{
                background: "var(--panel-alt, rgba(0,0,0,0.03))",
                padding: "6px 10px",
                borderRadius: "6px",
                border: "1px solid var(--line)",
              }}
            >
              <div className="stack" style={{ gap: 1 }}>
                <span style={{ fontSize: "12px", fontWeight: 700 }}>Order Type</span>
                <span className="mini muted" style={{ fontSize: "10px" }}>
                  {tradeOrderType === "MKT" ? "Market price execution" : "Specified limit price"}
                </span>
              </div>
              <div className="seg" role="group">
                <button
                  type="button"
                  aria-pressed={tradeOrderType === "MKT"}
                  onClick={() => setTradeOrderType("MKT")}
                  style={{
                    padding: "3px 10px",
                    fontSize: "11px",
                    fontWeight: tradeOrderType === "MKT" ? 700 : 500,
                  }}
                  title="Market Order (Default) - Orders execute immediately at market rate"
                >
                  Market (MKT)
                </button>
                <button
                  type="button"
                  aria-pressed={tradeOrderType === "L"}
                  onClick={() => setTradeOrderType("L")}
                  style={{
                    padding: "3px 10px",
                    fontSize: "11px",
                    fontWeight: tradeOrderType === "L" ? 700 : 500,
                  }}
                  title="Limit Order - Orders placed with your specified limit prices"
                >
                  Limit (L)
                </button>
              </div>
            </div>

            {/* List of Legs */}
            <div className="stack gap-2" style={{ maxHeight: "480px", overflowY: "auto" }}>
              {legs.map((leg) => {
                const liveLtp = leg.ltp ?? getQuote(leg.key)?.l;
                return (
                  <div
                    key={leg.id}
                    className="row justify-between align-center"
                    style={{
                      padding: "8px 10px",
                      background: leg.enabled ? "var(--panel-alt, rgba(0,0,0,0.02))" : "var(--panel)",
                      opacity: leg.enabled ? 1 : 0.45,
                      border: "1px solid var(--line)",
                      borderRadius: "6px",
                      gap: 8,
                    }}
                  >
                    {/* Checkbox */}
                    <input
                      type="checkbox"
                      checked={leg.enabled}
                      onChange={(e) => handleUpdateLeg(leg.id, { enabled: e.target.checked })}
                      title="Enable/disable in simulation"
                    />

                    {/* Side Toggle Button */}
                    <button
                      className={`btn xs ${leg.side === "B" ? "primary solid" : "danger solid"}`}
                      style={{
                        padding: "3px 8px",
                        fontSize: "11px",
                        fontWeight: 700,
                        background: leg.side === "B" ? "#2563eb" : "#dc2626",
                        color: "#ffffff",
                        borderColor: leg.side === "B" ? "#2563eb" : "#dc2626",
                      }}
                      onClick={() => handleUpdateLeg(leg.id, { side: leg.side === "B" ? "S" : "B" })}
                      title="Click to flip Buy/Sell"
                    >
                      {leg.side === "B" ? "BUY" : "SELL"}
                    </button>

                    {/* Lots Stepper */}
                    <div className="row align-center gap-1">
                      <button
                        className="btn xs"
                        style={{ padding: "2px 6px" }}
                        disabled={leg.lots <= 1}
                        onClick={() => handleUpdateLeg(leg.id, { lots: Math.max(1, leg.lots - 1) })}
                      >
                        -
                      </button>
                      <span style={{ fontSize: "12px", fontWeight: 600, minWidth: 18, textAlign: "center" }}>
                        {leg.lots}
                      </span>
                      <button
                        className="btn xs"
                        style={{ padding: "2px 6px" }}
                        onClick={() => handleUpdateLeg(leg.id, { lots: leg.lots + 1 })}
                      >
                        +
                      </button>
                    </div>

                    {/* Contract Details */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "12px", fontWeight: 600, textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>
                        {leg.kind === "FUT" ? `${underlying} FUT` : `${leg.strike} ${leg.kind}`}
                      </div>
                      <div className="mini muted" style={{ fontSize: "10px" }}>
                        {expiryLabel(leg.expiry)} · {leg.lots * leg.lotSize} qty
                      </div>
                    </div>

                    {/* Price Input or MKT Badge */}
                    <div style={{ width: 80, textAlign: "right" }}>
                      {tradeOrderType === "MKT" ? (
                        <div className="stack align-end" style={{ gap: 2 }}>
                          <span
                            style={{
                              fontSize: "10px",
                              padding: "1px 6px",
                              fontWeight: 700,
                              background: "rgba(37,99,235,0.12)",
                              color: "#2563eb",
                              border: "1px solid rgba(37,99,235,0.3)",
                              borderRadius: "4px",
                              display: "inline-block",
                            }}
                          >
                            MKT
                          </span>
                          <span className="mini muted" style={{ fontSize: "10px", fontWeight: 600 }}>
                            {liveLtp ? `₹${px(liveLtp)}` : `₹${px(leg.price)}`}
                          </span>
                        </div>
                      ) : (
                        <>
                          <input
                            className="input sm num"
                            style={{ width: "100%", padding: "2px 6px", fontSize: "12px", textAlign: "right" }}
                            type="number"
                            step="any"
                            value={leg.price}
                            onChange={(e) => handleUpdateLeg(leg.id, { price: Number(e.target.value) || 0 })}
                            title="Limit price"
                          />
                          {liveLtp && liveLtp !== leg.price && (
                            <div className="mini muted" style={{ fontSize: "10px" }}>
                              LTP: {px(liveLtp)}
                            </div>
                          )}
                        </>
                      )}
                    </div>

                    {/* Delete button */}
                    <button
                      className="btn xs icon danger"
                      style={{ padding: "4px" }}
                      onClick={() => handleRemoveLeg(leg.id)}
                      title="Remove leg"
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>

            {/* Bottom Premium Summary & Trade All Button */}
            <div style={{ borderTop: "1px solid var(--line)", paddingTop: "12px" }} className="stack gap-2">
              <div className="row justify-between align-center">
                <span className="muted" style={{ fontSize: "13px" }}>Net Premium:</span>
                <b style={{ fontSize: "15px", color: metrics.isCredit ? "#22c55e" : "var(--fg)" }}>
                  {metrics.isCredit
                    ? `Get ${rupees(Math.abs(metrics.netPremium))} (Credit)`
                    : `Pay ${rupees(Math.abs(metrics.netPremium))} (Debit)`}
                </b>
              </div>

              <div className="row justify-between align-center">
                <span className="muted" style={{ fontSize: "13px" }}>Estimated Margin:</span>
                <b style={{ fontSize: "14px" }}>{rupees(metrics.marginRequired)}</b>
              </div>

              <button
                className="btn primary solid"
                style={{
                  width: "100%",
                  height: "40px",
                  fontSize: "15px",
                  fontWeight: 700,
                  marginTop: "6px",
                  background: "var(--accent)",
                  borderColor: "var(--accent)",
                  color: "#ffffff",
                }}
                onClick={() => setTradeAllOpen(true)}
              >
                Trade All ({legs.filter((l) => l.enabled).length} {tradeOrderType === "MKT" ? "Market" : "Limit"} Orders)
              </button>
            </div>
          </div>

          {/* RIGHT: STRATEGY METRICS & PAYOFF ANALYZER */}
          <div className="stack gap-3">
            {/* Top Metrics Cards (Sensibull Style) */}
            <div
              className="strategy-metrics-grid"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
                gap: "10px",
              }}
            >
              {/* Max Profit */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>Max Profit</span>
                <span className="v" style={{ fontSize: "16px", fontWeight: 700, color: metrics.maxProfit !== null ? "#22c55e" : "var(--fg)" }}>
                  {metrics.maxProfit !== null
                    ? `+${rupees(metrics.maxProfit)} ${metrics.maxProfitPct !== null ? `(+${metrics.maxProfitPct}%)` : ""}`
                    : "Unlimited"}
                </span>
              </div>

              {/* Max Loss */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>Max Loss</span>
                <span className="v" style={{ fontSize: "16px", fontWeight: 700, color: metrics.maxLoss !== null ? "#ef4444" : "var(--fg)" }}>
                  {metrics.maxLoss !== null
                    ? `-${rupees(Math.abs(metrics.maxLoss))} ${metrics.maxLossPct !== null ? `(${metrics.maxLossPct}%)` : ""}`
                    : "Unlimited"}
                </span>
              </div>

              {/* Reward / Risk */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>Reward / Risk</span>
                <span className="v" style={{ fontSize: "16px", fontWeight: 700 }}>
                  {metrics.riskRewardRatio}
                </span>
              </div>

              {/* POP (Probability of Profit) */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>POP (Prob. of Profit)</span>
                <span className="v" style={{ fontSize: "16px", fontWeight: 700, color: metrics.pop >= 60 ? "#22c55e" : "var(--fg)" }}>
                  {metrics.pop}%
                </span>
              </div>

              {/* Breakeven Points */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>Breakeven(s)</span>
                <span className="v" style={{ fontSize: "13px", fontWeight: 700 }}>
                  {metrics.breakevens.length > 0
                    ? metrics.breakevens.map((b) => b.toLocaleString("en-IN")).join(", ")
                    : "None"}
                </span>
              </div>

              {/* Net Greeks */}
              <div className="stat panel" style={{ padding: "10px 14px", border: "1px solid var(--line)" }}>
                <span className="k" style={{ fontSize: "11px", color: "var(--muted)" }}>Strategy Delta / Theta</span>
                <span className="v" style={{ fontSize: "13px", fontWeight: 700 }}>
                  Δ {metrics.netDelta} · Θ {rupees(metrics.netTheta)}
                </span>
              </div>
            </div>

            {/* Payoff Graph & Sub-Tabs */}
            <div className="panel" style={{ border: "1px solid var(--line)", borderRadius: "8px", overflow: "hidden" }}>
              {/* Tabs */}
              <div className="row justify-between align-center" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)", background: "var(--panel-alt, var(--panel))" }}>
                <div className="seg" role="group" aria-label="Payoff view">
                  <button aria-pressed={payoffTab === "graph"} onClick={() => setPayoffTab("graph")}>
                    Payoff Graph
                  </button>
                  <button aria-pressed={payoffTab === "table"} onClick={() => setPayoffTab("table")}>
                    P&L Table
                  </button>
                  <button aria-pressed={payoffTab === "greeks"} onClick={() => setPayoffTab("greeks")}>
                    Greeks
                  </button>
                </div>

                <div className="row gap-3 align-center mini">
                  {/* Projected Profit Chip */}
                  <span
                    style={{
                      padding: "4px 10px",
                      borderRadius: "14px",
                      background: metrics.projectedPnl >= 0 ? "rgba(34, 197, 94, 0.15)" : "rgba(239, 68, 68, 0.15)",
                      color: metrics.projectedPnl >= 0 ? "#16a34a" : "#dc2626",
                      fontWeight: 700,
                      fontSize: "12px",
                    }}
                  >
                    Target P&L: {metrics.projectedPnl >= 0 ? `+${rupees(metrics.projectedPnl)}` : `-${rupees(Math.abs(metrics.projectedPnl))}`}
                  </span>
                </div>
              </div>

              {/* TAB CONTENT: PAYOFF GRAPH */}
              {payoffTab === "graph" && (
                <div>
                  <PayoffChart
                    points={points}
                    currentSpot={spot}
                    targetPrice={targetPrice}
                    onTargetPriceChange={setTargetPrice}
                    metrics={metrics}
                    oiData={oiData}
                    showOi={true}
                  />

                  {/* Interactive Sliders Below Graph (Sensibull Style) */}
                  <div
                    style={{
                      padding: "16px",
                      borderTop: "1px solid var(--line)",
                      background: "var(--panel-alt, var(--panel))",
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr",
                      gap: "20px",
                    }}
                  >
                    {/* Target Price Slider */}
                    <div className="stack gap-1">
                      <div className="row justify-between align-center" style={{ fontSize: "12px" }}>
                        <span>
                          <b>Target Price:</b> {targetPrice.toLocaleString("en-IN")}{" "}
                          <span className={targetPrice >= spot ? "up" : "down"}>
                            ({spot > 0 ? (((targetPrice - spot) / spot) * 100).toFixed(1) : "0"}%)
                          </span>
                        </span>
                        <button className="link mini" onClick={() => setTargetPrice(spot)}>
                          Reset
                        </button>
                      </div>
                      <div className="row align-center gap-2">
                        <button className="btn xs" onClick={() => setTargetPrice((p) => Math.round(p - (spot * 0.005)))}>
                          -
                        </button>
                        <input
                          type="range"
                          style={{ flex: 1 }}
                          min={Math.floor(spot * 0.9)}
                          max={Math.ceil(spot * 1.1)}
                          step={Math.max(1, Math.round(spot * 0.001))}
                          value={targetPrice}
                          onChange={(e) => setTargetPrice(Number(e.target.value))}
                        />
                        <button className="btn xs" onClick={() => setTargetPrice((p) => Math.round(p + (spot * 0.005)))}>
                          +
                        </button>
                      </div>
                    </div>

                    {/* Target Date Slider */}
                    <div className="stack gap-1">
                      <div className="row justify-between align-center" style={{ fontSize: "12px" }}>
                        <span>
                          <b>Target Date:</b> {targetDate.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}{" "}
                          <span className="muted">
                            ({targetDateOffset === 0 ? "Today" : targetDateOffset === 1 ? "At Expiry" : `${Math.round(targetDateOffset * 100)}% to expiry`})
                          </span>
                        </span>
                        <button className="link mini" onClick={() => setTargetDateOffset(0)}>
                          Reset
                        </button>
                      </div>
                      <div className="row align-center gap-2">
                        <span className="mini muted">Now</span>
                        <input
                          type="range"
                          style={{ flex: 1 }}
                          min={0}
                          max={1}
                          step={0.05}
                          value={targetDateOffset}
                          onChange={(e) => setTargetDateOffset(Number(e.target.value))}
                        />
                        <span className="mini muted">Expiry</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB CONTENT: P&L TABLE */}
              {payoffTab === "table" && (
                <div style={{ maxHeight: 420, overflowY: "auto" }}>
                  <table className="table compact" style={{ width: "100%", fontSize: "12px" }}>
                    <thead style={{ position: "sticky", top: 0, background: "var(--panel)" }}>
                      <tr>
                        <th>Underlying Price</th>
                        <th>% Move</th>
                        <th style={{ textAlign: "right", color: "#3b82f6" }}>Target Date P&L</th>
                        <th style={{ textAlign: "right", color: "#ef4444" }}>Expiry P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {points.filter((_, idx) => idx % 6 === 0).map((pt) => (
                        <tr key={pt.price} style={{ background: Math.abs(pt.price - spot) < (spot * 0.003) ? "var(--accent-tint)" : undefined }}>
                          <td style={{ fontWeight: 600 }}>{pt.price.toLocaleString("en-IN")}</td>
                          <td className={pt.pctChange >= 0 ? "up" : "down"}>
                            {pt.pctChange >= 0 ? `+${pt.pctChange}%` : `${pt.pctChange}%`}
                          </td>
                          <td style={{ textAlign: "right", fontWeight: 700, color: pt.pnlTargetDate >= 0 ? "#16a34a" : "#dc2626" }}>
                            {pt.pnlTargetDate >= 0 ? `+${rupees(pt.pnlTargetDate)}` : `-${rupees(Math.abs(pt.pnlTargetDate))}`}
                          </td>
                          <td style={{ textAlign: "right", fontWeight: 700, color: pt.pnlExpiry >= 0 ? "#16a34a" : "#dc2626" }}>
                            {pt.pnlExpiry >= 0 ? `+${rupees(pt.pnlExpiry)}` : `-${rupees(Math.abs(pt.pnlExpiry))}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* TAB CONTENT: GREEKS */}
              {payoffTab === "greeks" && (
                <div style={{ padding: "16px" }} className="stack gap-3">
                  <div className="row wrap gap-3">
                    <div className="stat panel" style={{ flex: 1, padding: "10px", border: "1px solid var(--line)" }}>
                      <span className="k">Net Delta (Δ)</span>
                      <b style={{ fontSize: "16px" }}>{metrics.netDelta}</b>
                      <span className="mini muted">₹ P&L for ₹1 spot move</span>
                    </div>
                    <div className="stat panel" style={{ flex: 1, padding: "10px", border: "1px solid var(--line)" }}>
                      <span className="k">Net Theta (Θ)</span>
                      <b style={{ fontSize: "16px", color: metrics.netTheta >= 0 ? "#16a34a" : "#dc2626" }}>
                        {metrics.netTheta >= 0 ? `+${rupees(metrics.netTheta)}` : `-${rupees(Math.abs(metrics.netTheta))}`} / day
                      </b>
                      <span className="mini muted">Daily time decay</span>
                    </div>
                    <div className="stat panel" style={{ flex: 1, padding: "10px", border: "1px solid var(--line)" }}>
                      <span className="k">Net Vega (𝒱)</span>
                      <b style={{ fontSize: "16px" }}>
                        {metrics.netVega >= 0 ? `+${rupees(metrics.netVega)}` : `-${rupees(Math.abs(metrics.netVega))}`} / 1% IV
                      </b>
                      <span className="mini muted">P&L per 1% change in IV</span>
                    </div>
                  </div>

                  <table className="table compact" style={{ width: "100%", fontSize: "12px" }}>
                    <thead>
                      <tr>
                        <th>Leg</th>
                        <th>Side</th>
                        <th>Lots</th>
                        <th>IV</th>
                        <th style={{ textAlign: "right" }}>Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {legs.filter((l) => l.enabled).map((leg) => (
                        <tr key={leg.id}>
                          <td>{leg.kind === "FUT" ? `${underlying} FUT` : `${leg.strike} ${leg.kind}`}</td>
                          <td>
                            <span className={leg.side === "B" ? "up" : "down"} style={{ fontWeight: 700 }}>
                              {leg.side === "B" ? "BUY" : "SELL"}
                            </span>
                          </td>
                          <td>{leg.lots} ({leg.lots * leg.lotSize})</td>
                          <td>{leg.iv ? `${Math.round(leg.iv * 100)}%` : "–"}</td>
                          <td style={{ textAlign: "right", fontWeight: 600 }}>₹{px(leg.price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        /* ────────────────────── EMPTY STATE HERO CARD ────────────────────── */
        <div
          className="panel"
          style={{
            padding: "36px 20px",
            textAlign: "center",
            border: "1px dashed var(--line)",
            borderRadius: "8px",
            background: "var(--panel)",
          }}
        >
          <div style={{ maxWidth: 440, margin: "0 auto" }} className="stack gap-3 align-center">
            <div style={{ width: 56, height: 56, borderRadius: "50%", background: "var(--accent-tint)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "24px" }}>
              📊
            </div>
            <div>
              <h2 style={{ fontSize: "20px", margin: "0 0 6px 0" }}>No Trades Added Yet</h2>
              <p className="muted" style={{ margin: 0, fontSize: "14px" }}>
                Select a ready-made options strategy below or build a custom strategy by picking strikes and futures.
              </p>
            </div>
            <button
              className="btn primary solid"
              style={{ padding: "10px 24px", fontSize: "15px", fontWeight: 700 }}
              onClick={() => setModalOpen(true)}
            >
              Build a new custom strategy
            </button>
          </div>
        </div>
      )}

      {/* ────────────────────── BOTTOM TABS: READY-MADE / POSITIONS / SAVED ────────────────────── */}
      <div className="panel" style={{ border: "1px solid var(--line)", borderRadius: "8px", padding: "16px" }}>
        {/* Navigation Tabs */}
        <div className="row justify-between align-center" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "12px", marginBottom: "16px" }}>
          <div className="seg" role="group" aria-label="Strategy templates or positions">
            <button aria-pressed={bottomTab === "readymade"} onClick={() => setBottomTab("readymade")}>
              Ready-made Strategies
            </button>
            <button
              aria-pressed={bottomTab === "positions"}
              onClick={() => {
                setBottomTab("positions");
                handleLoadPositions();
              }}
            >
              Positions
            </button>
            <button aria-pressed={bottomTab === "saved"} onClick={() => setBottomTab("saved")}>
              Saved Strategies ({savedStrategies.length})
            </button>
          </div>

          {bottomTab === "readymade" && (
            <div className="row gap-2 align-center">
              <span className="mini muted">Expiry:</span>
              <select
                className="select sm"
                style={{ width: "auto" }}
                value={selectedExpiry}
                onChange={(e) => setChainSel({ expiry: e.target.value })}
              >
                {expiries.map((exp) => (
                  <option key={exp} value={exp}>
                    {expiryLabel(exp)} · {daysTo(exp)}d
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* TAB 1: READY-MADE STRATEGIES */}
        {bottomTab === "readymade" && (
          <div className="stack gap-3">
            {/* Category Filter Pills */}
            <div className="seg" role="group" aria-label="Direction filter">
              {(["bullish", "bearish", "neutral", "others"] as StrategyCategory[]).map((cat) => (
                <button
                  key={cat}
                  aria-pressed={categoryFilter === cat}
                  onClick={() => setCategoryFilter(cat)}
                  style={{ textTransform: "capitalize" }}
                >
                  {cat}
                </button>
              ))}
            </div>

            {/* Grid of Strategy Cards */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
                gap: "12px",
              }}
            >
              {READY_MADE_STRATEGIES.filter((s) => s.category === categoryFilter).map((strat) => (
                <div
                  key={strat.id}
                  className="strategy-card panel"
                  style={{
                    padding: "12px 14px",
                    border: "1px solid var(--line)",
                    borderRadius: "6px",
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    gap: 10,
                  }}
                  onClick={() => handleApplyReadyMade(strat)}
                >
                  {/* Mini Payoff Preview Chart */}
                  <div style={{ height: 40, width: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <svg viewBox="0 0 40 24" style={{ width: "80px", height: "40px" }}>
                      <path
                        d={strat.iconSvg}
                        fill="none"
                        stroke={strat.category === "bullish" ? "#22c55e" : strat.category === "bearish" ? "#ef4444" : "#eab308"}
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>

                  <div>
                    <b style={{ fontSize: "14px", display: "block" }}>{strat.name}</b>
                    <span className="mini muted" style={{ fontSize: "11px", display: "block", marginTop: 2 }}>
                      {strat.outlook}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TAB 2: BROKER POSITIONS */}
        {bottomTab === "positions" && (
          <div>
            {loadingPositions ? (
              <div style={{ textAlign: "center", padding: "30px" }} className="muted">
                Loading positions from broker…
              </div>
            ) : positions.length === 0 ? (
              <Empty title="No open F&O positions found">
                You have no open derivatives positions in your connected broker account to import.
              </Empty>
            ) : (
              <div className="stack gap-2">
                <table className="table compact" style={{ width: "100%", fontSize: "12px" }}>
                  <thead>
                    <tr>
                      <th>Instrument</th>
                      <th>Qty</th>
                      <th>Net Price</th>
                      <th style={{ textAlign: "right" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p) => (
                      <tr key={p.key}>
                        <td>
                          <b>{p.symbol}</b>
                        </td>
                        <td className={p.qty > 0 ? "up" : "down"}>
                          {p.qty > 0 ? `+${p.qty}` : p.qty}
                        </td>
                        <td>₹{px(p.net_price || p.buy_price || p.sell_price)}</td>
                        <td style={{ textAlign: "right" }}>
                          <button className="btn xs primary" onClick={() => handleImportPosition(p)}>
                            Import into Builder
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* TAB 3: SAVED STRATEGIES */}
        {bottomTab === "saved" && (
          <div>
            {savedStrategies.length === 0 ? (
              <Empty title="No saved strategies yet">
                Build your custom strategy above and click "Save Strategy" to store it for future analysis.
              </Empty>
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                  gap: "12px",
                }}
              >
                {savedStrategies.map((s) => (
                  <div
                    key={s.id}
                    className="panel stack gap-2"
                    style={{ padding: "12px 14px", border: "1px solid var(--line)" }}
                  >
                    <div className="row justify-between align-center">
                      <b>{s.name}</b>
                      <span className="mini muted">{s.date}</span>
                    </div>
                    <div className="mini muted">
                      {s.legs.length} legs · {s.legs.map((l) => (l.kind === "FUT" ? "FUT" : `${l.strike}${l.kind}`)).join(", ")}
                    </div>
                    <div className="row gap-2" style={{ marginTop: 4 }}>
                      <button className="btn sm primary" onClick={() => handleLoadSaved(s.legs)}>
                        Load
                      </button>
                      <button className="btn sm danger" onClick={() => handleDeleteSaved(s.id)}>
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ────────────────────── STRIKE & FUTURES SELECTOR MODAL ────────────────────── */}
      <StrategyStrikeModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        underlying={underlying}
        chainData={chainData ?? null}
        selectedExpiry={selectedExpiry}
        onSelectExpiry={(exp) => setChainSel({ expiry: exp })}
        legs={legs}
        onToggleLeg={handleToggleLeg}
        onClearAll={handleClearAll}
      />

      {/* ────────────────────── SAVE STRATEGY MODAL ────────────────────── */}
      {saveModalOpen && (
        <Modal
          title="Save Strategy"
          onClose={() => setSaveModalOpen(false)}
          footer={
            <>
              <button className="btn" onClick={() => setSaveModalOpen(false)}>
                Cancel
              </button>
              <button
                className="btn primary"
                disabled={!saveName.trim()}
                onClick={handleSaveStrategy}
              >
                Save
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="strat-save-name">Strategy Name</label>
            <input
              id="strat-save-name"
              className="input"
              placeholder="e.g. NIFTY Weekly Iron Condor"
              autoFocus
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSaveStrategy()}
            />
          </div>
        </Modal>
      )}

      {/* ────────────────────── TRADE ALL CONFIRMATION MODAL ────────────────────── */}
      {tradeAllOpen && (
        <Modal
          title="Trade All Strategy Legs"
          onClose={() => setTradeAllOpen(false)}
          footer={
            <>
              <button className="btn" disabled={tradingBusy} onClick={() => setTradeAllOpen(false)}>
                Cancel
              </button>
              <button
                className="btn primary solid"
                disabled={tradingBusy}
                onClick={handleExecuteTradeAll}
                style={{ background: "var(--accent)", borderColor: "var(--accent)" }}
              >
                {tradingBusy ? "Placing Orders…" : "Confirm & Place Orders"}
              </button>
            </>
          }
        >
          <div className="stack gap-3">
            <div className="notice">
              {tradeOrderType === "MKT" ? (
                <span>
                  This will place <b>{legs.filter((l) => l.enabled).length} Market (MKT) orders</b> for instant execution at current market rates into your connected account.
                </span>
              ) : (
                <span>
                  This will place <b>{legs.filter((l) => l.enabled).length} Limit (L) orders</b> at your strategy prices into your connected account.
                </span>
              )}
            </div>

            {/* Account Selector */}
            {connectedAccounts.length > 0 && (
              <div className="field">
                <label>Target Account</label>
                <select
                  className="select"
                  value={tradeAccount}
                  onChange={(e) => setTradeAccount(e.target.value)}
                >
                  {connectedAccounts.length > 1 && <option value="all">All Connected Accounts</option>}
                  {connectedAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label} ({a.ucc || a.id})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Order Type (Market vs Limit) */}
            <div className="field">
              <label>Order Type</label>
              <div className="seg full">
                <button
                  type="button"
                  aria-pressed={tradeOrderType === "MKT"}
                  onClick={() => setTradeOrderType("MKT")}
                >
                  Market (MKT) - Default
                </button>
                <button
                  type="button"
                  aria-pressed={tradeOrderType === "L"}
                  onClick={() => setTradeOrderType("L")}
                >
                  Limit (L) - Strategy Prices
                </button>
              </div>
            </div>

            {/* Product Type */}
            <div className="field">
              <label>Product</label>
              <div className="seg full">
                <button aria-pressed={tradeProduct === "NRML"} onClick={() => setTradeProduct("NRML")}>
                  NRML (Carry Forward)
                </button>
                <button aria-pressed={tradeProduct === "MIS"} onClick={() => setTradeProduct("MIS")}>
                  MIS (Intraday)
                </button>
              </div>
            </div>

            {/* Order Review List */}
            <div className="stack gap-1" style={{ maxHeight: 200, overflowY: "auto" }}>
              {legs.filter((l) => l.enabled).map((l) => (
                <div key={l.id} className="row justify-between align-center mini" style={{ padding: "4px 8px", background: "var(--panel-alt)", borderRadius: 4 }}>
                  <span>
                    <b className={l.side === "B" ? "up" : "down"}>{l.side === "B" ? "BUY" : "SELL"}</b> {l.lots} lot ({l.lots * l.lotSize} qty) · {l.kind === "FUT" ? `${underlying} FUT` : `${l.strike} ${l.kind}`}
                  </span>
                  <b>{tradeOrderType === "MKT" ? "MKT" : `₹${px(l.price)}`}</b>
                </div>
              ))}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
