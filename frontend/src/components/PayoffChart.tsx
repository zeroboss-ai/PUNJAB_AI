import { useState, useRef, useMemo } from "react";
import type { PayoffPoint, StrategyMetrics } from "../lib/blackScholes";
import { rupees } from "../lib/format";

interface PayoffChartProps {
  points: PayoffPoint[];
  currentSpot: number;
  targetPrice: number;
  onTargetPriceChange: (price: number) => void;
  metrics: StrategyMetrics;
  oiData?: { strike: number; callOi: number; putOi: number }[];
  showOi?: boolean;
}

export function PayoffChart({
  points,
  currentSpot,
  targetPrice,
  onTargetPriceChange,
  metrics,
  oiData = [],
  showOi = true,
}: PayoffChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredPoint, setHoveredPoint] = useState<PayoffPoint | null>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  // SVG dimensions
  const width = 800;
  const height = 360;
  const padding = { top: 30, right: 40, bottom: 40, left: 70 };

  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  // Compute Scales
  const { minPrice, maxPrice, minPnl, maxPnl } = useMemo(() => {
    if (!points.length) {
      return {
        minPrice: currentSpot * 0.95,
        maxPrice: currentSpot * 1.05,
        minPnl: -10000,
        maxPnl: 10000,
      };
    }

    const minP = points[0].price;
    const maxP = points[points.length - 1].price;

    let pnlLo = 0;
    let pnlHi = 0;
    for (const p of points) {
      if (p.pnlExpiry < pnlLo) pnlLo = p.pnlExpiry;
      if (p.pnlExpiry > pnlHi) pnlHi = p.pnlExpiry;
      if (p.pnlTargetDate < pnlLo) pnlLo = p.pnlTargetDate;
      if (p.pnlTargetDate > pnlHi) pnlHi = p.pnlTargetDate;
    }

    // Add padding to Y bounds
    const pnlSpan = Math.max(Math.abs(pnlHi - pnlLo), 5000);
    const lo = pnlLo - pnlSpan * 0.12;
    const hi = pnlHi + pnlSpan * 0.12;

    return { minPrice: minP, maxPrice: maxP, minPnl: lo, maxPnl: hi };
  }, [points, currentSpot]);

  const scaleX = (price: number) => {
    if (maxPrice <= minPrice) return padding.left + plotWidth / 2;
    return padding.left + ((price - minPrice) / (maxPrice - minPrice)) * plotWidth;
  };

  const scaleY = (pnl: number) => {
    if (maxPnl <= minPnl) return padding.top + plotHeight / 2;
    return padding.top + (1 - (pnl - minPnl) / (maxPnl - minPnl)) * plotHeight;
  };

  const yZero = scaleY(0);

  // Generate SVG path for Expiry Payoff (Red/Green line)
  const expiryPath = useMemo(() => {
    if (!points.length) return "";
    return points
      .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.price).toFixed(1)} ${scaleY(p.pnlExpiry).toFixed(1)}`)
      .join(" ");
  }, [points, minPrice, maxPrice, minPnl, maxPnl]);

  // Generate SVG path for Target Date Payoff (Smooth Blue line)
  const targetPath = useMemo(() => {
    if (!points.length) return "";
    return points
      .map((p, idx) => `${idx === 0 ? "M" : "L"} ${scaleX(p.price).toFixed(1)} ${scaleY(p.pnlTargetDate).toFixed(1)}`)
      .join(" ");
  }, [points, minPrice, maxPrice, minPnl, maxPnl]);

  // Fill area between Expiry Curve and Zero Line for profit/loss zones
  const { profitArea, lossArea } = useMemo(() => {
    if (!points.length) return { profitArea: "", lossArea: "" };

    // Clamped points for profit (> 0) and loss (< 0)
    const profPts = points.map((p) => ({ x: scaleX(p.price), y: scaleY(Math.max(0, p.pnlExpiry)) }));
    const lossPts = points.map((p) => ({ x: scaleX(p.price), y: scaleY(Math.min(0, p.pnlExpiry)) }));

    const pPath = `M ${scaleX(points[0].price)} ${yZero} ` +
      profPts.map((pt) => `L ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(" ") +
      ` L ${scaleX(points[points.length - 1].price)} ${yZero} Z`;

    const lPath = `M ${scaleX(points[0].price)} ${yZero} ` +
      lossPts.map((pt) => `L ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(" ") +
      ` L ${scaleX(points[points.length - 1].price)} ${yZero} Z`;

    return { profitArea: pPath, lossArea: lPath };
  }, [points, yZero, minPrice, maxPrice, minPnl, maxPnl]);

  // Open Interest bars scaled for background
  const maxOi = useMemo(() => {
    let m = 0;
    for (const it of oiData) {
      if (it.callOi > m) m = it.callOi;
      if (it.putOi > m) m = it.putOi;
    }
    return m > 0 ? m : 1;
  }, [oiData]);

  // Mouse hover interaction
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!containerRef.current || !points.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const svgX = (clientX / rect.width) * width;

    if (svgX < padding.left || svgX > width - padding.right) {
      setHoveredPoint(null);
      setHoverX(null);
      return;
    }

    const price = minPrice + ((svgX - padding.left) / plotWidth) * (maxPrice - minPrice);
    // Find closest point
    let closest = points[0];
    let minDiff = Math.abs(points[0].price - price);
    for (const p of points) {
      const diff = Math.abs(p.price - price);
      if (diff < minDiff) {
        minDiff = diff;
        closest = p;
      }
    }
    setHoveredPoint(closest);
    setHoverX(scaleX(closest.price));
  };

  const handleMouseLeave = () => {
    setHoveredPoint(null);
    setHoverX(null);
  };

  const handleSvgClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!points.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const svgX = (clientX / rect.width) * width;
    if (svgX >= padding.left && svgX <= width - padding.right) {
      const price = minPrice + ((svgX - padding.left) / plotWidth) * (maxPrice - minPrice);
      onTargetPriceChange(Math.round(price));
    }
  };

  // Y-axis grid ticks
  const yTicks = useMemo(() => {
    const ticks: number[] = [];
    const count = 6;
    const step = (maxPnl - minPnl) / count;
    for (let i = 0; i <= count; i++) {
      ticks.push(Math.round(minPnl + i * step));
    }
    return ticks;
  }, [minPnl, maxPnl]);

  // X-axis price ticks
  const xTicks = useMemo(() => {
    const ticks: number[] = [];
    const count = 7;
    const step = (maxPrice - minPrice) / count;
    for (let i = 0; i <= count; i++) {
      ticks.push(Math.round(minPrice + i * step));
    }
    return ticks;
  }, [minPrice, maxPrice]);

  return (
    <div className="payoff-chart-wrap" ref={containerRef} style={{ position: "relative", userSelect: "none" }}>
      {/* Chart Legend / Controls Header */}
      <div className="row wrap justify-between" style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)", fontSize: "12px", gap: 12 }}>
        <div className="row gap-4 align-center">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 14, height: 3, background: "#ef4444", borderRadius: 2 }} />
            <b style={{ color: "var(--fg)" }}>On Expiry</b>
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 10 }}>
            <span style={{ width: 14, height: 3, background: "#3b82f6", borderRadius: 2 }} />
            <b style={{ color: "#3b82f6" }}>On Target Date</b>
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 10 }}>
            <span style={{ width: 10, height: 10, border: "1px dashed var(--muted)", borderRadius: "50%" }} />
            <span className="muted">Spot: {currentSpot.toLocaleString("en-IN")}</span>
          </span>
        </div>

        {showOi && oiData.length > 0 && (
          <div className="row gap-3 align-center mini muted">
            <span className="row align-center gap-1">
              <span style={{ width: 8, height: 8, background: "rgba(249, 115, 22, 0.45)", borderRadius: 1 }} />
              Call OI
            </span>
            <span className="row align-center gap-1">
              <span style={{ width: 8, height: 8, background: "rgba(34, 197, 94, 0.45)", borderRadius: 1 }} />
              Put OI
            </span>
          </div>
        )}
      </div>

      {/* Main SVG Area */}
      <div style={{ position: "relative", width: "100%", overflow: "hidden" }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: "100%", height: "auto", display: "block", cursor: "crosshair" }}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          onClick={handleSvgClick}
        >
          <defs>
            <linearGradient id="profitGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(34, 197, 94, 0.22)" />
              <stop offset="100%" stopColor="rgba(34, 197, 94, 0.02)" />
            </linearGradient>
            <linearGradient id="lossGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(239, 68, 68, 0.02)" />
              <stop offset="100%" stopColor="rgba(239, 68, 68, 0.22)" />
            </linearGradient>
          </defs>

          {/* Background Grid */}
          {yTicks.map((tick) => {
            const y = scaleY(tick);
            return (
              <g key={tick}>
                <line
                  x1={padding.left}
                  y1={y}
                  x2={width - padding.right}
                  y2={y}
                  stroke="var(--line)"
                  strokeDasharray={tick === 0 ? "none" : "3,3"}
                  strokeWidth={tick === 0 ? 1.5 : 1}
                  opacity={tick === 0 ? 0.9 : 0.4}
                />
                <text
                  x={padding.left - 8}
                  y={y + 4}
                  textAnchor="end"
                  fontSize="10"
                  fill={tick === 0 ? "var(--fg)" : "var(--muted)"}
                  fontWeight={tick === 0 ? "600" : "normal"}
                >
                  {tick >= 0 ? `+${rupees(tick)}` : `-${rupees(Math.abs(tick))}`}
                </text>
              </g>
            );
          })}

          {xTicks.map((p) => {
            const x = scaleX(p);
            return (
              <g key={p}>
                <line x1={x} y1={padding.top} x2={x} y2={height - padding.bottom} stroke="var(--line)" strokeDasharray="3,3" opacity="0.3" />
                <text x={x} y={height - padding.bottom + 16} textAnchor="middle" fontSize="10" fill="var(--muted)">
                  {p.toLocaleString("en-IN")}
                </text>
              </g>
            );
          })}

          {/* Open Interest Bars in Background */}
          {showOi &&
            oiData.map((d) => {
              if (d.strike < minPrice || d.strike > maxPrice) return null;
              const x = scaleX(d.strike);
              const barHeightCall = (d.callOi / maxOi) * (plotHeight * 0.4);
              const barHeightPut = (d.putOi / maxOi) * (plotHeight * 0.4);
              return (
                <g key={d.strike} opacity="0.5">
                  {/* Call OI bar (top half) */}
                  <rect
                    x={x - 4}
                    y={yZero - barHeightCall}
                    width={3.5}
                    height={barHeightCall}
                    fill="#f97316"
                    rx={1}
                  />
                  {/* Put OI bar (bottom half) */}
                  <rect
                    x={x + 0.5}
                    y={yZero}
                    width={3.5}
                    height={barHeightPut}
                    fill="#22c55e"
                    rx={1}
                  />
                </g>
              );
            })}

          {/* Shaded Profit & Loss Areas */}
          {profitArea && <path d={profitArea} fill="url(#profitGrad)" />}
          {lossArea && <path d={lossArea} fill="url(#lossGrad)" />}

          {/* Zero Line (solid) */}
          <line
            x1={padding.left}
            y1={yZero}
            x2={width - padding.right}
            y2={yZero}
            stroke="var(--line-bold, var(--line))"
            strokeWidth={1.5}
          />

          {/* Payoff Curves */}
          {expiryPath && <path d={expiryPath} fill="none" stroke="#ef4444" strokeWidth={2.5} strokeLinejoin="round" />}
          {targetPath && <path d={targetPath} fill="none" stroke="#3b82f6" strokeWidth={2.5} strokeLinejoin="round" />}

          {/* Current Spot Line */}
          {currentSpot >= minPrice && currentSpot <= maxPrice && (
            <g>
              <line
                x1={scaleX(currentSpot)}
                y1={padding.top}
                x2={scaleX(currentSpot)}
                y2={height - padding.bottom}
                stroke="var(--fg)"
                strokeDasharray="4,4"
                strokeWidth={1.2}
                opacity="0.6"
              />
              <rect
                x={scaleX(currentSpot) - 34}
                y={padding.top - 18}
                width={68}
                height={16}
                fill="var(--panel)"
                stroke="var(--line)"
                rx={3}
              />
              <text
                x={scaleX(currentSpot)}
                y={padding.top - 6}
                textAnchor="middle"
                fontSize="9"
                fontWeight="600"
                fill="var(--fg)"
              >
                Spot {currentSpot.toLocaleString("en-IN")}
              </text>
            </g>
          )}

          {/* Target Price Vertical Marker */}
          {targetPrice >= minPrice && targetPrice <= maxPrice && (
            <g>
              <line
                x1={scaleX(targetPrice)}
                y1={padding.top}
                x2={scaleX(targetPrice)}
                y2={height - padding.bottom}
                stroke="#3b82f6"
                strokeDasharray="2,2"
                strokeWidth={1.5}
              />
              <rect
                x={scaleX(targetPrice) - 36}
                y={height - padding.bottom + 20}
                width={72}
                height={16}
                fill="#3b82f6"
                rx={3}
              />
              <text
                x={scaleX(targetPrice)}
                y={height - padding.bottom + 32}
                textAnchor="middle"
                fontSize="9"
                fontWeight="700"
                fill="#ffffff"
              >
                Target {targetPrice.toLocaleString("en-IN")}
              </text>
            </g>
          )}

          {/* Breakeven Marker lines */}
          {metrics.breakevens.map((be) => {
            if (be < minPrice || be > maxPrice) return null;
            const x = scaleX(be);
            return (
              <g key={be}>
                <line
                  x1={x}
                  y1={yZero - 12}
                  x2={x}
                  y2={yZero + 12}
                  stroke="#eab308"
                  strokeWidth={2}
                />
                <circle cx={x} cy={yZero} r={3} fill="#eab308" />
              </g>
            );
          })}

          {/* Mouse Hover Cursor Crosshair & Dot */}
          {hoveredPoint && hoverX !== null && (
            <g>
              <line
                x1={hoverX}
                y1={padding.top}
                x2={hoverX}
                y2={height - padding.bottom}
                stroke="var(--fg)"
                strokeWidth={1}
                opacity="0.4"
              />
              {/* Point on Expiry curve */}
              <circle cx={hoverX} cy={scaleY(hoveredPoint.pnlExpiry)} r={4} fill="#ef4444" stroke="#fff" strokeWidth={1.5} />
              {/* Point on Target curve */}
              <circle cx={hoverX} cy={scaleY(hoveredPoint.pnlTargetDate)} r={4} fill="#3b82f6" stroke="#fff" strokeWidth={1.5} />
            </g>
          )}
        </svg>

        {/* Floating Tooltip during hover */}
        {hoveredPoint && hoverX !== null && (
          <div
            style={{
              position: "absolute",
              top: 10,
              left: Math.min(Math.max(hoverX - 75, 80), width - 190),
              background: "var(--panel)",
              border: "1px solid var(--line)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
              borderRadius: "6px",
              padding: "6px 10px",
              fontSize: "11px",
              pointerEvents: "none",
              zIndex: 10,
              minWidth: 150,
            }}
          >
            <div className="row justify-between align-center" style={{ borderBottom: "1px solid var(--line)", paddingBottom: 4, marginBottom: 4 }}>
              <b>Price: {hoveredPoint.price.toLocaleString("en-IN")}</b>
              <span className={hoveredPoint.pctChange >= 0 ? "up" : "down"}>
                {hoveredPoint.pctChange >= 0 ? `+${hoveredPoint.pctChange}%` : `${hoveredPoint.pctChange}%`}
              </span>
            </div>
            <div className="row justify-between" style={{ color: "#ef4444" }}>
              <span>Expiry P&L:</span>
              <b>{hoveredPoint.pnlExpiry >= 0 ? `+${rupees(hoveredPoint.pnlExpiry)}` : `-${rupees(Math.abs(hoveredPoint.pnlExpiry))}`}</b>
            </div>
            <div className="row justify-between" style={{ color: "#3b82f6" }}>
              <span>Target P&L:</span>
              <b>{hoveredPoint.pnlTargetDate >= 0 ? `+${rupees(hoveredPoint.pnlTargetDate)}` : `-${rupees(Math.abs(hoveredPoint.pnlTargetDate))}`}</b>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
