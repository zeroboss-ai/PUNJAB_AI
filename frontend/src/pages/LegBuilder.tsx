import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import {
  MAX_LEGS, REENTRY_LABEL, STRIKE_BY, TRIGGER_LABEL, UNDERLYINGS, UND_LABEL, COMMODITY_UNDERLYINGS, COMMODITY_LABEL, MARKET_TIMES, isCommodity, newLeg, noReentry, strikeOf, timeTrigger,
  type LegCfg, type LegSpec, type Reentry, type RiskRule, type Trigger,
} from "../lib/legs";
import { newCondition, type StrategyRow, type Template } from "../lib/strategy";
import { ConditionList } from "./Builder";

interface Check { ok: boolean; errors: string[]; warnings: string[]; summary: string[] }

const num = (v: string, d = 0) => (v === "" || isNaN(Number(v)) ? d : Number(v));
const unitOf = (m: string) => (m === "percent" ? "%" : "points");

function Num({ label, value, onChange, min, max, step = "any", w }: { label: string; value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number | "any"; w?: number }) {
  return <input className="input num" style={w ? { width: w } : undefined} type="number" aria-label={label} min={min} max={max} step={step} value={value} onChange={(e) => onChange(num(e.target.value))} />;
}

/** Stop-loss / target: none | points | percent. */
function RiskEditor({ what, v, onChange }: { what: string; v: RiskRule; onChange: (r: RiskRule) => void }) {
  return (
    <div className="opt">
      <div className="opt-h">{what}</div>
      <div className="row wrap">
        <select className="select" aria-label={`${what} type`} value={v.mode} onChange={(e) => onChange({ mode: e.target.value as RiskRule["mode"], value: e.target.value === "none" ? 0 : v.value || (what === "Stop-loss" ? 25 : 30) })}>
          <option value="none">None</option><option value="points">Points</option><option value="percent">Percent</option>
        </select>
        {v.mode !== "none" && <><Num label={`${what} value`} value={v.value} min={0} onChange={(value) => onChange({ ...v, value })} /><span className="mini">{unitOf(v.mode)} {what === "Stop-loss" ? "against" : "in favour"}</span></>}
      </div>
    </div>
  );
}

function ReentryEditor({ what, v, onChange, enabled }: { what: string; v: Reentry; onChange: (r: Reentry) => void; enabled: boolean }) {
  return (
    <div className="opt">
      <div className="opt-h">Re-entry after {what}</div>
      <div className="stack tight">
        <select className="select" aria-label={`Re-entry after ${what}`} disabled={!enabled} value={v.mode} onChange={(e) => onChange({ ...noReentry(), ...v, mode: e.target.value as Reentry["mode"], value: v.value || 10 })}>
          {Object.entries(REENTRY_LABEL).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
        </select>
        {!enabled && <span className="hint">Set a {what} first.</span>}
        {enabled && v.mode === "momentum" && (
          <div className="row wrap">
            <span className="mini">after a move of</span>
            <Num label="Momentum size" value={v.value} min={0} onChange={(value) => onChange({ ...v, value })} />
            <select className="select" aria-label="Momentum unit" value={v.unit} onChange={(e) => onChange({ ...v, unit: e.target.value as Reentry["unit"] })}><option value="points">points</option><option value="percent">%</option></select>
          </div>
        )}
        {enabled && v.mode !== "none" && (
          <div className="row wrap">
            <label className="mini">up to <Num label="Re-entry count" value={v.count} min={1} max={10} step={1} w={64} onChange={(count) => onChange({ ...v, count: Math.round(count) || 1 })} /> time(s)</label>
            <label className="check inline"><input type="checkbox" checked={v.reverse} onChange={(e) => onChange({ ...v, reverse: e.target.checked })} /><span>Reverse (take the opposite side)</span></label>
          </div>
        )}
      </div>
    </div>
  );
}

function StrikeEditor({ leg, onChange }: { leg: LegCfg; onChange: (s: LegCfg["strike"]) => void }) {
  const s = leg.strike;
  const by = STRIKE_BY.find((b) => b.id === s.by);
  return (
    <div className="opt wide">
      <div className="opt-h">Strike criteria</div>
      <div className="row wrap">
        <select className="select" aria-label="Strike criteria" value={s.by} onChange={(e) => onChange(strikeOf(e.target.value, s))}>
          {STRIKE_BY.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
        </select>
        {s.by === "atm" && (
          <>
            <select className="select" aria-label="ATM, OTM or ITM" value={s.steps === 0 ? "atm" : s.steps > 0 ? "otm" : "itm"}
              onChange={(e) => onChange({ ...s, steps: e.target.value === "atm" ? 0 : (e.target.value === "otm" ? 1 : -1) * Math.max(1, Math.abs(s.steps)) })}>
              <option value="atm">ATM</option><option value="otm">OTM</option><option value="itm">ITM</option>
            </select>
            {s.steps !== 0 && <><Num label="Strikes away" value={Math.abs(s.steps)} min={1} max={30} step={1} w={64} onChange={(n) => onChange({ ...s, steps: (s.steps > 0 ? 1 : -1) * Math.max(1, Math.round(n)) })} /><span className="mini">strike(s)</span></>}
          </>
        )}
        {s.by === "synthetic" && <><span className="mini">steps from the synthetic future (+ out, - in)</span><Num label="Steps" value={s.steps} min={-30} max={30} step={1} w={72} onChange={(n) => onChange({ ...s, steps: Math.round(n) })} /></>}
        {["premium_closest", "premium_gte", "premium_lte"].includes(s.by) && <><span className="mini">₹</span><Num label="Premium" value={s.value} min={0} onChange={(value) => onChange({ ...s, value })} /></>}
        {(s.by === "premium_range" || s.by === "delta_range") && (
          <>
            <span className="mini">between</span><Num label="Lower" value={s.lo} min={0} step={s.by === "delta_range" ? 0.01 : "any"} onChange={(lo) => onChange({ ...s, lo })} />
            <span className="mini">and</span><Num label="Upper" value={s.hi} min={0} step={s.by === "delta_range" ? 0.01 : "any"} onChange={(hi) => onChange({ ...s, hi })} />
          </>
        )}
        {s.by === "delta_closest" && <><span className="mini">delta</span><Num label="Delta" value={s.value} min={0.02} max={0.98} step={0.01} onChange={(value) => onChange({ ...s, value })} /></>}
        {s.by === "pct_atm" && <><Num label="Percent from ATM" value={s.value} min={-100} max={100} onChange={(value) => onChange({ ...s, value })} /><span className="mini">% from ATM (− = in the money)</span></>}
        {s.by === "straddle" && <><Num label="Percent of straddle" value={s.value} min={-100} max={100} onChange={(value) => onChange({ ...s, value })} /><span className="mini">% of the ATM straddle price (− = in)</span></>}
      </div>
      {by && <span className="hint">{by.hint}</span>}
    </div>
  );
}

function TriggerEditor({ leg, onChange }: { leg: LegCfg; onChange: (t: Trigger) => void }) {
  const t = leg.trigger;
  return (
    <div className="opt wide">
      <div className="opt-h">Entry trigger</div>
      <div className="stack tight">
        <select className="select" aria-label="Entry trigger" value={t.type} onChange={(e) => {
          const type = e.target.value as Trigger["type"];
          onChange({ ...timeTrigger(), type, value: type === "momentum" ? 10 : 0, unit: type === "momentum" ? "percent" : "points", conditions: type === "condition" ? [newCondition()] : [] });
        }}>
          {Object.entries(TRIGGER_LABEL).map(([k, x]) => <option key={k} value={k}>{x}</option>)}
        </select>
        {t.type === "time" && <span className="hint">Enters at the strategy's entry time.</span>}
        {t.type === "momentum" && (
          <div className="row wrap">
            <span className="mini">enter when the price has moved</span>
            <select className="select" aria-label="Momentum direction" value={t.dir} onChange={(e) => onChange({ ...t, dir: e.target.value as Trigger["dir"] })}><option value="up">up</option><option value="down">down</option></select>
            <Num label="Momentum size" value={t.value} min={0} onChange={(value) => onChange({ ...t, value })} />
            <select className="select" aria-label="Momentum unit" value={t.unit} onChange={(e) => onChange({ ...t, unit: e.target.value as Trigger["unit"] })}><option value="points">points</option><option value="percent">%</option></select>
            <span className="mini">from the price when the strike was chosen</span>
          </div>
        )}
        {t.type === "range" && (
          <div className="row wrap">
            <span className="mini">record the range of</span>
            <select className="select" aria-label="Range of" value={t.of} onChange={(e) => onChange({ ...t, of: e.target.value as Trigger["of"] })}>
              <option value="option">{leg.segment === "futures" ? "this future" : "this option"}</option><option value="underlying">the index / commodity future</option>
            </select>
            <span className="mini">until</span><input className="input" type="time" aria-label="Range ends" value={t.until} onChange={(e) => onChange({ ...t, until: e.target.value })} />
            <span className="mini">enter on a break of the range</span>
            <select className="select" aria-label="Range side" value={t.side} onChange={(e) => onChange({ ...t, side: e.target.value as Trigger["side"] })}><option value="high">high</option><option value="low">low</option></select>
          </div>
        )}
        {t.type === "condition" && (
          <div className="stack">
            <span className="lbl">Enter when <span className="seg" role="group" aria-label="Match"><button aria-pressed={t.match === "all"} onClick={() => onChange({ ...t, match: "all" })}>all</button><button aria-pressed={t.match === "any"} onClick={() => onChange({ ...t, match: "any" })}>any</button></span> of these are true</span>
            <ConditionList list={t.conditions} srcs={["INDEX", "SELF"]} onChange={(conditions) => onChange({ ...t, conditions })} />
          </div>
        )}
      </div>
    </div>
  );
}

function LegCard({ n, leg, onChange, onRemove, onCopy, canAdd }: { n: number; leg: LegCfg; onChange: (l: LegCfg) => void; onRemove: () => void; onCopy: () => void; canAdd: boolean }) {
  const [more, setMore] = useState(true);
  const up = (p: Partial<LegCfg>) => onChange({ ...leg, ...p });
  const fut = leg.segment === "futures";
  return (
    <div className="leg">
      <div className="leg-h">
        <span className="leg-n">Leg {n}</span>
        <div className="seg" role="group" aria-label={`Leg ${n} segment`}>
          <button aria-pressed={!fut} onClick={() => up({ segment: "options" })}>Options</button>
          <button aria-pressed={fut} onClick={() => up({ segment: "futures" })}>Futures</button>
        </div>
        <label className="mini">Lots <Num label={`Leg ${n} lots`} value={leg.lots} min={1} max={50} step={1} w={64} onChange={(x) => up({ lots: Math.max(1, Math.round(x)) })} /></label>
        <div className="seg side" role="group" aria-label={`Leg ${n} side`}>
          <button className="b" aria-pressed={leg.side === "B"} onClick={() => up({ side: "B" })}>Buy</button>
          <button className="s" aria-pressed={leg.side === "S"} onClick={() => up({ side: "S" })}>Sell</button>
        </div>
        {!fut && (
          <div className="seg" role="group" aria-label={`Leg ${n} option type`}>
            <button aria-pressed={leg.kind === "CE"} onClick={() => up({ kind: "CE" })}>Call</button>
            <button aria-pressed={leg.kind === "PE"} onClick={() => up({ kind: "PE" })}>Put</button>
          </div>
        )}
        <select className="select" aria-label={`Leg ${n} expiry`} value={leg.expiry} onChange={(e) => up({ expiry: e.target.value as LegCfg["expiry"] })}>
          <option value="nearest">Nearest expiry</option><option value="next">Next expiry</option><option value="monthly">Monthly expiry</option>
        </select>
        <span className="grow" />
        <button className="btn ghost sm" onClick={() => setMore(!more)} aria-expanded={more}>{more ? "Hide settings" : "Show settings"}</button>
        <button className="btn ghost sm" disabled={!canAdd} onClick={onCopy}>Duplicate</button>
        <button className="btn ghost sm" onClick={onRemove}>Remove</button>
      </div>
      {more && (
        <div className="leg-grid">
          {!fut && <StrikeEditor leg={leg} onChange={(strike) => up({ strike })} />}
          {fut && <div className="opt wide"><div className="opt-h">Instrument</div><span className="hint">The index future of the chosen expiry. Futures need much more margin than options.</span></div>}
          <TriggerEditor leg={leg} onChange={(trigger) => up({ trigger })} />
          <RiskEditor what="Stop-loss" v={leg.sl} onChange={(sl) => up({ sl, trail: sl.mode === "none" ? { on: false, every: 0, by: 0 } : leg.trail, reentry_sl: sl.mode === "none" ? noReentry() : leg.reentry_sl })} />
          <RiskEditor what="Target" v={leg.target} onChange={(target) => up({ target, reentry_tgt: target.mode === "none" ? noReentry() : leg.reentry_tgt })} />
          <div className="opt">
            <div className="opt-h">Trail stop-loss</div>
            <div className="stack tight">
              <label className="check inline"><input type="checkbox" disabled={leg.sl.mode === "none"} checked={leg.trail.on && leg.sl.mode !== "none"} onChange={(e) => up({ trail: { on: e.target.checked, every: leg.trail.every || 10, by: leg.trail.by || 5 } })} /><span>Move the stop-loss as the price goes my way</span></label>
              {leg.sl.mode === "none" && <span className="hint">Set a stop-loss first.</span>}
              {leg.trail.on && leg.sl.mode !== "none" && (
                <div className="row wrap">
                  <span className="mini">for every</span><Num label="Trail every" value={leg.trail.every} min={0} onChange={(every) => up({ trail: { ...leg.trail, every } })} />
                  <span className="mini">{unitOf(leg.sl.mode)} in my favour, move it</span><Num label="Trail by" value={leg.trail.by} min={0} onChange={(by) => up({ trail: { ...leg.trail, by } })} /><span className="mini">{unitOf(leg.sl.mode)}</span>
                </div>
              )}
            </div>
          </div>
          <ReentryEditor what="stop-loss" v={leg.reentry_sl} enabled={leg.sl.mode !== "none"} onChange={(reentry_sl) => up({ reentry_sl })} />
          <ReentryEditor what="target" v={leg.reentry_tgt} enabled={leg.target.mode !== "none"} onChange={(reentry_tgt) => up({ reentry_tgt })} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- page
export function LegBuilder() {
  useTitle("Strategy builder");
  const s = useSession();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const editId = params.get("id") ? Number(params.get("id")) : null;
  const sess = useSession();
  const connectedAccounts = (sess?.accounts && sess.accounts.length > 0)
    ? sess.accounts
    : (sess?.account ? [sess.account] : []);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [spec, setSpec] = useState<LegSpec | null>(null);
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
        const legsTpl = t.templates.filter((x) => x.engine === "legs");
        setTemplates(legsTpl);
        if (editId != null) {
          const r = await api<{ strategy: StrategyRow }>(`/strategies/${editId}`);
          if (!alive) return;
          if (!("legs" in r.strategy.spec)) { navigate(`/builder?id=${editId}`); return; }
          setSpec(r.strategy.spec as LegSpec);
          setLocked(r.strategy.running);
        } else {
          const want = params.get("template") ?? "leg_straddle";
          const tpl = legsTpl.find((x) => x.id === want) ?? legsTpl[0];
          setSpec(structuredClone(tpl.spec as LegSpec));
          setTplNote(tpl.caution);
        }
      } catch (e) {
        toast((e as Error).message, "err");
      }
    })();
    return () => { alive = false; };
  }, [editId, params]);

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
  const up = (p: Partial<LegSpec>) => setSpec({ ...spec, ...p });
  /** Switching between an index and a commodity swaps the usual times for that market. */
  const pickUnderlying = (u: string) => {
    if (isCommodity(u) === isCommodity(spec.underlying)) return up({ underlying: u });
    const t = MARKET_TIMES[isCommodity(u) ? "commodity" : "index"];
    up({ underlying: u, entry_time: t.entry_time, last_entry_time: t.last_entry_time, exit: { ...spec.exit, time: t.exit } });
  };
  const upOv = (p: Partial<LegSpec["overall"]>) => setSpec({ ...spec, overall: { ...spec.overall, ...p } });
  const upLim = (p: Partial<LegSpec["limits"]>) => setSpec({ ...spec, limits: { ...spec.limits, ...p } });
  const setLeg = (i: number, l: LegCfg) => up({ legs: spec.legs.map((x, j) => (j === i ? l : x)) });
  const ov = spec.overall;

  async function save() {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; errors?: string[] }>(editId != null ? `/strategies/${editId}` : "/strategies", { method: editId != null ? "PUT" : "POST", body: { spec } });
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
    setSpec(structuredClone(t.spec as LegSpec));
    setTplNote(t.caution);
  }

  const timeField = (id: string, label: string, v: string, set: (x: string) => void, optional = false) => (
    <div className="field"><label htmlFor={id}>{label}</label>
      <div className="row">
        <input id={id} className="input" type="time" value={v} onChange={(e) => set(e.target.value)} />
        {optional && v && <button className="btn ghost sm" onClick={() => set("")}>Clear</button>}
      </div>
    </div>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{editId != null ? "Edit strategy" : "New strategy"}</h1>
          <p>Build it leg by leg, like on AlgoTest. The plain-English box shows exactly what will run. Nothing trades until you press Start on the My strategies page.</p>
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
            <div className="panel-h">1. Instrument and timing</div>
            <div className="panel-b form-grid">
              <div className="field span2"><label htmlFor="lname">Name</label><input id="lname" className="input" maxLength={60} value={spec.name} onChange={(e) => up({ name: e.target.value })} /></div>
              <div className="field span2"><span className="lbl">Index</span>
                <div className="seg" role="group" aria-label="Underlying index">
                  {UNDERLYINGS.map((u) => <button key={u} aria-pressed={spec.underlying === u} onClick={() => pickUnderlying(u)}>{UND_LABEL[u]}</button>)}
                </div>
              </div>
              <div className="field span2"><span className="lbl">Commodity (MCX)</span>
                <div className="seg wrap" role="group" aria-label="Underlying commodity">
                  {COMMODITY_UNDERLYINGS.map((u) => <button key={u} aria-pressed={spec.underlying === u} onClick={() => pickUnderlying(u)}>{COMMODITY_LABEL[u]}</button>)}
                </div>
                {isCommodity(spec.underlying) && (
                  <span className="hint">Commodity hours: 09:00 to 23:30 in US summer time, 09:00 to 23:55 in winter (from 2 Nov 2026). Pick your own exit time; the terminal does not close positions at the market close for you. Back-testing is not available for commodities.</span>
                )}
              </div>
              <div className="field"><span className="lbl">Product</span><div className="seg" role="group" aria-label="Product"><button aria-pressed>Intraday (MIS)</button></div></div>
              {connectedAccounts.length > 1 && (
                <div className="field"><label htmlFor="lacct">Account</label>
                  <select id="lacct" className="select" value={spec.account_id || ""} onChange={(e) => up({ account_id: e.target.value })}>
                    <option value="">Default (Active account)</option>
                    {connectedAccounts.map((a) => <option key={a.id} value={a.id}>{a.label} ({a.ucc || a.id})</option>)}
                  </select>
                </div>
              )}
              {timeField("lentry", "Entry time", spec.entry_time, (entry_time) => up({ entry_time }))}
              {timeField("lexit", "Exit time", spec.exit.time, (time) => up({ exit: { ...spec.exit, time } }))}
              {timeField("llast", "Last entry time", spec.last_entry_time, (last_entry_time) => up({ last_entry_time }))}
              {timeField("lnore", "No re-entry after", spec.no_reentry_after, (no_reentry_after) => up({ no_reentry_after }), true)}
              {timeField("lmon", "Stop monitoring after", spec.stop_monitoring_after, (stop_monitoring_after) => up({ stop_monitoring_after }), true)}
              <div className="field"><span className="lbl">When a leg hits its stop-loss or target</span>
                <div className="seg" role="group" aria-label="Square off">
                  <button aria-pressed={spec.square_off === "partial"} onClick={() => up({ square_off: "partial" })}>Square off partial</button>
                  <button aria-pressed={spec.square_off === "complete"} onClick={() => up({ square_off: "complete" })}>Square off complete</button>
                </div>
              </div>
              <div className="field span2">
                <label className="check"><input type="checkbox" checked={spec.trail_be} onChange={(e) => up({ trail_be: e.target.checked })} /><span><b>Trail stop-loss to break-even</b> after a leg stops out: the other legs' stop-loss moves to their entry price.</span></label>
                {spec.trail_be && (
                  <div className="seg" role="group" aria-label="Break-even scope" style={{ marginTop: 6 }}>
                    <button aria-pressed={spec.trail_be_scope === "all"} onClick={() => up({ trail_be_scope: "all" })}>All legs</button>
                    <button aria-pressed={spec.trail_be_scope === "sl_legs"} onClick={() => up({ trail_be_scope: "sl_legs" })}>Only legs with a stop-loss</button>
                  </div>
                )}
              </div>
              <p className="hint span2">Strikes are chosen at the entry time. "No re-entry after" stops further re-entries. "Stop monitoring after" switches off targets, trailing and re-entries from that time; stop-loss orders stay.</p>
            </div>
          </fieldset>

          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">2. Legs <span className="sub">{spec.legs.length} of {MAX_LEGS}</span></div>
            <div className="panel-b stack">
              {spec.legs.map((l, i) => (
                <LegCard key={i} n={i + 1} leg={l} canAdd={spec.legs.length < MAX_LEGS} onChange={(x) => setLeg(i, x)}
                  onRemove={() => up({ legs: spec.legs.filter((_, j) => j !== i) })} onCopy={() => up({ legs: [...spec.legs.slice(0, i + 1), structuredClone(l), ...spec.legs.slice(i + 1)] })} />
              ))}
              <div className="row wrap">
                <button className="btn sm" disabled={spec.legs.length >= MAX_LEGS} onClick={() => up({ legs: [...spec.legs, newLeg()] })}>+ Add a leg</button>
                <button className="btn ghost sm" disabled={spec.legs.length >= MAX_LEGS} onClick={() => up({ legs: [...spec.legs, newLeg({ side: "B", sl: { mode: "none", value: 0 }, strike: strikeOf("atm", { steps: 6 }) })] })}>+ Add a bought hedge</button>
              </div>
              <p className="hint">Bought legs are placed before sold legs, so the hedge is in place first. If a hedge is refused, the sold leg is not placed.</p>
            </div>
          </fieldset>

          <fieldset className="panel" disabled={locked}>
            <div className="panel-h">3. Whole-strategy limits</div>
            <div className="panel-b form-grid">
              <div className="field"><label htmlFor="osl">Overall stop-loss</label>
                <div className="row">
                  <select id="osl" className="select" value={ov.sl.mode} onChange={(e) => upOv({ sl: { mode: e.target.value as typeof ov.sl.mode, value: e.target.value === "none" ? 0 : ov.sl.value || 1000 } })}>
                    <option value="none">None</option><option value="rupees">Rupees (₹)</option><option value="percent">% of premium taken</option>
                  </select>
                  {ov.sl.mode !== "none" && <Num label="Overall stop-loss value" value={ov.sl.value} min={0} onChange={(value) => upOv({ sl: { ...ov.sl, value } })} />}
                </div>
              </div>
              <div className="field"><label htmlFor="otg">Overall target</label>
                <div className="row">
                  <select id="otg" className="select" value={ov.target.mode} onChange={(e) => upOv({ target: { mode: e.target.value as typeof ov.target.mode, value: e.target.value === "none" ? 0 : ov.target.value || 1000 } })}>
                    <option value="none">None</option><option value="rupees">Rupees (₹)</option><option value="percent">% of premium taken</option>
                  </select>
                  {ov.target.mode !== "none" && <Num label="Overall target value" value={ov.target.value} min={0} onChange={(value) => upOv({ target: { ...ov.target, value } })} />}
                </div>
              </div>
              <div className="field span2">
                <label className="check"><input type="checkbox" checked={ov.trail.on} onChange={(e) => upOv({ trail: { on: e.target.checked, reach: ov.trail.reach || 1000, lock: ov.trail.lock || 400, every: ov.trail.every, by: ov.trail.by } })} /><span><b>Overall trailing (lock profit):</b> once total profit reaches a level, lock some of it in and leave if profit falls back to the locked amount.</span></label>
                {ov.trail.on && (
                  <div className="row wrap" style={{ marginTop: 6 }}>
                    <span className="mini">when profit reaches ₹</span><Num label="Profit reach" value={ov.trail.reach} min={0} onChange={(reach) => upOv({ trail: { ...ov.trail, reach } })} />
                    <span className="mini">lock ₹</span><Num label="Profit lock" value={ov.trail.lock} min={0} onChange={(lock) => upOv({ trail: { ...ov.trail, lock } })} />
                    <span className="mini">then for every ₹</span><Num label="Trail every" value={ov.trail.every} min={0} onChange={(every) => upOv({ trail: { ...ov.trail, every } })} />
                    <span className="mini">more, raise the lock by ₹</span><Num label="Trail by" value={ov.trail.by} min={0} onChange={(by) => upOv({ trail: { ...ov.trail, by } })} />
                  </div>
                )}
              </div>
              <div className="field"><label htmlFor="lloss">Max loss for this strategy (₹, 0 = no limit)</label><input id="lloss" className="input num" type="number" min={0} step={100} value={spec.limits.max_loss} onChange={(e) => upLim({ max_loss: num(e.target.value) })} /></div>
              <div className="field"><label htmlFor="lslip">Order price: % beyond the last price</label><input id="lslip" className="input num" type="number" min={0.1} max={10} step={0.1} value={spec.limits.slippage_pct} onChange={(e) => upLim({ slippage_pct: num(e.target.value, 1) })} /></div>
              <label className="check span2">
                <input type="checkbox" checked={spec.exit.exchange_sl} onChange={(e) => setSpec({ ...spec, exit: { ...spec.exit, exchange_sl: e.target.checked } })} />
                <span><b>Also place each stop-loss as an exchange order.</b> It then protects you even if this computer loses power or internet. It is a stop-limit order: if the price jumps far past the limit, the order does not fill, and this terminal closes the position itself while it is running.</span>
              </label>
              <p className="hint span2">Max loss is your own rule: a software check on the whole strategy (0 = none). How many times a leg may enter again is set on the leg (re-entry). A daily loss limit, if you set one in config.yaml, also applies.</p>
            </div>
          </fieldset>
        </div>

        <aside className="builder-side">
          <section className="panel sticky">
            <div className="panel-h">What this will do</div>
            <div className="panel-b stack">
              {check == null && <span className="hint">Checking…</span>}
              {check?.errors.map((e, i) => <div key={i} className="notice bad" role="alert">{e}</div>)}
              {check?.ok && <ol className="plain">{check.summary.map((l, i) => <li key={i}>{l}</li>)}</ol>}
              {check?.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}
              <p className="hint">
                {s?.mode === "live" ? "Mode: LIVE. A running strategy places real orders." : s?.mode === "paper" ? "Mode: paper. Real prices, simulated orders." : "Mode: sample. Invented prices, simulated orders."}
                {" "}Strategies stop if you restart the terminal. Need indicator-based entries? <a href="/builder" onClick={(e) => { e.preventDefault(); navigate("/builder"); }}>Use the rule-block builder</a>.
              </p>
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}
