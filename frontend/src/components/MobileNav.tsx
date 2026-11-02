import { useEffect, useRef, useState, type TouchEvent } from "react";
import { navigate, useSession } from "../lib/app";
import type { Listing, OrderRow } from "../lib/types";
import { usePoll } from "../lib/store";
import { activeEntry, isActive, NAV, type NavGroup } from "./nav";

/** True on phone widths. The bottom bar is only built (and only polls) there. */
export function useIsPhone(): boolean {
  const q = "(max-width: 720px)";
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const f = () => setM(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, []);
  return m;
}

type Badge = { n?: number; dot?: boolean } | null;

function BadgeMark({ b }: { b: Badge }) {
  if (!b || (!b.n && !b.dot)) return null;
  return b.n ? <span className="mbadge" aria-label={`${b.n} need attention`}>{b.n > 9 ? "9+" : b.n}</span> : <span className="mbadge dot" aria-hidden="true" />;
}

const SWIPE_MIN = 48;

/**
 * Bottom bar with the four categories. A category with several pages slides up a one-row list of them;
 * only tapping a category moves the list: tapping the same category again closes it. The category that holds the current page stays highlighted
 * (and shows that page's icon and name) even when the list is closed.
 */
export function MobileNav({ path }: { path: string }) {
  const s = useSession();
  const [open, setOpen] = useState<NavGroup["id"] | null>(null);
  const [shown, setShown] = useState<NavGroup["id"]>("trade");          // which list the sheet holds (kept while it slides away)
  const orders = usePoll<Listing<OrderRow>>("/orders", 10000);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const here = activeEntry(path);

  // The list moves only when a category is tapped (no outside tap, no Esc, no auto-close). The page stays usable behind it,
  // and gets extra room at the bottom so nothing is hidden under the list.
  useEffect(() => {
    document.body.toggleAttribute("data-mnav-open", open !== null);
    return () => document.body.removeAttribute("data-mnav-open");
  }, [open]);

  const openOrders = (orders.data?.rows ?? []).filter((r) => r.open).length;
  const strategies = s?.activity?.strategies ?? 0;
  const algo = !!s?.activity?.algo;
  const attention = !!s?.ip_alert || (!!s && s.needs_connect && !s.connected);

  const itemBadge = (path: string): Badge =>
    path === "/orders" ? { n: openOrders } : path === "/strategies" ? { n: strategies } : path === "/algo" ? { dot: algo } : path === "/settings" ? { dot: attention } : null;
  const groupBadge = (g: NavGroup): Badge => {
    if (g.id === "trade") return { n: openOrders };
    if (g.id === "automate") return strategies ? { n: strategies } : { dot: algo };
    if (g.id === "account") return { dot: attention };
    return null;
  };

  function tapCategory(g: NavGroup) {
    if (g.items.length === 1) {                                         // nothing to choose from: go straight there
      setOpen(null);
      navigate(g.home);
      return;
    }
    if (open === g.id) {
      setOpen(null);
    } else {
      setShown(g.id);
      setOpen(g.id);
    }
  }

  const multi = NAV.filter((g) => g.items.length > 1);
  function swipe(dir: 1 | -1) {
    const i = multi.findIndex((g) => g.id === shown);
    const next = multi[i + dir];
    if (next) {
      setShown(next.id);
      setOpen(next.id);
    }
  }
  function onStart(e: TouchEvent) {
    const t = e.touches[0];
    touch.current = { x: t.clientX, y: t.clientY };
  }
  function onEnd(e: TouchEvent) {
    const a = touch.current;
    touch.current = null;
    if (!a) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - a.x;
    const dy = t.clientY - a.y;
    if (Math.abs(dx) >= SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 1.5) swipe(dx < 0 ? 1 : -1);    // swipe left = next category
  }

  const sheet = NAV.find((g) => g.id === shown)!;
  const isOpen = open !== null;

  return (
    <>
      <nav className="mnav" aria-label="Main">
        <div id="msheet" className="msheet" data-open={isOpen} aria-hidden={!isOpen} onTouchStart={onStart} onTouchEnd={onEnd}>
          <div className="msheet-h">{sheet.group}<span className="msheet-hint">swipe to change</span></div>
          <div className="msheet-items" role="list">
            {sheet.items.map((it) => (
              <a key={it.path} role="listitem" href={it.path} className="mitem" aria-current={isActive(it, path) ? "page" : undefined} tabIndex={isOpen ? 0 : -1}
                onClick={(e) => { e.preventDefault(); navigate(it.path); }}>
                <span className="mitem-ico">{it.icon}<BadgeMark b={itemBadge(it.path)} /></span>
                <span className="mitem-lbl">{it.label}</span>
              </a>
            ))}
          </div>
        </div>
        <div className="mnav-cats">
          {NAV.map((g) => {
            const holdsPage = here?.group.id === g.id;
            const single = g.items.length === 1;
            const icon = holdsPage ? here!.item.icon : g.icon;
            const label = holdsPage ? here!.item.label : single ? g.items[0].label : g.group;
            return (
              <button key={g.id} className="mcat" data-here={holdsPage} data-open={open === g.id} aria-expanded={single ? undefined : open === g.id}
                aria-controls={single ? undefined : "msheet"} aria-current={single && holdsPage ? "page" : undefined}
                aria-label={single ? label : `${g.group}${holdsPage ? `, now on ${here!.item.label}` : ""}`} onClick={() => tapCategory(g)}>
                <span className="mcat-ico">{icon}<BadgeMark b={groupBadge(g)} /></span>
                <span className="mcat-lbl">{label}{!single && <i className="mchev" aria-hidden="true" />}</span>
              </button>
            );
          })}
        </div>
      </nav>
    </>
  );
}
