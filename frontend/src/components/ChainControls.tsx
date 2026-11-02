import { useEffect, type ReactNode } from "react";
import { api } from "../lib/api";
import { expiryLabel, daysTo, openUnderlying, setChainSel, useChainSel, useRecentUnderlyings, type ChainData, type Underlying } from "../lib/chain";
import { usePoll } from "../lib/store";
import { UnderlyingSearch } from "./UnderlyingSearch";

const QUICK_CM: [string, string][] = [["GOLD", "Gold"], ["SILVER", "Silver"], ["CRUDEOIL", "Crude"], ["NATURALGAS", "Nat Gas"]];

export function ChainControls({ data, allowAll = true, extra }: { data: ChainData | null; allowAll?: boolean; extra?: ReactNode }) {
  const sel = useChainSel();
  const { data: u } = usePoll<{ ok: boolean; underlyings: Underlying[] }>("/chain/underlyings", 60000);
  const list = u?.underlyings ?? [];
  const recent = useRecentUnderlyings().filter((r) => !list.some((x) => x.name === r.name));
  const stockOpen = sel.underlying && !list.some((x) => x.name === sel.underlying) && !QUICK_CM.some(([n]) => n === sel.underlying);

  // If the saved underlying does not exist in this mode (e.g. FINNIFTY in the sample world), fall back to the first one.
  // A stock you opened earlier is kept when it is a real F&O name.
  useEffect(() => {
    if (!list.length || list.some((x) => x.name === sel.underlying)) return;
    let alive = true;
    (async () => {
      try {
        const r = await api<{ ok: boolean; results?: Underlying[] }>("/chain/search?q=" + encodeURIComponent(sel.underlying));
        if (alive && !(r.results ?? []).some((x) => x.name === sel.underlying)) setChainSel({ underlying: list[0].name });
      } catch {
        /* cannot check right now: keep the choice */
      }
    })();
    return () => { alive = false; };
  }, [list, sel.underlying]);

  const mine = data && data.underlying === sel.underlying ? data : null;
  return (
    <div className="row chain-controls" style={{ flexWrap: "wrap", gap: 10 }}>
      <div className="seg" role="group" aria-label="Underlying">
        {list.map((x) => (
          <button key={x.name} aria-pressed={sel.underlying === x.name} onClick={() => setChainSel({ underlying: x.name })} title={x.label}>
            {x.name}
          </button>
        ))}
        {stockOpen && !recent.some((r) => r.name === sel.underlying) && (
          <button aria-pressed="true" title={sel.underlying}>{sel.underlying}</button>
        )}
        {recent.map((x) => (
          <button key={x.name} aria-pressed={sel.underlying === x.name} onClick={() => openUnderlying(x)} title={x.label}>{x.name}</button>
        ))}
      </div>
      <div className="seg" role="group" aria-label="Commodity">
        {QUICK_CM.map(([name, label]) => (
          <button key={name} aria-pressed={sel.underlying === name} onClick={() => setChainSel({ underlying: name })} title={`${label} options (MCX)`}>{label}</button>
        ))}
      </div>
      <UnderlyingSearch />
      <select className="select" style={{ width: "auto", minWidth: 150 }} aria-label="Expiry" value={mine?.expiry ?? sel.expiry}
        onChange={(e) => setChainSel({ expiry: e.target.value })} disabled={!mine}>
        {(mine?.expiries ?? []).map((e) => (
          <option key={e} value={e}>{expiryLabel(e)} · {daysTo(e)}d</option>
        ))}
        {!mine && <option>Expiry</option>}
      </select>
      <div className="seg" role="group" aria-label="Strikes shown">
        {([6, 10, 15, ...(allowAll ? [0] : [])] as number[]).map((w) => (
          <button key={w} aria-pressed={sel.window === w} onClick={() => setChainSel({ window: w })}>{w === 0 ? "All" : `±${w}`}</button>
        ))}
      </div>
      {extra}
      {mine && (
        <span className="hint" title="The chain is re-read from the broker every few seconds; prices and open interest update live in between.">
          {mine.source === "sample" ? "Sample chain" : mine.stale ? "Snapshot is old" : `Snapshot ${Math.round(mine.snapshot_age_s)}s old`}
          {` · ${sel.window === 0 || mine.rows.length >= mine.strikes_total ? `all ${mine.strikes_total}` : `${mine.rows.length} of ${mine.strikes_total}`} strikes`}
        </span>
      )}
    </div>
  );
}
