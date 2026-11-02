import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, CrosshairMode, LineStyle, createChart,
  type IChartApi, type IPriceLine, type ISeriesApi, type MouseEventParams, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { api, ApiError } from "../lib/api";
import { toast, useTitle } from "../lib/app";
import { TF_LABEL, setChart, useChartSel, type ChartSel, type Drawing } from "../lib/chart";
import { dash, pct, px, signed, tone } from "../lib/format";
import { useQuote, useWant } from "../lib/quotes";
import { usePoll } from "../lib/store";
import { SearchBox } from "../components/SearchBox";
import { Empty } from "../components/ui";
import { IconTrash } from "../components/Icons";

interface ChartReply {
  ok: boolean;
  key: string;
  tf: string;
  since: number | null;
  minutes_recorded: number;
  invented: boolean;
  candles: [number, number, number, number, number, number][];
  inst: { label: string; lot: number; tick: number; segment: string };
}

type Tool = "cursor" | "h" | "t";

function css(name: string, fallback: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function palette() {
  return { text: css("--muted", "#8b98a8"), line: css("--line", "#212b37"), up: css("--up", "#2dbb82"), down: css("--down", "#f0605f"), accent: css("--accent", "#f0a23b") };
}
const fmtTime = (t: number, withDate = true) => {
  const d = new Date(t * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${withDate ? `${p(d.getUTCDate())} ${d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ` : ""}${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

export function ChartPage() {
  useTitle("Chart");
  const sel = useChartSel();
  const [tool, setTool] = useState<Tool>("cursor");
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [legend, setLegend] = useState<string>("");
  const [pending, setPending] = useState<{ t: number; p: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candle = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const hLines = useRef<Map<string, IPriceLine>>(new Map());
  const tLines = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const toolRef = useRef<Tool>("cursor");
  const pendingRef = useRef<typeof pending>(null);
  const drawRef = useRef<Drawing[]>([]);
  const fitted = useRef("");
  const saveRef = useRef<(d: Drawing[]) => void>(() => undefined);
  toolRef.current = tool;
  pendingRef.current = pending;
  drawRef.current = drawings;

  useWant(sel.key ? [sel.key] : []);
  const q = useQuote(sel.key || undefined);
  const { data, error } = usePoll<ChartReply>(sel.key ? `/chart?key=${encodeURIComponent(sel.key)}&tf=${sel.tf}&limit=1500` : null, 3000);
  const mine = data && data.key === sel.key && data.tf === sel.tf ? data : null;

  // ---- build the chart once
  useEffect(() => {
    if (!box.current) return;
    const c = palette();
    const ch = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: c.text, fontFamily: "inherit", fontSize: 12 },
      grid: { vertLines: { color: c.line }, horzLines: { color: c.line } },
      rightPriceScale: { borderColor: c.line },
      timeScale: { borderColor: c.line, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { timeFormatter: (t: number) => fmtTime(t) },
    });
    const cs = ch.addCandlestickSeries({ upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down, wickUpColor: c.up, wickDownColor: c.down });
    const vs = ch.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.current = ch;
    candle.current = cs;
    volume.current = vs;

    ch.subscribeCrosshairMove((p: MouseEventParams<Time>) => {
      const d = p.seriesData.get(cs) as { open: number; high: number; low: number; close: number } | undefined;
      const v = p.seriesData.get(vs) as { value: number } | undefined;
      if (!d || typeof p.time !== "number") return setLegend("");
      setLegend(`${fmtTime(p.time)}   O ${px(d.open)}  H ${px(d.high)}  L ${px(d.low)}  C ${px(d.close)}${v ? `  Vol ${Math.round(v.value).toLocaleString("en-IN")}` : ""}`);
    });
    ch.subscribeClick((p: MouseEventParams<Time>) => {
      if (toolRef.current === "cursor" || !p.point || typeof p.time !== "number") return;
      const price = cs.coordinateToPrice(p.point.y);
      if (price == null) return;
      const rounded = Math.round(price * 100) / 100;
      if (toolRef.current === "h") {
        saveRef.current([...drawRef.current, { id: uid(), type: "h", price: rounded }]);
        setTool("cursor");
      } else if (!pendingRef.current) {
        setPending({ t: p.time, p: rounded });
      } else {
        const a = pendingRef.current;
        if (a.t === p.time) return;
        const [first, second] = a.t < p.time ? [a, { t: p.time as number, p: rounded }] : [{ t: p.time as number, p: rounded }, a];
        saveRef.current([...drawRef.current, { id: uid(), type: "t", t1: first.t, p1: first.p, t2: second.t, p2: second.p }]);
        setPending(null);
        setTool("cursor");
      }
    });
    const mo = new MutationObserver(() => applyTheme());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", applyTheme);
    function applyTheme() {
      const k = palette();
      ch.applyOptions({ layout: { textColor: k.text }, grid: { vertLines: { color: k.line }, horzLines: { color: k.line } }, rightPriceScale: { borderColor: k.line }, timeScale: { borderColor: k.line } });
      cs.applyOptions({ upColor: k.up, downColor: k.down, borderUpColor: k.up, borderDownColor: k.down, wickUpColor: k.up, wickDownColor: k.down });
    }
    return () => {
      mo.disconnect();
      mq.removeEventListener("change", applyTheme);
      ch.remove();
      chart.current = candle.current = volume.current = null;
      hLines.current.clear();
      tLines.current.clear();
    };
  }, []);

  // ---- candles
  useEffect(() => {
    const cs = candle.current, vs = volume.current;
    if (!cs || !vs) return;
    if (!mine) return;
    const c = palette();
    cs.setData(mine.candles.map(([t, o, h, l, cl]) => ({ time: t as UTCTimestamp, open: o, high: h, low: l, close: cl })));
    vs.setData(mine.candles.map(([t, o, , , cl, v]) => ({ time: t as UTCTimestamp, value: v, color: cl >= o ? c.up + "55" : c.down + "55" })));
    const sig = `${sel.key}|${sel.tf}`;
    if (fitted.current !== sig && mine.candles.length) {
      fitted.current = sig;
      chart.current?.timeScale().fitContent();
    }
    cs.applyOptions({ priceFormat: { type: "price", precision: mine.inst.tick >= 1 ? 0 : 2, minMove: mine.inst.tick || 0.05 } });
  }, [mine, sel.key, sel.tf]);

  // ---- drawings: load per instrument
  useEffect(() => {
    setDrawings([]);
    setPending(null);
    if (!sel.key) return;
    let alive = true;
    api<{ drawings: Drawing[] }>(`/chart/drawings?key=${encodeURIComponent(sel.key)}`).then((r) => alive && setDrawings(r.drawings ?? [])).catch(() => undefined);
    return () => { alive = false; };
  }, [sel.key]);

  // ---- drawings: paint
  useEffect(() => {
    const cs = candle.current, ch = chart.current;
    if (!cs || !ch) return;
    const c = palette();
    const want = new Set(drawings.map((d) => d.id));
    for (const [id, l] of hLines.current) if (!want.has(id)) { cs.removePriceLine(l); hLines.current.delete(id); }
    for (const [id, s] of tLines.current) if (!want.has(id)) { ch.removeSeries(s); tLines.current.delete(id); }
    for (const d of drawings) {
      if (d.type === "h" && d.price != null && !hLines.current.has(d.id)) {
        hLines.current.set(d.id, cs.createPriceLine({ price: d.price, color: c.accent, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" }));
      }
      if (d.type === "t" && !tLines.current.has(d.id) && d.t1 != null && d.t2 != null && d.p1 != null && d.p2 != null) {
        const s = ch.addLineSeries({ color: c.accent, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData([{ time: d.t1 as UTCTimestamp, value: d.p1 }, { time: d.t2 as UTCTimestamp, value: d.p2 }]);
        tLines.current.set(d.id, s);
      }
    }
  }, [drawings]);

  async function save(next: Drawing[]) {
    setDrawings(next);
    try {
      const r = await api<{ drawings: Drawing[] }>(`/chart/drawings?key=${encodeURIComponent(sel.key)}`, { method: "PUT", body: { drawings: next } });
      setDrawings(r.drawings);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Could not save the drawing", "err");
    }
  }

  saveRef.current = save;
  const t = tone(q?.c);
  const lastKnown = q?.l ?? mine?.candles[mine.candles.length - 1]?.[4];
  const since = mine?.since ? fmtTime(mine.since) : null;
  const toolHint = useMemo(() => {
    if (tool === "h") return "Click the chart where the line should go.";
    if (tool === "t") return pending ? "Now click the second point." : "Click the first point of the trend line.";
    return "";
  }, [tool, pending]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Chart</h1>
          <p>Candles built from live prices while this terminal runs.</p>
        </div>
      </div>

      <div className="row chart-bar" style={{ flexWrap: "wrap", gap: 10, marginBottom: 10 }}>
        <SearchBox placeholder="Search a chart: RELIANCE, NIFTY 24500 CE, GOLD…" onPick={(i) => setChart({ key: i.key, label: i.label })} />
        <div className="seg" role="group" aria-label="Timeframe">
          {(Object.keys(TF_LABEL) as ChartSel["tf"][]).map((k) => (
            <button key={k} aria-pressed={sel.tf === k} onClick={() => setChart({ tf: k })}>{TF_LABEL[k]}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Drawing tools">
          <button aria-pressed={tool === "cursor"} onClick={() => { setTool("cursor"); setPending(null); }}>Cursor</button>
          <button aria-pressed={tool === "h"} onClick={() => { setTool("h"); setPending(null); }} disabled={!sel.key}>Horizontal line</button>
          <button aria-pressed={tool === "t"} onClick={() => { setTool("t"); setPending(null); }} disabled={!sel.key}>Trend line</button>
        </div>
        {toolHint && <span className="hint" role="status">{toolHint}</span>}
      </div>

      {!sel.key ? (
        <Empty title="Pick something to chart">Search above for a stock, index, future, option or commodity. The chart starts recording it from now.</Empty>
      ) : (
        <section className="panel chart-panel">
          <div className="panel-h chart-h">
            <b>{sel.label || mine?.inst.label || sel.key}</b>
            <span className="chart-px">{lastKnown != null ? px(lastKnown) : dash}</span>
            <span className={`chart-chg ${t}`}>{signed(q?.c)} {pct(q?.p)}</span>
            <span className="grow" />
            <span className="legend" aria-live="off">{legend}</span>
          </div>
          <div className="chart-box" ref={box} role="img" aria-label={`Candlestick chart of ${sel.label}`} />
          {error && <div className="err" style={{ padding: 10 }}>{error}</div>}
        </section>
      )}

      {sel.key && (
        <>
          <p className="hint chart-note">
            {mine?.invented ? "Sample mode: the earlier candles are INVENTED so there is something to look at; new candles follow the sample prices. " : ""}
            {!mine?.invented && since ? `Recording since ${since} (${mine?.minutes_recorded} one-minute candles). ` : ""}
            {!mine?.invented && !since && mine ? "Nothing recorded yet: candles appear as prices arrive. " : ""}
            {!mine?.invented ? "Kotak gives no price history, so a chart can only show what the terminal itself watched; the last 30 instruments you open are recorded while it runs." : ""}
          </p>
          {drawings.length > 0 && (
            <div className="chips" aria-label="Your drawings">
              {drawings.map((d) => (
                <span className="chip dr" key={d.id}>
                  {d.type === "h" ? `Line ${px(d.price)}` : `Trend ${fmtTime(d.t1 ?? 0, false)} → ${fmtTime(d.t2 ?? 0, false)}`}
                  <button className="btn ghost icon sm" aria-label="Delete this drawing" title="Delete" onClick={() => save(drawings.filter((x) => x.id !== d.id))}><IconTrash /></button>
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}
