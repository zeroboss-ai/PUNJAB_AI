import type { ReactNode } from "react";
import { IconAlgo, IconBacktest, IconBuilder, IconChain, IconChart, IconDashboard, IconJournal, IconMarket, IconOI, IconOrders, IconPositions, IconSettings, IconWatch } from "./Icons";

export interface NavDef {
  path: string;
  label: string;
  icon: ReactNode;
  soon?: boolean;
}
export interface NavGroup {
  id: "trade" | "automate" | "analyse" | "account";
  group: string;
  /** Page that opens when the category is tapped and has only this one page, or when you jump to it. */
  home: string;
  icon: ReactNode;
  items: NavDef[];
}

export const NAV: NavGroup[] = [
  {
    id: "trade", group: "Trade", home: "/", icon: <IconPositions />,
    items: [
      { path: "/", label: "Dashboard", icon: <IconDashboard /> },
      { path: "/watchlist", label: "Watchlist", icon: <IconWatch /> },
      { path: "/orders", label: "Orders", icon: <IconOrders /> },
      { path: "/positions", label: "Positions", icon: <IconPositions /> },
    ],
  },
  {
    id: "automate", group: "Automate", home: "/algo", icon: <IconAlgo />,
    items: [{ path: "/algo", label: "Algo", icon: <IconAlgo /> }, { path: "/strategies", label: "My strategies", icon: <IconBuilder /> }, { path: "/backtest", label: "Back-test", icon: <IconBacktest /> }],
  },
  {
    id: "analyse", group: "Analyse", home: "/chain", icon: <IconChain />,
    items: [{ path: "/chart", label: "Chart", icon: <IconChart /> }, { path: "/chain", label: "Option chain", icon: <IconChain /> }, { path: "/oi", label: "OI analysis", icon: <IconOI /> }, { path: "/market", label: "Market data", icon: <IconMarket /> }, { path: "/journal", label: "Trade journal", icon: <IconJournal /> }],
  },
  { id: "account", group: "Account", home: "/settings", icon: <IconSettings />, items: [{ path: "/settings", label: "Settings", icon: <IconSettings /> }] },
];

/** Is this menu entry the page being shown? (The strategy builders belong to "My strategies".) */
export function isActive(it: NavDef, path: string): boolean {
  return path === it.path || (it.path === "/strategies" && (path === "/builder" || path === "/leg-builder"));
}

export function activeEntry(path: string): { group: NavGroup; item: NavDef } | null {
  for (const g of NAV) {
    const item = g.items.find((i) => isActive(i, path));
    if (item) return { group: g, item };
  }
  return null;
}
