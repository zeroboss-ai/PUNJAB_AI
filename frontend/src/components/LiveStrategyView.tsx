import { px, signedRupees } from "../lib/format";
import { STATUS_TEXT, type CondState, type RunState } from "../lib/strategy";
import type { LegView } from "../lib/legs";

export const pnlClass = (v: number | undefined) => (v == null || Math.abs(v) < 0.5 ? "flat" : v > 0 ? "up" : "down");

export function Cond({ c }: { c: CondState }) {
  return (
    <li className={c.ok ? "ok" : ""}>
      <span className={`dot ${c.ok ? "ok" : ""}`} aria-label={c.ok ? "true" : "false"} />
      <span>
        {c.text}
        <small className="muted">
          {c.l != null && c.r != null ? ` (${px(c.l)} vs ${px(c.r)})` : ""}
          {c.why.length ? ` · ${c.why[0]}` : ""}
        </small>
      </span>
    </li>
  );
}

export const LEG_STATUS: Record<string, string> = {
  selected: "Strike chosen, waiting for its trigger",
  waiting: "Choosing the strike",
  open: "In position",
  closed: "Finished",
  skipped: "Not traded",
  attention: "Needs attention",
};

export function LegLine({ l }: { l: LegView }) {
  const reSl = l.re_sl[1] ? ` · stop re-entries ${l.re_sl[0]}/${l.re_sl[1]}` : "";
  const reTg = l.re_tgt[1] ? ` · target re-entries ${l.re_tgt[0]}/${l.re_tgt[1]}` : "";
  return (
    <div className="leg-live">
      <div><b>{l.title}</b><br /><small className="muted">{l.strike_rule}</small></div>
      <div>
        {l.label ? <>{l.label}{l.ltp != null ? ` @ ${px(l.ltp)}` : ""}<br /></> : null}
        <small className="muted">{LEG_STATUS[l.status] ?? l.status}{l.progress ? ` · ${l.progress}` : ""}{l.pending ? ` · re-entry (${l.pending.mode}) waiting` : ""}{reSl}{reTg}{l.note ? ` · ${l.note}` : ""}</small>
        {l.pick && <><br /><small className="faint">{l.pick}</small></>}
      </div>
      <span className="chip flat">{l.side === "S" ? "Sell" : "Buy"}</span>
    </div>
  );
}

export function Live({ st }: { st: RunState }) {
  const legsLive = st.kind === "legs" && Array.isArray(st.legs) ? (st.legs as unknown as LegView[]) : null;
  return (
    <div className="live stack">
      {st.halt && <div className="notice bad" role="alert"><b>Action required.</b> {st.halt}</div>}
      <dl className="kv">
        <dt>Status</dt><dd style={{ fontWeight: 600 }}>{STATUS_TEXT[st.status] ?? st.status}{st.paused ? " · new entries paused" : ""}</dd>
        {st.account_label && <><dt>Account</dt><dd><b>{st.account_label}</b></dd></>}
        {st.message && <><dt>Now</dt><dd>{st.message}</dd></>}
        {!legsLive && st.legs && Object.keys(st.legs).length > 0 && <><dt>Strikes</dt><dd>{Object.entries(st.legs).map(([k, l]) => `${k === "CE" ? "Call" : "Put"} ${l.strike} @ ${px(l.ltp)}`).join(" · ")}{st.expiry ? ` · expiry ${st.expiry}` : ""}</dd></>}
        {legsLive && st.overall && <><dt>Whole strategy</dt><dd>{st.overall.reason ? `Ended: ${st.overall.reason}. ` : ""}{st.overall.floor != null ? `Profit locked: ${signedRupees(st.overall.floor)}. ` : ""}{st.overall.monitoring ? "Watching targets, trailing and re-entries." : "Monitoring of targets and re-entries is switched off (stop-loss orders stay)."}</dd></>}
        {st.spot && <><dt>Index</dt><dd>{px(st.spot.ltp)}</dd></>}
        {st.pnl && <><dt>P&amp;L today</dt><dd className={pnlClass(st.pnl.total)} style={{ fontWeight: 600 }}>{signedRupees(st.pnl.total)} <small className="muted">(booked {signedRupees(st.pnl.realised)}, open {signedRupees(st.pnl.unrealised)})</small></dd></>}
        {st.entries_done != null && <><dt>{legsLive ? "Legs entered" : "Entries"}</dt><dd>{st.entries_done}{st.max_entries ? ` of ${st.max_entries}` : " (no limit set)"}</dd></>}
      </dl>
      {legsLive && <div className="legs-live">{legsLive.map((l) => <LegLine key={l.id} l={l} />)}</div>}
      {st.rules?.map((r) => (
        <div key={r.leg}>
          <div className="lbl">{r.side === "S" ? "Sell" : "Buy"} the {r.leg === "CE" ? "Call" : "Put"}: {r.ok ? "signal true now" : "no signal"}{!r.in_window ? " (outside the entry window)" : ""}{r.ok ? "" : r.armed ? " · ready" : " · waiting to re-arm"}</div>
          {r.conds.length ? <ul className="conds">{r.conds.map((c, i) => <Cond key={i} c={c} />)}</ul> : <span className="hint">No conditions: enters when the window opens.</span>}
        </div>
      ))}
      {st.exit_rule && st.exit_rule.conds.length > 0 && (
        <div><div className="lbl">Extra exit rule</div><ul className="conds">{st.exit_rule.conds.map((c, i) => <Cond key={i} c={c} />)}</ul></div>
      )}
      {st.positions && st.positions.length > 0 && (
        <div className="tscroll">
          <table className="t">
            <thead><tr><th>Position</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Now / exit</th><th className="num">P&amp;L</th><th>Status</th></tr></thead>
            <tbody>
              {st.positions.map((p, i) => (
                <tr key={i}>
                  <td>{p.side === "S" ? "Sold" : "Bought"} {p.symbol}</td>
                  <td className="num">{p.qty}</td>
                  <td className="num">{px(p.entry_price)}</td>
                  <td className="num">{px(p.sl)}{p.exchange_sl ? " ⚑" : ""}</td>
                  <td className="num">{px(p.status === "closed" ? p.exit_price : p.ltp)}</td>
                  <td className={`num ${pnlClass(p.pnl)}`}>{signedRupees(p.pnl)}</td>
                  <td>{p.status === "closed" ? p.exit_reason + (p.estimated ? " (price estimated)" : "") : p.status === "attention" ? "Needs attention" : "Open"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {st.positions.some((p) => p.exchange_sl) && <p className="hint" style={{ padding: "6px 2px" }}>⚑ = a stop-loss order is resting at the exchange.</p>}
        </div>
      )}
      {st.log && st.log.length > 0 && (
        <div className="log" role="log" aria-label="Strategy log">
          {[...st.log].reverse().map((e, i) => (
            <div key={i} className={e.level === "error" ? "lv-ERROR" : e.level === "warn" ? "lv-WARN" : ""}>
              <span className="faint">{e.t}</span><span>{e.level}</span><span>{e.msg}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
