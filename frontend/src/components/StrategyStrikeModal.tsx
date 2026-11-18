import { useState, useEffect, useMemo, useRef } from "react";
import { api } from "../lib/api";
import { expiryLabel, daysTo, type ChainData } from "../lib/chain";
import { calcIV, bsGreeks, type StrategyLeg } from "../lib/blackScholes";
import { px, rupees } from "../lib/format";
import { CloseBtn } from "./ui";

interface FutureContract {
  key: string;
  symbol: string;
  name: string;
  expiry: string | null;
  lot: number;
  tick: number;
  ltp: number | null;
  pc: number | null;
}

interface StrategyStrikeModalProps {
  isOpen: boolean;
  onClose: () => void;
  underlying: string;
  chainData: ChainData | null;
  selectedExpiry: string;
  onSelectExpiry: (exp: string) => void;
  legs: StrategyLeg[];
  onToggleLeg: (leg: Omit<StrategyLeg, "id">) => void;
  onClearAll: () => void;
}

export function StrategyStrikeModal({
  isOpen,
  onClose,
  underlying,
  chainData,
  selectedExpiry,
  onSelectExpiry,
  legs,
  onToggleLeg,
  onClearAll,
}: StrategyStrikeModalProps) {
  const [activeTab, setActiveTab] = useState<"strikes" | "futures" | "straddles" | "strangles">("strikes");
  const [viewMode, setViewMode] = useState<"ltp" | "oi" | "greeks">("ltp");
  const [futures, setFutures] = useState<FutureContract[]>([]);
  const [loadingFuts, setLoadingFuts] = useState(false);

  // Fetch futures when tab opens or underlying changes
  useEffect(() => {
    if (!isOpen) return;
    let alive = true;
    setLoadingFuts(true);
    api<{ ok: boolean; futures: FutureContract[] }>(`/chain/futures?underlying=${encodeURIComponent(underlying)}`)
      .then((r) => {
        if (alive && r.ok && r.futures) {
          setFutures(r.futures);
        }
      })
      .catch(() => {
        /* fallback empty */
      })
      .finally(() => {
        if (alive) setLoadingFuts(false);
      });
    return () => {
      alive = false;
    };
  }, [isOpen, underlying]);

  const spot = chainData?.spot ?? 0;
  const atm = chainData?.atm ?? 0;
  const expiries = chainData?.expiries ?? [];
  const rows = chainData?.rows ?? [];
  const lotSize = chainData?.lot ?? 1;

  // Max OI for bar charts
  const maxOi = useMemo(() => {
    let max = 1;
    for (const r of rows) {
      if (r.ce?.oi && r.ce.oi > max) max = r.ce.oi;
      if (r.pe?.oi && r.pe.oi > max) max = r.pe.oi;
    }
    return max;
  }, [rows]);

  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const jumpToAtm = (smooth = true) => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const atmRow = el.querySelector<HTMLElement>(".atm-row");
    if (!atmRow) return;
    const delta =
      atmRow.getBoundingClientRect().top -
      el.getBoundingClientRect().top -
      el.clientHeight / 2 +
      atmRow.clientHeight / 2;
    el.scrollTo({ top: el.scrollTop + delta, behavior: smooth ? "smooth" : "auto" });
  };

  useEffect(() => {
    if (isOpen && activeTab === "strikes" && rows.length > 0 && atm > 0) {
      const timer = window.setTimeout(() => jumpToAtm(false), 50);
      return () => window.clearTimeout(timer);
    }
  }, [isOpen, activeTab, selectedExpiry, rows.length, atm]);

  if (!isOpen) return null;

  // Check if a specific leg is selected
  const getLegStatus = (kind: "CE" | "PE" | "FUT", strike: number, exp: string) => {
    const found = legs.find((l) => l.kind === kind && l.strike === strike && l.expiry === exp);
    return found ? found.side : null;
  };

  const getLegLots = (kind: "CE" | "PE" | "FUT", strike: number, exp: string) => {
    const found = legs.find((l) => l.kind === kind && l.strike === strike && l.expiry === exp);
    return found ? found.lots : 1;
  };

  const handleLegClick = (
    kind: "CE" | "PE",
    side: "B" | "S",
    strike: number,
    price: number,
    key: string,
    symbol: string,
    ivValue?: number
  ) => {
    onToggleLeg({
      enabled: true,
      kind,
      side,
      strike,
      expiry: selectedExpiry,
      lots: 1,
      lotSize,
      price: price > 0 ? price : 10,
      key,
      symbol,
      underlying,
      ltp: price,
      iv: ivValue,
    });
  };

  const handleFutureClick = (fut: FutureContract, side: "B" | "S") => {
    onToggleLeg({
      enabled: true,
      kind: "FUT",
      side,
      strike: 0,
      expiry: fut.expiry || selectedExpiry,
      lots: 1,
      lotSize: fut.lot || lotSize,
      price: fut.ltp && fut.ltp > 0 ? fut.ltp : spot,
      key: fut.key,
      symbol: fut.symbol,
      underlying,
      ltp: fut.ltp || spot,
      iv: 0,
    });
  };

  // Pre-calculate Greeks & IV for each row
  const enrichedRows = rows.map((r) => {
    const expDate = new Date(selectedExpiry + "T15:30:00+05:30").getTime();
    const tYears = Math.max(0.0001, (expDate - Date.now()) / (365 * 24 * 3600 * 1000));

    const cePrice = r.ce?.ltp ?? 0;
    const pePrice = r.pe?.ltp ?? 0;

    const ceIV = cePrice > 0 && spot > 0 ? calcIV(cePrice, spot, r.strike, tYears, "CE") : 0.16;
    const peIV = pePrice > 0 && spot > 0 ? calcIV(pePrice, spot, r.strike, tYears, "PE") : 0.16;

    const ceGreeks = spot > 0 ? bsGreeks(spot, r.strike, tYears, ceIV, "CE") : { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 };
    const peGreeks = spot > 0 ? bsGreeks(spot, r.strike, tYears, peIV, "PE") : { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 };

    return {
      ...r,
      ceIV: Math.round(ceIV * 1000) / 10,
      peIV: Math.round(peIV * 1000) / 10,
      ceDelta: Math.round(ceGreeks.delta * 100) / 100,
      peDelta: Math.round(peGreeks.delta * 100) / 100,
      ceTheta: Math.round(ceGreeks.theta * 10) / 10,
      peTheta: Math.round(peGreeks.theta * 10) / 10,
    };
  });

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.65)",
        display: "flex",
        justifyContent: "flex-start",
        backdropFilter: "blur(2px)",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "760px",
          height: "100%",
          background: "var(--panel)",
          boxShadow: "4px 0 24px rgba(0,0,0,0.4)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--line)" }} className="row justify-between align-center">
          <div className="row gap-3 align-center">
            <h2 style={{ fontSize: "18px", margin: 0 }}>
              {underlying} <span style={{ fontWeight: 400, fontSize: "15px", color: "var(--muted)" }}>{spot ? spot.toLocaleString("en-IN") : "–"}</span>
            </h2>
            {chainData?.summary?.pcr && (
              <span className="badge mini">PCR {chainData.summary.pcr}</span>
            )}
          </div>
          <div className="row gap-2 align-center">
            <CloseBtn onClick={onClose} />
          </div>
        </div>

        {/* Sub-tabs & Controls */}
        <div style={{ padding: "10px 18px", borderBottom: "1px solid var(--line)", background: "var(--panel-alt, var(--panel))" }} className="row wrap justify-between align-center gap-3">
          <div className="seg" role="group" aria-label="Strategy Builder Strike View">
            <button aria-pressed={activeTab === "strikes"} onClick={() => setActiveTab("strikes")}>
              Strikes
            </button>
            <button aria-pressed={activeTab === "futures"} onClick={() => setActiveTab("futures")}>
              Futures
            </button>
            <button aria-pressed={activeTab === "straddles"} onClick={() => setActiveTab("straddles")}>
              Straddles
            </button>
            <button aria-pressed={activeTab === "strangles"} onClick={() => setActiveTab("strangles")}>
              Strangles
            </button>
          </div>

          <div className="row gap-2 align-center">
            {activeTab === "strikes" && (
              <div className="seg" role="group" aria-label="Strike info">
                <button aria-pressed={viewMode === "ltp"} onClick={() => setViewMode("ltp")}>LTP</button>
                <button aria-pressed={viewMode === "oi"} onClick={() => setViewMode("oi")}>OI</button>
                <button aria-pressed={viewMode === "greeks"} onClick={() => setViewMode("greeks")}>Greeks</button>
              </div>
            )}

            <select
              className="select sm"
              style={{ minWidth: 130 }}
              value={selectedExpiry}
              onChange={(e) => onSelectExpiry(e.target.value)}
            >
              {expiries.map((exp) => (
                <option key={exp} value={exp}>
                  {expiryLabel(exp)} · {daysTo(exp)}d
                </option>
              ))}
            </select>

            {activeTab === "strikes" && (
              <button
                className="btn sm"
                onClick={() => jumpToAtm(true)}
                title="Jump to at-the-money strike"
                style={{
                  padding: "4px 10px",
                  fontSize: "12px",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                  cursor: "pointer",
                }}
              >
                Jump to ATM
              </button>
            )}
          </div>
        </div>

        {/* Content Area */}
        <div ref={scrollContainerRef} style={{ flex: 1, overflowY: "auto", position: "relative" }}>
          {/* TAB: STRIKES */}
          {activeTab === "strikes" && (
            <table className="table compact chain-table" style={{ width: "100%", fontSize: "12px", borderCollapse: "collapse" }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--panel)", zIndex: 5, boxShadow: "0 1px 3px rgba(0,0,0,0.1)" }}>
                <tr>
                  <th style={{ textAlign: "left", width: 50 }}>Delta</th>
                  <th style={{ textAlign: "right", width: 75 }}>Call LTP</th>
                  {viewMode === "oi" && <th style={{ textAlign: "right", width: 70 }}>Call OI</th>}
                  <th style={{ textAlign: "center", width: 85 }}>Call B/S</th>
                  <th style={{ textAlign: "center", width: 80, fontWeight: 700, background: "var(--line-soft, var(--line))" }}>Strike</th>
                  <th style={{ textAlign: "center", width: 45 }}>IV</th>
                  <th style={{ textAlign: "center", width: 85 }}>Put B/S</th>
                  {viewMode === "oi" && <th style={{ textAlign: "left", width: 70 }}>Put OI</th>}
                  <th style={{ textAlign: "left", width: 75 }}>Put LTP</th>
                  <th style={{ textAlign: "right", width: 50 }}>Delta</th>
                </tr>
              </thead>
              <tbody>
                {enrichedRows.map((r) => {
                  const isAtm = r.strike === atm;
                  const ceStatus = getLegStatus("CE", r.strike, selectedExpiry);
                  const peStatus = getLegStatus("PE", r.strike, selectedExpiry);
                  const ceLots = getLegLots("CE", r.strike, selectedExpiry);
                  const peLots = getLegLots("PE", r.strike, selectedExpiry);

                  const callOiPct = r.ce?.oi ? Math.min(100, Math.round((r.ce.oi / maxOi) * 100)) : 0;
                  const putOiPct = r.pe?.oi ? Math.min(100, Math.round((r.pe.oi / maxOi) * 100)) : 0;

                  return (
                    <tr
                      key={r.strike}
                      className={isAtm ? "atm-row" : ""}
                      style={{
                        background: isAtm ? "var(--accent-tint, rgba(230, 92, 0, 0.08))" : undefined,
                        borderBottom: "1px solid var(--line-soft, var(--line))",
                      }}
                    >
                      {/* Call Delta */}
                      <td style={{ textAlign: "left", color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                        {r.ceDelta}
                      </td>

                      {/* Call LTP */}
                      <td style={{ textAlign: "right", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                        {r.ce?.ltp ? px(r.ce.ltp) : "–"}
                      </td>

                      {/* Call OI bar if OI mode */}
                      {viewMode === "oi" && (
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 4 }}>
                            <span className="mini muted">{r.ce?.oi ? (r.ce.oi / 1000).toFixed(0) + "k" : "–"}</span>
                            <div style={{ width: 35, height: 4, background: "var(--line)", borderRadius: 2, overflow: "hidden" }}>
                              <div style={{ width: `${callOiPct}%`, height: "100%", background: "#f97316" }} />
                            </div>
                          </div>
                        </td>
                      )}

                      {/* Call B/S Buttons */}
                      <td style={{ textAlign: "center" }}>
                        <div style={{ display: "inline-flex", gap: 3 }}>
                          <button
                            className={`btn xs ${ceStatus === "B" ? "primary solid" : ""}`}
                            style={{
                              padding: "2px 6px",
                              fontSize: "11px",
                              background: ceStatus === "B" ? "#2563eb" : undefined,
                              color: ceStatus === "B" ? "#fff" : undefined,
                              borderColor: ceStatus === "B" ? "#2563eb" : undefined,
                            }}
                            disabled={!r.ce}
                            onClick={() =>
                              r.ce &&
                              handleLegClick(
                                "CE",
                                "B",
                                r.strike,
                                r.ce.ltp ?? 0,
                                r.ce.key,
                                r.ce.symbol,
                                r.ceIV / 100
                              )
                            }
                          >
                            B{ceStatus === "B" ? ` ${ceLots}` : ""}
                          </button>
                          <button
                            className={`btn xs ${ceStatus === "S" ? "danger solid" : ""}`}
                            style={{
                              padding: "2px 6px",
                              fontSize: "11px",
                              background: ceStatus === "S" ? "#dc2626" : undefined,
                              color: ceStatus === "S" ? "#fff" : undefined,
                              borderColor: ceStatus === "S" ? "#dc2626" : undefined,
                            }}
                            disabled={!r.ce}
                            onClick={() =>
                              r.ce &&
                              handleLegClick(
                                "CE",
                                "S",
                                r.strike,
                                r.ce.ltp ?? 0,
                                r.ce.key,
                                r.ce.symbol,
                                r.ceIV / 100
                              )
                            }
                          >
                            S{ceStatus === "S" ? ` ${ceLots}` : ""}
                          </button>
                        </div>
                      </td>

                      {/* Center Strike */}
                      <td
                        style={{
                          textAlign: "center",
                          fontWeight: 700,
                          background: isAtm ? "var(--accent)" : "var(--panel-alt, rgba(0,0,0,0.03))",
                          color: isAtm ? "#ffffff" : "var(--fg)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {r.strike}
                        {isAtm && (
                          <div style={{ fontSize: "8.5px", lineHeight: 1, fontWeight: 800, marginTop: 1, letterSpacing: 0.5, color: "#fff" }}>
                            ATM
                          </div>
                        )}
                      </td>

                      {/* IV */}
                      <td style={{ textAlign: "center", color: "var(--muted)", fontSize: "11px" }}>
                        {r.ceIV || r.peIV || "–"}
                      </td>

                      {/* Put B/S Buttons */}
                      <td style={{ textAlign: "center" }}>
                        <div style={{ display: "inline-flex", gap: 3 }}>
                          <button
                            className={`btn xs ${peStatus === "B" ? "primary solid" : ""}`}
                            style={{
                              padding: "2px 6px",
                              fontSize: "11px",
                              background: peStatus === "B" ? "#2563eb" : undefined,
                              color: peStatus === "B" ? "#fff" : undefined,
                              borderColor: peStatus === "B" ? "#2563eb" : undefined,
                            }}
                            disabled={!r.pe}
                            onClick={() =>
                              r.pe &&
                              handleLegClick(
                                "PE",
                                "B",
                                r.strike,
                                r.pe.ltp ?? 0,
                                r.pe.key,
                                r.pe.symbol,
                                r.peIV / 100
                              )
                            }
                          >
                            B{peStatus === "B" ? ` ${peLots}` : ""}
                          </button>
                          <button
                            className={`btn xs ${peStatus === "S" ? "danger solid" : ""}`}
                            style={{
                              padding: "2px 6px",
                              fontSize: "11px",
                              background: peStatus === "S" ? "#dc2626" : undefined,
                              color: peStatus === "S" ? "#fff" : undefined,
                              borderColor: peStatus === "S" ? "#dc2626" : undefined,
                            }}
                            disabled={!r.pe}
                            onClick={() =>
                              r.pe &&
                              handleLegClick(
                                "PE",
                                "S",
                                r.strike,
                                r.pe.ltp ?? 0,
                                r.pe.key,
                                r.pe.symbol,
                                r.peIV / 100
                              )
                            }
                          >
                            S{peStatus === "S" ? ` ${peLots}` : ""}
                          </button>
                        </div>
                      </td>

                      {/* Put OI bar if OI mode */}
                      {viewMode === "oi" && (
                        <td style={{ textAlign: "left" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                            <div style={{ width: 35, height: 4, background: "var(--line)", borderRadius: 2, overflow: "hidden" }}>
                              <div style={{ width: `${putOiPct}%`, height: "100%", background: "#22c55e" }} />
                            </div>
                            <span className="mini muted">{r.pe?.oi ? (r.pe.oi / 1000).toFixed(0) + "k" : "–"}</span>
                          </div>
                        </td>
                      )}

                      {/* Put LTP */}
                      <td style={{ textAlign: "left", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                        {r.pe?.ltp ? px(r.pe.ltp) : "–"}
                      </td>

                      {/* Put Delta */}
                      <td style={{ textAlign: "right", color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                        {r.peDelta}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {/* TAB: FUTURES */}
          {activeTab === "futures" && (
            <div style={{ padding: "16px" }}>
              {loadingFuts ? (
                <div style={{ textAlign: "center", padding: "40px" }} className="muted">
                  Loading futures contracts…
                </div>
              ) : futures.length === 0 ? (
                <div style={{ textAlign: "center", padding: "40px" }} className="muted">
                  No active futures found for {underlying}.
                </div>
              ) : (
                <div className="stack gap-3">
                  {futures.map((f) => {
                    const futStatus = getLegStatus("FUT", 0, f.expiry || selectedExpiry);
                    return (
                      <div
                        key={f.key}
                        className="row justify-between align-center panel"
                        style={{ padding: "12px 16px", border: "1px solid var(--line)" }}
                      >
                        <div>
                          <div style={{ fontWeight: 600, fontSize: "14px" }}>{f.symbol}</div>
                          <div className="mini muted">
                            Expiry: {f.expiry ? expiryLabel(f.expiry) : "–"} · Lot size: {f.lot}
                          </div>
                        </div>

                        <div className="row gap-4 align-center">
                          <div style={{ textAlign: "right" }}>
                            <div style={{ fontSize: "15px", fontWeight: 700 }}>
                              {f.ltp ? `₹${px(f.ltp)}` : "–"}
                            </div>
                            <div className="mini muted">LTP</div>
                          </div>

                          <div className="row gap-2">
                            <button
                              className={`btn ${futStatus === "B" ? "primary solid" : ""}`}
                              style={{
                                background: futStatus === "B" ? "#2563eb" : undefined,
                                color: futStatus === "B" ? "#fff" : undefined,
                              }}
                              onClick={() => handleFutureClick(f, "B")}
                            >
                              Buy Future
                            </button>
                            <button
                              className={`btn ${futStatus === "S" ? "danger solid" : ""}`}
                              style={{
                                background: futStatus === "S" ? "#dc2626" : undefined,
                                color: futStatus === "S" ? "#fff" : undefined,
                              }}
                              onClick={() => handleFutureClick(f, "S")}
                            >
                              Sell Future
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* TAB: STRADDLES */}
          {activeTab === "straddles" && (
            <div style={{ padding: "16px" }}>
              <div className="notice" style={{ marginBottom: "12px" }}>
                A Straddle combines a Call and Put at the same strike. Buy for big breakout; Sell for rangebound pin.
              </div>
              <div className="stack gap-2">
                {enrichedRows.slice(Math.max(0, rows.findIndex((r) => r.strike === atm) - 4), Math.min(rows.length, rows.findIndex((r) => r.strike === atm) + 5)).map((r) => {
                  const straddlePrice = (r.ce?.ltp ?? 0) + (r.pe?.ltp ?? 0);
                  const isAtm = r.strike === atm;
                  return (
                    <div
                      key={r.strike}
                      className="row justify-between align-center panel"
                      style={{
                        padding: "10px 14px",
                        border: isAtm ? "2px solid var(--accent)" : "1px solid var(--line)",
                        background: isAtm ? "var(--accent-tint)" : undefined,
                      }}
                    >
                      <div>
                        <b>{r.strike} Straddle</b> {isAtm && <span className="badge mini">ATM</span>}
                        <div className="mini muted">
                          CE: ₹{r.ce?.ltp ? px(r.ce.ltp) : "–"} | PE: ₹{r.pe?.ltp ? px(r.pe.ltp) : "–"}
                        </div>
                      </div>

                      <div className="row gap-3 align-center">
                        <div style={{ textAlign: "right" }}>
                          <b>₹{px(straddlePrice)}</b>
                          <div className="mini muted">Combined</div>
                        </div>

                        <div className="row gap-2">
                          <button
                            className="btn sm"
                            style={{ background: "#2563eb", color: "#fff" }}
                            disabled={!r.ce || !r.pe}
                            onClick={() => {
                              if (r.ce && r.pe) {
                                handleLegClick("CE", "B", r.strike, r.ce.ltp ?? 0, r.ce.key, r.ce.symbol);
                                handleLegClick("PE", "B", r.strike, r.pe.ltp ?? 0, r.pe.key, r.pe.symbol);
                              }
                            }}
                          >
                            Buy Straddle
                          </button>
                          <button
                            className="btn sm danger"
                            style={{ background: "#dc2626", color: "#fff" }}
                            disabled={!r.ce || !r.pe}
                            onClick={() => {
                              if (r.ce && r.pe) {
                                handleLegClick("CE", "S", r.strike, r.ce.ltp ?? 0, r.ce.key, r.ce.symbol);
                                handleLegClick("PE", "S", r.strike, r.pe.ltp ?? 0, r.pe.key, r.pe.symbol);
                              }
                            }}
                          >
                            Sell Straddle
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* TAB: STRANGLES */}
          {activeTab === "strangles" && (
            <div style={{ padding: "16px" }}>
              <div className="notice" style={{ marginBottom: "12px" }}>
                A Strangle combines an OTM Call and an OTM Put. Wider profit zone for sellers; lower debit for buyers.
              </div>
              <div className="stack gap-2">
                {[1, 2, 3, 4].map((offset) => {
                  const atmIdx = rows.findIndex((r) => r.strike === atm);
                  if (atmIdx - offset < 0 || atmIdx + offset >= rows.length) return null;
                  const peRow = rows[atmIdx - offset];
                  const ceRow = rows[atmIdx + offset];
                  const stranglePrice = (ceRow.ce?.ltp ?? 0) + (peRow.pe?.ltp ?? 0);

                  return (
                    <div
                      key={offset}
                      className="row justify-between align-center panel"
                      style={{ padding: "10px 14px", border: "1px solid var(--line)" }}
                    >
                      <div>
                        <b>
                          {peRow.strike} PE + {ceRow.strike} CE (±{offset} strikes)
                        </b>
                        <div className="mini muted">
                          PE: ₹{peRow.pe?.ltp ? px(peRow.pe.ltp) : "–"} | CE: ₹{ceRow.ce?.ltp ? px(ceRow.ce.ltp) : "–"}
                        </div>
                      </div>

                      <div className="row gap-3 align-center">
                        <div style={{ textAlign: "right" }}>
                          <b>₹{px(stranglePrice)}</b>
                          <div className="mini muted">Combined</div>
                        </div>

                        <div className="row gap-2">
                          <button
                            className="btn sm"
                            style={{ background: "#2563eb", color: "#fff" }}
                            disabled={!ceRow.ce || !peRow.pe}
                            onClick={() => {
                              if (ceRow.ce && peRow.pe) {
                                handleLegClick("PE", "B", peRow.strike, peRow.pe.ltp ?? 0, peRow.pe.key, peRow.pe.symbol);
                                handleLegClick("CE", "B", ceRow.strike, ceRow.ce.ltp ?? 0, ceRow.ce.key, ceRow.ce.symbol);
                              }
                            }}
                          >
                            Buy Strangle
                          </button>
                          <button
                            className="btn sm danger"
                            style={{ background: "#dc2626", color: "#fff" }}
                            disabled={!ceRow.ce || !peRow.pe}
                            onClick={() => {
                              if (ceRow.ce && peRow.pe) {
                                handleLegClick("PE", "S", peRow.strike, peRow.pe.ltp ?? 0, peRow.pe.key, peRow.pe.symbol);
                                handleLegClick("CE", "S", ceRow.strike, ceRow.ce.ltp ?? 0, ceRow.ce.key, ceRow.ce.symbol);
                              }
                            }}
                          >
                            Sell Strangle
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Sticky Footer Bar */}
        <div
          style={{
            padding: "12px 18px",
            borderTop: "1px solid var(--line)",
            background: "var(--panel)",
          }}
          className="row justify-between align-center"
        >
          <div className="row gap-3 align-center">
            <b>{legs.length} {legs.length === 1 ? "leg" : "legs"} selected</b>
            {legs.length > 0 && (
              <button className="btn sm danger" onClick={onClearAll}>
                Clear All
              </button>
            )}
          </div>

          <button className="btn primary" style={{ minWidth: 100 }} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
