import { Shell } from "./components/Shell";
import { TicketHost } from "./components/Ticket";
import { Toasts } from "./components/Toasts";
import { useAuth, usePath, useSession } from "./lib/app";
import { Algo } from "./pages/Algo";
import { Backtest } from "./pages/Backtest";
import { Builder } from "./pages/Builder";
import { LegBuilder } from "./pages/LegBuilder";
import { Chain } from "./pages/Chain";
import { ChartPage } from "./pages/Chart";
import { OIAnalysis } from "./pages/OI";
import { Dashboard } from "./pages/Dashboard";
import { Journal } from "./pages/Journal";
import { MarketData } from "./pages/Market";
import { ConnectGate, LoginPage } from "./pages/Gate";
import { Orders } from "./pages/Orders";
import { Positions } from "./pages/Positions";
import { Settings } from "./pages/Settings";
import { Strategies } from "./pages/Strategies";
import { Watchlist } from "./pages/Watchlist";

const ROUTES: Record<string, () => JSX.Element> = {
  "/": Dashboard,
  "/watchlist": Watchlist,
  "/orders": Orders,
  "/positions": Positions,
  "/chart": ChartPage,
  "/chain": Chain,
  "/oi": OIAnalysis,
  "/algo": Algo,
  "/strategies": Strategies,
  "/builder": Builder,
  "/leg-builder": LegBuilder,
  "/backtest": Backtest,
  "/journal": Journal,
  "/market": MarketData,
  "/settings": Settings,
};

function NotFound() {
  return (
    <div className="empty">
      <b>Page not found</b>
      Use the menu on the left.
    </div>
  );
}

export default function App() {
  const auth = useAuth();
  const path = usePath().replace(/\/+$/, "") || "/";
  const sess = useSession();

  if (auth.state === "loading") return null;
  if (auth.state === "out") return (<><LoginPage /><Toasts /></>);

  const Page = ROUTES[path] ?? NotFound;
  const gated = !!sess && sess.needs_connect && !sess.connected && path !== "/settings";
  return (
    <>
      <Shell path={path}>{gated ? <ConnectGate /> : <Page />}</Shell>
      <TicketHost />
      <Toasts />
    </>
  );
}
