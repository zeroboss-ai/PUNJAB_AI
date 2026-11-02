import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { closeTicket, toast, useSession, useTicket, type TicketReq } from "../lib/app";
import { isDerivativeKey, px, rupees, snapToTick, uuid } from "../lib/format";
import { useQuote, useWant } from "../lib/quotes";
import { CloseBtn, Drawer } from "./ui";

type OType = "L" | "MKT" | "SL" | "SL-M";
const OT_LABEL: Record<OType, string> = { L: "Limit", MKT: "Market", SL: "SL", "SL-M": "SL-M" };

export function TicketHost() {
  const req = useTicket();
  if (!req) return null;
  return <Ticket key={req.key + (req.modify?.id ?? "") + req.side} req={req} />;
}

function Ticket({ req }: { req: TicketReq }) {
  const sess = useSession();
  const deriv = isDerivativeKey(req.key);
  const mcx = req.key.startsWith("mcx_fo|");
  // Commodity lot and tick sizes differ per contract (tick 1 for Gold, 0.1 for Natural Gas ...): ask the server, which reads Kotak's file.
  const [meta, setMeta] = useState<{ lot: number; tick: number } | null>(null);
  useEffect(() => {
    if (!mcx) return;
    let alive = true;
    api<{ instrument: { lot: number; tick: number } }>(`/instrument?key=${encodeURIComponent(req.key)}`)
      .then((r) => { if (alive) setMeta(r.instrument); })
      .catch(() => { /* keep what the caller passed */ });
    return () => { alive = false; };
  }, [mcx, req.key]);
  const lotSize = mcx && meta ? meta.lot : req.lot;
  const tickSize = mcx && meta ? meta.tick : req.tick;
  const m = req.modify;
  useWant([req.key]);
  const q = useQuote(req.key);
  const ltp = q?.l;

  const [side, setSide] = useState<"B" | "S">(req.side);
  const [lots, setLots] = useState(m ? Math.max(1, Math.round(m.qty / Math.max(m.lot, 1))) : 1);
  const [qty, setQty] = useState(m ? m.qty : 1);
  const [product, setProduct] = useState(m?.product ?? req.product ?? (deriv ? "MIS" : "MIS"));
  const [otype, setOtype] = useState<OType>((m?.order_type as OType) ?? "L");
  const [price, setPrice] = useState<string>(m && m.price ? String(m.price) : "");
  const [trigger, setTrigger] = useState<string>(m && m.trigger ? String(m.trigger) : "");
  const [validity, setValidity] = useState<"DAY" | "IOC">((m?.validity as "DAY" | "IOC") ?? "DAY");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientId = useRef(uuid());
  const touchedPrice = useRef(!!m);

  // Pre-fill the limit price from the live price once, unless the user has typed one.
  useEffect(() => {
    if (!touchedPrice.current && ltp) {
      setPrice(snapToTick(ltp, tickSize).toFixed(tickSize >= 1 ? 0 : 2));
    }
  }, [ltp, tickSize]);

  const units = deriv ? lots * Math.max(lotSize, 1) : qty;
  const needsPrice = otype === "L" || otype === "SL";
  const needsTrigger = otype === "SL" || otype === "SL-M";
  const refPrice = needsPrice && Number(price) > 0 ? Number(price) : ltp ?? 0;
  const value = units * refPrice;
  const maxLots = sess?.limits.max_lots_per_order;

  const problem = useMemo(() => {
    if (units < 1) return deriv ? "Enter at least 1 lot" : "Enter a quantity";
    if (deriv && maxLots && lots > maxLots) return `Max ${maxLots} lots per order`;
    if (needsPrice && !(Number(price) > 0)) return "Enter a limit price";
    if (needsTrigger && !(Number(trigger) > 0)) return "Enter a trigger price";
    return null;
  }, [units, deriv, lots, maxLots, needsPrice, needsTrigger, price, trigger]);

  async function submit() {
    if (busy || problem) return;
    setBusy(true);
    setError(null);
    try {
      if (m) {
        await api(`/orders/${encodeURIComponent(m.id)}`, {
          method: "PUT",
          body: { order_type: otype, lots: deriv ? lots : 0, qty: deriv ? 0 : qty, price: needsPrice ? Number(price) : 0, trigger: needsTrigger ? Number(trigger) : 0, validity },
        });
        toast(`Order ${m.id} modified`);
      } else {
        const r = await api<{ order_id: string; qty: number }>("/orders", {
          method: "POST",
          body: {
            key: req.key, side, lots: deriv ? lots : 0, qty: deriv ? 0 : qty, product, order_type: otype,
            price: needsPrice ? Number(price) : 0, trigger: needsTrigger ? Number(trigger) : 0, validity, client_id: clientId.current,
          },
        });
        toast(`${side === "B" ? "Buy" : "Sell"} order sent · ${r.qty} × ${req.label}`);
      }
      closeTicket();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") submit();
  };

  return (
    <Drawer side={side} onClose={closeTicket} label={m ? "Modify order" : "Order ticket"}>
      <div onKeyDown={onKey} style={{ display: "contents" }}>
        <div className="drawer-h">
          <div className="grow">
            <div className="ttl">{req.label}</div>
            <div className="hint">
              {m ? `Modify order ${m.id}` : "New order"} · <span style={{ fontWeight: 560, color: "var(--text)" }}>{ltp ? px(ltp) : "waiting for price"}</span>
              {deriv && ` · lot ${lotSize}`}
            </div>
          </div>
          <CloseBtn onClick={closeTicket} />
        </div>
        <div className="drawer-b">
          {!m && (
            <div className="seg side full" role="group" aria-label="Side">
              <button className="b" aria-pressed={side === "B"} onClick={() => setSide("B")}>Buy</button>
              <button className="s" aria-pressed={side === "S"} onClick={() => setSide("S")}>Sell</button>
            </div>
          )}

          <div className="two">
            {deriv ? (
              <div className="field">
                <label htmlFor="t-lots">Lots</label>
                <input id="t-lots" className="input num" type="number" min={1} step={1} value={lots || ""} autoFocus onChange={(e) => setLots(Math.floor(Number(e.target.value)))} />
                <span className="hint">{units} units</span>
              </div>
            ) : (
              <div className="field">
                <label htmlFor="t-qty">Quantity</label>
                <input id="t-qty" className="input num" type="number" min={1} step={1} value={qty || ""} autoFocus onChange={(e) => setQty(Math.floor(Number(e.target.value)))} />
              </div>
            )}
            <div className="field">
              <span className="lbl">Product</span>
              <div className="seg full" role="group" aria-label="Product">
                <button aria-pressed={product === "MIS"} disabled={!!m} onClick={() => setProduct("MIS")} title="Intraday - closed by the broker before the market closes">Intraday</button>
                {deriv ? (
                  <button aria-pressed={product === "NRML"} disabled={!!m} onClick={() => setProduct("NRML")} title="Normal - can be carried overnight">Carry</button>
                ) : (
                  <button aria-pressed={product === "CNC"} disabled={!!m} onClick={() => setProduct("CNC")} title="Delivery">Delivery</button>
                )}
              </div>
            </div>
          </div>

          <div className="field">
            <span className="lbl">Order type</span>
            <div className="seg full" role="group" aria-label="Order type">
              {(Object.keys(OT_LABEL) as OType[]).map((t) => (
                <button key={t} aria-pressed={otype === t} onClick={() => setOtype(t)}>{OT_LABEL[t]}</button>
              ))}
            </div>
          </div>

          <div className="two">
            <div className="field">
              <label htmlFor="t-price">Price</label>
              <input id="t-price" className="input num" inputMode="decimal" disabled={!needsPrice} value={needsPrice ? price : ""} placeholder={needsPrice ? "" : "at market"}
                onChange={(e) => { touchedPrice.current = true; setPrice(e.target.value); }} />
            </div>
            <div className="field">
              <label htmlFor="t-trig">Trigger price</label>
              <input id="t-trig" className="input num" inputMode="decimal" disabled={!needsTrigger} value={needsTrigger ? trigger : ""} onChange={(e) => setTrigger(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <span className="lbl">Validity</span>
            <div className="seg" role="group" aria-label="Validity">
              <button aria-pressed={validity === "DAY"} onClick={() => setValidity("DAY")}>Day</button>
              {!mcx && <button aria-pressed={validity === "IOC"} onClick={() => setValidity("IOC")} title="Fill immediately or cancel">IOC</button>}
            </div>
          </div>

          {mcx && (
            <div className="notice">
              Commodity order: Day validity only, and the quantity is lots × {lotSize} as Kotak lists it. The value shown is price × quantity as this terminal counts it;
              compare it with Kotak's own figure on your first order, because the exchange's price unit (for example per 10 grams) may change the real contract value.
            </div>
          )}
          {otype === "MKT" && deriv && (
            <div className="notice">Market orders on options can fill far from the last price when the market is thin. A limit order near the live price is safer.</div>
          )}
          {!m && side === "S" && deriv && (
            <div className="notice">Selling options has no fixed loss limit. Place a stop-loss order or watch the position closely.</div>
          )}
        </div>
        <div className="drawer-f">
          <div className="est">
            <span>Approx. value</span>
            <b>{value > 0 ? rupees(value) : "–"}</b>
          </div>
          {(error || problem) && <div className="err" role="alert">{error ?? problem}</div>}
          <button className={`btn ${side === "B" ? "buy" : "sell"}`} style={{ height: 38 }} disabled={busy || !!problem} onClick={submit}>
            {busy ? "Sending…" : m ? "Save changes" : side === "B" ? "Buy" : "Sell"}
          </button>
          <span className="hint" style={{ textAlign: "center" }}>
            {sess?.mode === "live" ? "This sends a real order to Kotak." : sess?.mode === "paper" ? "Paper mode: simulated order at real prices." : "Sample mode: simulated order at invented prices."}
          </span>
        </div>
      </div>
    </Drawer>
  );
}
