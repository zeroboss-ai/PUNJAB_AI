import { navigate, useSession, useTitle } from "../lib/app";
import { dash, int, pct, px, rupees, shortTime, signed, signedRupees, tone } from "../lib/format";
import { useQuote, useWant } from "../lib/quotes";
import { usePoll } from "../lib/store";
import type { AlgoSnap, Funds, Listing, OrderRow, PositionRow } from "../lib/types";
import { DayRange, Empty, Pnl, Spark } from "../components/ui";

function IndexCard({ k, label, sub }: { k: string; label: string; sub?: string }) {
  const q = useQuote(k);
  const t = tone(q?.c);
  return (
    <section className="panel idx" aria-label={label}>
      <div className="idx-name">
        <span>{label}</span>
        {sub && <span className="idx-sub">{sub}</span>}
      </div>
      <div className="idx-val">{q ? px(q.l) : dash}</div>
      <div className={`idx-chg ${t}`}>
        <span>{signed(q?.c)}</span>
        <span>{pct(q?.p)}</span>
      </div>
      {q && <Spark k={k} up={(q.c ?? 0) >= 0} />}
      {q ? <DayRange low={q.lo} high={q.h} ltp={q.l} /> : <div className="range-lbl"><span>Waiting for price</span></div>}
    </section>
  );
}

const DEFAULT_INDICES = [
  { label: "NIFTY 50", key: "nse_cm|Nifty 50" },
  { label: "NIFTY BANK", key: "nse_cm|Nifty Bank" },
  { label: "SENSEX", key: "bse_cm|SENSEX" },
  { label: "NIFTY FIN SERVICE", key: "nse_cm|Nifty Fin Service" },
  { label: "NIFTY MID SELECT", key: "nse_cm|NIFTY MID SELECT" },
  { label: "INDIA VIX", key: "nse_cm|India VIX" }
];

const DEFAULT_COMMODITIES = [
  { label: "Gold", key: "mcx_fo|104765", name: "GOLD", contract: "Gold FUT" },
  { label: "Silver", key: "mcx_fo|105506", name: "SILVER", contract: "Silver FUT" },
  { label: "Crude Oil", key: "mcx_fo|106247", name: "CRUDEOIL", contract: "Crude Oil FUT" },
  { label: "Natural Gas", key: "mcx_fo|106985", name: "NATURALGAS", contract: "Natural Gas FUT" }
];

export function Dashboard() {
  useTitle("Dashboard");
  const s = useSession();
  const pos = usePoll<Listing<PositionRow>>("/positions", 2000);
  const ord = usePoll<Listing<OrderRow>>("/orders", 3000);
  const funds = usePoll<{ ok: boolean; funds: Funds | null }>("/funds", 10000);
  const algo = usePoll<AlgoSnap>("/algo", 3000);

  const indices = (s?.indices && s.indices.length > 0) ? s.indices : DEFAULT_INDICES;
  const commodities = (s?.commodities && s.commodities.length > 0) ? s.commodities : DEFAULT_COMMODITIES;

  useWant(indices.map((i) => i.key));
  useWant(commodities.map((c) => c.key));
  const open = pos.data?.rows.filter((r) => r.open) ?? [];
  const totals = pos.data?.totals;
  const orders = (ord.data?.rows ?? []).slice(0, 6);
  const f = funds.data?.funds;
  const a = algo.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>{new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" })}</p>
        </div>
      </div>

      <div className="cards">{indices.map((i) => <IndexCard key={i.key} k={i.key} label={i.label} />)}</div>

      <div className="cards-h">
        <h2>Commodities</h2>
        <span className="hint">MCX front-month futures · {s?.market_mcx?.label ?? "MCX open"}</span>
        <span className="grow" />
        <button className="btn sm" onClick={() => navigate("/watchlist")}>Trade</button>
      </div>
      <div className="cards">{commodities.map((c) => <IndexCard key={c.key} k={c.key} label={c.label} sub={c.contract?.replace(/^.*? (\d{2} \w{3}) FUT$/, "$1 FUT") ?? ""} />)}</div>

      <div className="grid2">
        <section className="panel">
          <div className="panel-h">Today</div>
          <div className="panel-b stats">
            <div className="stat">
              <span className="k">Day P&amp;L</span>
              <span className="v"><Pnl v={totals?.pnl} /></span>
            </div>
            <div className="stat">
              <span className="k">Open positions</span>
              <span className="v">{totals ? totals.open : dash}</span>
            </div>
            <div className="stat">
              <span className="k">{f?.simulated ? "Practice cash" : "Available cash"}</span>
              <span className="v">{rupees(f?.available)}</span>
            </div>
            <div className="stat">
              <span className="k">Daily loss limit</span>
              <span className="v">{s ? (s.limits.max_daily_loss ? rupees(s.limits.max_daily_loss) : "Not set") : dash}</span>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-h">
            <span>Opening-range algo</span>
            <span className="grow" />
            <button className="btn sm" onClick={() => navigate("/algo")}>Open</button>
          </div>
          <div className="panel-b">
            {a ? (
              <div className="stats">
                <div className="stat">
                  <span className="k">Status</span>
                  <span className="v" style={{ fontSize: 16 }}>{a.phase === "IDLE" ? "Not started" : a.finished ? "Finished" : a.phase.toLowerCase().replace(/_/g, " ")}</span>
                </div>
                <div className="stat">
                  <span className="k">Algo P&amp;L today</span>
                  <span className="v"><Pnl v={a.risk?.total ?? a.strategy?.realised ?? 0} /></span>
                </div>
                <div className="stat">
                  <span className="k">Trades</span>
                  <span className="v">{a.strategy ? `${a.strategy.trades_taken}/${a.strategy.max_trades}` : dash}</span>
                </div>
              </div>
            ) : (
              <span className="muted">Loading…</span>
            )}
          </div>
        </section>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-h">
          <span>Open positions</span>
          <span className="grow" />
          <button className="btn sm" onClick={() => navigate("/positions")}>All positions</button>
        </div>
        {open.length === 0 ? (
          <Empty title="No open positions">Place a trade from the Watchlist, or start the algo.</Empty>
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Instrument</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">LTP</th><th className="num">P&amp;L</th></tr>
              </thead>
              <tbody>
                {open.slice(0, 6).map((p) => (
                  <tr key={p.key + p.product}>
                    <td><div className="cell-name"><b>{p.label}</b><span>{p.product}</span></div></td>
                    <td className="num">{int(p.net_qty)}</td>
                    <td className="num">{px(p.avg_price)}</td>
                    <td className="num">{px(p.ltp)}</td>
                    <td className="num"><Pnl v={p.pnl} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-h">
          <span>Latest orders</span>
          <span className="grow" />
          <button className="btn sm" onClick={() => navigate("/orders")}>All orders</button>
        </div>
        {orders.length === 0 ? (
          <Empty title="No orders today" />
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Time</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th>Status</th></tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id}>
                    <td className="muted">{shortTime(o.time)}</td>
                    <td>{o.label}</td>
                    <td><span className={`chip ${o.side === "B" ? "buy" : "sell"}`}>{o.side === "B" ? "Buy" : "Sell"}</span></td>
                    <td className="num">{int(o.qty)}</td>
                    <td className="num">{o.avg_price ? px(o.avg_price) : o.price ? px(o.price) : "Market"}</td>
                    <td><StatusChip s={o.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export function StatusChip({ s }: { s: string }) {
  const map: Record<string, [string, string]> = {
    COMPLETE: ["up", "Filled"],
    OPEN: ["warn", "Open"],
    TRIGGER_PENDING: ["warn", "Waiting for trigger"],
    REJECTED: ["down", "Rejected"],
    CANCELLED: ["", "Cancelled"],
    UNKNOWN: ["warn", "Unknown"],
  };
  const [cls, text] = map[s] ?? ["", s];
  return <span className={`chip ${cls}`}>{text}</span>;
}

export { signedRupees };
