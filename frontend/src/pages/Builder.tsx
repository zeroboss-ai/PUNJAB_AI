import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import { KIND_LABEL, KINDS, OP_LABEL, SRC_LABEL, TFS, newCondition, operandOf, type Condition, type Operand, type Rule, type Spec, type StrategyRow, type Template } from "../lib/strategy";
import { IconX } from "../components/Icons";

interface Check {
  ok: boolean;
  errors: string[];
  warnings: string[];
  summary: string[];
}

const num = (v: string, d = 0) => (v === "" || isNaN(Number(v)) ? d : Number(v));

// -------------------------------------------------------------------------------- operand editor
export function OperandEditor({ v, onChange, selfOnly = false, label, srcs }: { v: Operand; onChange: (o: Operand) => void; selfOnly?: boolean; label: string; srcs?: string[] }) {
  const set = (p: Partial<Operand>) => onChange({ ...v, ...p });
  const kinds = selfOnly ? KINDS.filter((k) => k !== "number") : KINDS;
  const isNum = v.kind === "number";
  return (
    <div className="operand" role="group" aria-label={label}>
      <select className="select" aria-label={`${label}: what to compare`} value={v.kind} onChange={(e) => onChange(operandOf(e.target.value, v))}>
        {kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
      </select>
      {!isNum && !selfOnly && (
        <select className="select" aria-label={`${label}: of which instrument`} value={v.src} onChange={(e) => set({ src: e.target.value as Operand["src"] })}>
          {Object.entries(SRC_LABEL).filter(([k]) => !srcs || srcs.includes(k)).map(([k, t]) => <option key={k} value={k}>of {t}</option>)}
        </select>
      )}
      {isNum && <input className="input num" type="number" step="any" aria-label={`${label}: number`} value={v.value ?? 0} onChange={(e) => set({ value: num(e.target.value) })} />}
      {(v.kind === "open_range_high" || v.kind === "open_range_low") && (
        <>
          <label className="mini">from <input className="input" type="time" value={v.start ?? "09:15"} onChange={(e) => set({ start: e.target.value })} /></label>
          <label className="mini">first <input className="input num" type="number" min={1} max={60} value={v.candles ?? 4} onChange={(e) => set({ candles: num(e.target.value, 1) })} /> one-minute candles</label>
        </>
      )}
      {v.kind === "candle" && (
        <>
          <select className="select" aria-label={`${label}: candle value`} value={v.field} onChange={(e) => set({ field: e.target.value })}>
            {["open", "high", "low", "close"].map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <label className="mini">of the one-minute candle at <input className="input" type="time" value={v.at ?? "09:16"} onChange={(e) => set({ at: e.target.value })} /></label>
        </>
      )}
      {v.kind === "prev_candle" && (
        <>
          <select className="select" aria-label={`${label}: candle value`} value={v.field} onChange={(e) => set({ field: e.target.value })}>
            {["open", "high", "low", "close"].map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <label className="mini">candle size <select className="select" value={v.tf} onChange={(e) => set({ tf: Number(e.target.value) })}>{TFS.map((t) => <option key={t} value={t}>{t} min</option>)}</select></label>
        </>
      )}
      {["ema", "sma", "rsi"].includes(v.kind) && (
        <>
          <label className="mini">period <input className="input num" type="number" min={v.kind === "rsi" ? 2 : 1} max={200} value={v.period ?? 14} onChange={(e) => set({ period: num(e.target.value, 14) })} /></label>
          <label className="mini">candle size <select className="select" value={v.tf} onChange={(e) => set({ tf: Number(e.target.value) })}>{TFS.map((t) => <option key={t} value={t}>{t} min</option>)}</select></label>
        </>
      )}
      {!isNum && (
        <label className="mini" title="Added to the value, e.g. +2 means 'two points above'">± <input className="input num" type="number" step="any" aria-label={`${label}: points added`} value={v.offset ?? 0} onChange={(e) => set({ offset: num(e.target.value) })} /> pts</label>
      )}
    </div>
  );
}

function ConditionEditor({ c, onChange, onRemove, selfOnly, n, srcs }: { c: Condition; onChange: (c: Condition) => void; onRemove: () => void; selfOnly?: boolean; n: number; srcs?: string[] }) {
  return (
    <div className="cond">
      <div className="cond-n">{n}</div>
      <div className="cond-body">
        <OperandEditor v={c.left} onChange={(left) => onChange({ ...c, left })} selfOnly={selfOnly} srcs={srcs} label={`Condition ${n} left side`} />
        <select className="select op" aria-label={`Condition ${n} comparison`} value={c.op} onChange={(e) => onChange({ ...c, op: e.target.value })}>
          {Object.entries(OP_LABEL).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
        </select>
        <OperandEditor v={c.right} onChange={(right) => onChange({ ...c, right })} selfOnly={selfOnly} srcs={srcs} label={`Condition ${n} right side`} />
      </div>
      <button className="btn ghost icon sm" onClick={onRemove} aria-label={`Remove condition ${n}`}><IconX /></button>
    </div>
  );
}

export function ConditionList({ list, onChange, max = 6, srcs }: { list: Condition[]; onChange: (l: Condition[]) => void; max?: number; srcs?: string[] }) {
  return (
    <>
      {list.map((c, i) => (
        <ConditionEditor key={i} c={c} n={i + 1} srcs={srcs} onChange={(x) => onChange(list.map((y, j) => (j === i ? x : y)))} onRemove={() => onChange(list.filter((_, j) => j !== i))} />
      ))}
      {list.length < max && <button className="btn sm" onClick={() => onChange([...list, newCondition()])}>+ Add a condition</button>}
    </>
  );
}

// ---------------------------------------------------------------------------------------------- page
export function Builder() {
  useTitle("Strategy builder");
  const s = useSession();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const editId = params.get("id") ? Number(params.get("id")) : null;
  const [templates, setTemplates] = useState<Template[]>([]);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [locked, setLocked] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [tplNote, setTplNote] = useState("");
  const seq = useRef(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const t = await api<{ templates: Template[] }>("/strategy-templates");
        if (!alive) return;
        setTemplates(t.templates.filter((x) => x.engine !== "legs"));
        if (editId != null) {
          const r = await api<{ strategy: StrategyRow }>(`/strategies/${editId}`);
          if (!alive) return;
          if ("legs" in r.strategy.spec) { navigate(`/leg-builder?id=${editId}`); return; }
          setSpec(r.strategy.spec as Spec);
          setLocked(r.strategy.running);
        } else {
          const want = params.get("template") ?? "blank";
          const rules = t.templates.filter((x) => x.engine !== "legs");
          const tpl = rules.find((x) => x.id === want) ?? rules[0];
          setSpec(structuredClone(tpl.spec as Spec));
          setTplNote(tpl.caution);
        }
      } catch (e) {
        toast((e as Error).message, "err");
      }
    })();
    return () => { alive = false; };
  }, [editId, params]);

  // Re-check with the server (the single source of truth for the rules) shortly after every change.
  useEffect(() => {
    if (!spec) return;
    const my = ++seq.current;
    const id = window.setTimeout(async () => {
      try {
        const r = await api<Check>("/strategies/validate", { method: "POST", body: { spec } });
        if (my === seq.current) setCheck(r);
      } catch (e) {
        if (my === seq.current) setCheck({ ok: false, errors: [(e as Error).message], warnings: [], summary: [] });
      }
    }, 350);
    return () => window.clearTimeout(id);
  }, [spec]);

  if (!spec) return <div className="empty"><b>Loading…</b></div>;
  const up = (p: Partial<Spec>) => setSpec({ ...spec, ...p });
  const upExit = (p: Partial<Spec["exit"]>) => setSpec({ ...spec, exit: { ...spec.exit, ...p } });
  const upLim = (p: Partial<Spec["limits"]>) => setSpec({ ...spec, limits: { ...spec.limits, ...p } });
  const setRule = (i: number, r: Rule) => up({ entries: spec.entries.map((x, j) => (j === i ? r : x)) });
  const stop = spec.exit.stop;
  const tgt = spec.exit.target;
  const usedLegs = new Set(spec.entries.map((e) => e.leg));

  async function save() {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; id?: number; errors?: string[] }>(editId != null ? `/strategies/${editId}` : "/strategies", { method: editId != null ? "PUT" : "POST", body: { spec } });
      if (!r.ok) {
        setCheck({ ok: false, errors: r.errors ?? ["Not saved"], warnings: [], summary: [] });
        return;
      }
      toast("Strategy saved");
      navigate("/strategies");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  function pickTemplate(id: string) {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    setSpec(structuredClone(t.spec as Spec));
    setTplNote(t.caution);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{editId != null ? "Edit strategy" : "New strategy"}</h1>
          <p>Build the rules with blocks. The plain-English box on the right always shows exactly what will run. Nothing trades until you press Start on the My strategies page.</p>
        </div>
        <div className="row">
          <label className="mini">Start from <select className="select" value="" onChange={(e) => pickTemplate(e.target.value)} aria-label="Start from a template">
            <option value="">template…</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select></label>
          <button className="btn" onClick={() => navigate("/strategies")}>Cancel</button>
          <button className="btn primary" disabled={busy || locked || !check?.ok} onClick={save}>{busy ? "Saving…" : "Save strategy"}</button>
        </div>
      </div>

      {locked && <div className="notice bad" style={{ marginBottom: 12 }}>This strategy is running. Stop it on the My strategies page before editing.</div>}
      {tplNote && <div className="notice" style={{ marginBottom: 12 }}>{tplNote}</div>}

      <div className="builder">
        <div className="builder-main">
          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">1. What to trade</div>
            <div className="panel-b form-grid">
              <div className="field span2"><label htmlFor="sname">Name</label><input id="sname" className="input" maxLength={60} value={spec.name} onChange={(e) => up({ name: e.target.value })} /></div>
              <div className="field"><span className="lbl">Index</span>
                <div className="seg" role="group" aria-label="Underlying">
                  {["NIFTY", "BANKNIFTY", "SENSEX"].map((u) => <button key={u} aria-pressed={spec.underlying === u} onClick={() => up({ underlying: u })}>{u}</button>)}
                </div>
              </div>
              <div className="field"><label htmlFor="sexp">Expiry</label>
                <select id="sexp" className="select" value={spec.expiry} onChange={(e) => up({ expiry: e.target.value })}>
                  <option value="nearest">Nearest expiry</option><option value="next">Next expiry</option>
                </select>
              </div>
              <div className="field"><label htmlFor="slots">Lots per trade</label><input id="slots" className="input num" type="number" min={1} max={50} value={spec.lots} onChange={(e) => up({ lots: num(e.target.value, 1) })} /></div>
              <div className="field"><span className="lbl">Product</span>
                <div className="seg" role="group" aria-label="Product">
                  {(["MIS", "NRML"] as const).map((p) => <button key={p} aria-pressed={spec.product === p} onClick={() => up({ product: p })}>{p === "MIS" ? "Intraday (MIS)" : "Carry (NRML)"}</button>)}
                </div>
              </div>
              <div className="field"><label htmlFor="slock">Choose strikes at</label><input id="slock" className="input" type="time" value={spec.lock_time} onChange={(e) => up({ lock_time: e.target.value })} /></div>
              <div className="field"><label htmlFor="soce">Call strike offset</label><input id="soce" className="input num" type="number" min={-20} max={20} value={spec.offset_ce} onChange={(e) => up({ offset_ce: num(e.target.value) })} /></div>
              <div className="field"><label htmlFor="sope">Put strike offset</label><input id="sope" className="input num" type="number" min={-20} max={20} value={spec.offset_pe} onChange={(e) => up({ offset_pe: num(e.target.value) })} /></div>
              <p className="hint span2">The two strikes are picked once, at the time above, from the index price then, and stay fixed for the day. 0 = at the money. Each step is one strike; + means a higher strike.</p>
            </div>
          </fieldset>

          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">2. When to enter</div>
            <div className="panel-b stack">
              {spec.entries.map((r, i) => (
                <div className="rule" key={i}>
                  <div className="rule-h">
                    <div className="seg side" role="group" aria-label={`Entry ${i + 1} action`}>
                      <button className="s" aria-pressed={r.side === "S"} onClick={() => setRule(i, { ...r, side: "S" })}>Sell</button>
                      <button className="b" aria-pressed={r.side === "B"} onClick={() => setRule(i, { ...r, side: "B" })}>Buy</button>
                    </div>
                    <span>the</span>
                    <div className="seg" role="group" aria-label={`Entry ${i + 1} option`}>
                      <button aria-pressed={r.leg === "CE"} disabled={r.leg !== "CE" && usedLegs.has("CE")} onClick={() => setRule(i, { ...r, leg: "CE" })}>Call</button>
                      <button aria-pressed={r.leg === "PE"} disabled={r.leg !== "PE" && usedLegs.has("PE")} onClick={() => setRule(i, { ...r, leg: "PE" })}>Put</button>
                    </div>
                    <span>when</span>
                    <div className="seg" role="group" aria-label={`Entry ${i + 1} match`}>
                      <button aria-pressed={r.match === "all"} onClick={() => setRule(i, { ...r, match: "all" })}>all</button>
                      <button aria-pressed={r.match === "any"} onClick={() => setRule(i, { ...r, match: "any" })}>any</button>
                    </div>
                    <span>of these are true</span>
                    <span className="grow" />
                    {spec.entries.length > 1 && <button className="btn ghost sm" onClick={() => up({ entries: spec.entries.filter((_, j) => j !== i) })}>Remove rule</button>}
                  </div>
                  <ConditionList list={r.conditions} onChange={(conditions) => setRule(i, { ...r, conditions })} />
                  {!r.conditions.length && <p className="hint">No conditions: it enters as soon as the entry window (section 4) opens.</p>}
                </div>
              ))}
              {spec.entries.length < 2 && (
                <button className="btn sm" onClick={() => up({ entries: [...spec.entries, { side: "S", leg: usedLegs.has("CE") ? "PE" : "CE", match: "all", conditions: [] }] })}>+ Add another entry rule (the other option)</button>
              )}
              <p className="hint">A signal fires once. After it, the conditions must turn false and true again before it can fire again. A signal already true when you press Start is ignored.</p>
            </div>
          </fieldset>

          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">3. How to exit</div>
            <div className="panel-b form-grid">
              <div className="field"><label htmlFor="sstop">Stop-loss</label>
                <select id="sstop" className="select" value={stop.mode} onChange={(e) => upExit({ stop: { mode: e.target.value as typeof stop.mode, value: stop.value || 25, level: e.target.value === "level" ? stop.level ?? operandOf("candle", { kind: "x", src: "SELF" }) : null } })}>
                  <option value="none">None</option>
                  <option value="points">Points against the entry price</option>
                  <option value="percent">Percent against the entry price</option>
                  <option value="level">A price level from the chart</option>
                </select>
              </div>
              {(stop.mode === "points" || stop.mode === "percent") && (
                <div className="field"><label htmlFor="sstopv">{stop.mode === "points" ? "Points" : "Percent"}</label><input id="sstopv" className="input num" type="number" step="any" min={0} value={stop.value ?? 0} onChange={(e) => upExit({ stop: { ...stop, value: num(e.target.value) } })} /></div>
              )}
              {stop.mode === "level" && stop.level && (
                <div className="field span2"><span className="lbl">Stop-loss level (fixed at the moment you enter)</span>
                  <OperandEditor v={stop.level} selfOnly label="Stop-loss level" onChange={(level) => upExit({ stop: { ...stop, level } })} />
                </div>
              )}
              <div className="field"><label htmlFor="starget">Target</label>
                <select id="starget" className="select" value={tgt.mode} onChange={(e) => upExit({ target: { mode: e.target.value as typeof tgt.mode, value: tgt.value || 20 } })}>
                  <option value="none">None</option><option value="points">Points in your favour</option><option value="percent">Percent in your favour</option>
                </select>
              </div>
              {tgt.mode !== "none" && <div className="field"><label htmlFor="stv">{tgt.mode === "points" ? "Points" : "Percent"}</label><input id="stv" className="input num" type="number" step="any" min={0} value={tgt.value ?? 0} onChange={(e) => upExit({ target: { ...tgt, value: num(e.target.value) } })} /></div>}
              <div className="field"><label htmlFor="sexit">Close everything at</label><input id="sexit" className="input" type="time" value={spec.exit.time} onChange={(e) => upExit({ time: e.target.value })} /></div>
              <label className="check span2">
                <input type="checkbox" disabled={stop.mode === "none"} checked={spec.exit.exchange_sl && stop.mode !== "none"} onChange={(e) => upExit({ exchange_sl: e.target.checked })} />
                <span><b>Also place the stop-loss as an exchange order.</b> It then protects you even if this computer loses power or internet. The software stop-loss stands down while the exchange order is active. If the order cannot be placed, the position is closed straight away.</span>
              </label>
              <div className="span2 stack">
                <span className="lbl">Extra exit rule (optional): close early when
                  {" "}<span className="seg" role="group" aria-label="Exit match"><button aria-pressed={spec.exit.match === "any"} onClick={() => upExit({ match: "any" })}>any</button><button aria-pressed={spec.exit.match === "all"} onClick={() => upExit({ match: "all" })}>all</button></span> of these are true
                </span>
                <ConditionList list={spec.exit.conditions} onChange={(conditions) => upExit({ conditions })} />
              </div>
            </div>
          </fieldset>

          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">4. Limits</div>
            <div className="panel-b form-grid">
              <div className="field"><label htmlFor="lwin0">Enter no earlier than</label><input id="lwin0" className="input" type="time" value={spec.limits.window_start} onChange={(e) => upLim({ window_start: e.target.value })} /></div>
              <div className="field"><label htmlFor="lwin1">Enter no later than</label><input id="lwin1" className="input" type="time" value={spec.limits.window_end} onChange={(e) => upLim({ window_end: e.target.value })} /></div>
              <div className="field"><label htmlFor="lmax">Most entries per day (0 = no limit)</label><input id="lmax" className="input num" type="number" min={0} max={100} value={spec.limits.max_entries} onChange={(e) => upLim({ max_entries: Math.max(0, Math.round(num(e.target.value, 0))) })} /></div>
              <div className="field"><label htmlFor="lloss">Max loss for this strategy (₹, 0 = no limit)</label><input id="lloss" className="input num" type="number" min={0} step={100} value={spec.limits.max_loss} onChange={(e) => upLim({ max_loss: num(e.target.value) })} /></div>
              <div className="field"><label htmlFor="lslip">Order price: % beyond the last price</label><input id="lslip" className="input num" type="number" min={0.1} max={10} step={0.1} value={spec.limits.slippage_pct} onChange={(e) => upLim({ slippage_pct: num(e.target.value, 1) })} /></div>
              <p className="hint span2">Entry and exit orders are limit orders placed this far through the last price so they fill. These limits are your own rules: leave them at 0 for none. A daily loss limit, if you set one in config.yaml, also applies.</p>
            </div>
          </fieldset>
        </div>

        <aside className="builder-side">
          <section className="panel sticky">
            <div className="panel-h">What this will do</div>
            <div className="panel-b stack">
              {check == null && <span className="hint">Checking…</span>}
              {check?.errors.map((e, i) => <div key={i} className="notice bad" role="alert">{e}</div>)}
              {check?.ok && (
                <ol className="plain">
                  {check.summary.map((l, i) => <li key={i}>{l}</li>)}
                </ol>
              )}
              {check?.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}
              <p className="hint">
                {s?.mode === "live" ? "Mode: LIVE. A running strategy places real orders." : s?.mode === "paper" ? "Mode: paper. Real prices, simulated orders." : "Mode: sample. Invented prices, simulated orders."}
                {" "}Candles are built from the moment the terminal starts watching, so start before any candle time you use. Strategies stop if you restart the terminal.
              </p>
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}
