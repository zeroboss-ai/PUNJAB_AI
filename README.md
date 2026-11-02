# PUNJAB AI

A personal trading terminal for your Kotak Neo account. It runs on **your own computer or server**, with **your own API keys**. Nothing is sent to anyone else.

**This release (5):** Dashboard · Watchlist · Manual buy/sell · Orders & trades · Positions with live P&L · Opening-range algo · Stop-all button · Option chain · OI analysis · Strategy builder (no code) · My strategies · Trade journal · Market data (FII/DII, delivery report, F&O bhavcopy) · Telegram alerts · **Back-test (replay your strategies over days the terminal recorded)**.
Ideas for later: more indicators in the builder, strategy re-adoption after a restart, importing Kotak's own history for still-listed contracts.

## Try it first (no login, no broker)

```
pip install -r requirements.txt
python run.py --mode sample
```
Open the address it prints. The password for that run is printed too. Sample mode uses **invented prices and simulated orders**, so you can click through every screen. On the Algo page, **Run a demo day** replays an invented day through the real algo logic.

## Modes

| Mode | Prices | Orders | Needs login |
|---|---|---|---|
| `sample` | invented | simulated | no |
| `paper` | real (Kotak) | simulated | yes |
| `live` | real (Kotak) | **real** | yes, plus `ALLOW_LIVE=yes` in `.env` |

Change it in `config.yaml` (`mode:`) or with `python run.py --mode paper`. A coloured banner on every screen shows the mode. Live mode refuses to start if the terminal is exposed beyond localhost.

**Switching between paper and real while the terminal runs: Settings → Trading mode.** Start in any mode (including the `sample` demo), then use the **Paper trade / Real trade** buttons. From the demo, **Paper trade** turns off the invented prices and the terminal waits for your Kotak login (add your account in the box below, press Connect, type the 6-digit code). From paper or real you are switched at once with no new login. The whole terminal changes: orders, positions, the journal, the Algo, strategies, the coloured banner and the Telegram tag all follow the chosen mode, and no new Kotak login is needed. The safety rules:
- Real needs `ALLOW_LIVE=yes` in `.env` (the same second lock as `--mode live`), the terminal open on this computer only, and you typing **REAL** in the confirmation box. The static IP is checked at the switch; if it does not match, nothing changes.
- You cannot switch while a strategy or the Algo is running (stop them first), and you cannot leave Real while there are open real positions or open orders at Kotak (close them first; if Kotak cannot be read, the switch is refused too). Otherwise real positions would vanish from the screen.
- Paper and real keep separate positions and journals. Paper positions are hidden while you are in Real and come back when you switch back.
- The choice lasts for this run only. After a restart the terminal starts in the mode from `config.yaml` / `--mode`, so real trading is never switched on by itself.
- Leaving the demo clears everything it invented: prices, option chains, sample watchlist contracts and recent charts. Invented candles and OI points are saved under a separate sample name (so the demo pages still work) and never show up as real history; older data from earlier versions is moved aside the same way if no broker was ever connected here. The way back to the demo is a restart with `--mode sample`.

## Setup for paper / live

1. **Python 3.10+**, then `pip install -r requirements.txt` (installs Kotak's official SDK `kotakneoapi`, imported as `neo_api_client`).
2. `cp .env.example .env` and fill in `NEO_CONSUMER_KEY`, `NEO_MOBILE_NUMBER`, `NEO_UCC`, `NEO_MPIN` and a long `DASHBOARD_PASSWORD`. Leave `NEO_TOTP_SECRET` empty; you type the 6-digit code each morning.
3. In the Kotak Neo app/web, open **Trade API → API Dashboard**: create your token, **register your static IP**, and register TOTP.
4. Tell the terminal which IP you registered: **Settings → Static IP** (see "Static IP" below). The old `static_ip:` line in `config.yaml` still works as a fallback. In live mode the terminal asks Kotak which IP it sees and refuses to connect if it differs.
5. `python tools/discover.py` once while the market is open. It is read-only (no orders) and writes `discover_output.txt`: what Kotak really returns for index names, scrip master, positions, order and trade books, funds, option chain and history. Review it before sharing; never share `.env`.
6. `python run.py --mode paper`, open the address, log in with `DASHBOARD_PASSWORD`, then enter the 6-digit code on the **Connect to Kotak** screen.

To reach it from another computer, use an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 you@your-server`, then browse to `http://127.0.0.1:8000`.

### Rebuilding the screens (only if you change the front end)
The built screens are included (`terminal/web/dist`). To change them: `cd frontend && npm install && npm run build`.

## The screens

- **Dashboard** — index cards (Nifty, Bank Nifty, Sensex, Fin Service, Midcap Select, India VIX) with day range and a small price trail; day P&L, funds, algo status, open positions, latest orders. The top bar keeps the main indices in view on every page.
- **Watchlist** — several lists; search stocks, indices, futures and options (`RELIANCE`, `NIFTY 24500 CE`, `BANKNIFTY FUT`); live price, change, high, low, volume, OI; hover a row for **B / S**.
- **Order ticket** — lots for futures/options (the server applies the real lot size), quantity for shares; Intraday / Carry / Delivery; Limit, Market, SL, SL-M; Day or IOC. Modify open orders from the Orders page.
- **Orders** and **Trades** — today's order book and fills; cancel or modify open orders.
- **Positions** — live P&L, buy/sell more, close one position or **Close all** (you see every closing order before it is sent).
- **Stop all** (top right, every page) — cancels open orders, stops the algo, closes every position with marketable limit orders.
- **Option chain** — Nifty, Bank Nifty, Sensex, Fin Service, Midcap Select; pick the expiry and how many strikes to show. Live call and put prices, open interest (OI) bars, OI change since yesterday, volume, in-the-money shading, ATM / max-pain / largest-OI markers, and B / S buttons on every price. Summary: PCR, max pain, largest call and put OI, ATM straddle.
- **Chart** — candles with volume for any stock, index, future, option or commodity (1m, 5m, 15m, 1h, 1D), a crosshair readout, and two drawing tools: horizontal lines and trend lines (click, wait a moment, click again). Open it from the menu or from the chart button on a watchlist row, a position or an option-chain price. See *Charts* below.
- **OI analysis** — call vs put OI by strike, change in OI since yesterday (with a "show as table" view of each chart), PCR and total OI through the day, and the biggest OI additions with a build-up reading. The through-the-day lines are recorded while the terminal is running and a chain or OI page is open (about one point a minute); they are not back-filled.
- **Algo** — your opening-range strategy (below): start, pause new entries, stop and exit, ranges, trades, log.
- **My strategies** — your own algos, built leg by leg or from rule blocks (below). Start, pause entries, stop and exit, see each condition live (true/false and why), positions, log. Edit, duplicate, delete.
- **Leg builder** — AlgoTest-style: legs with strike rules, entry triggers, stop-loss, target, trailing and re-entries, plus whole-strategy limits (below). Straddles, strangles, delta and premium strategies, hedged condors, futures legs.
- **Rule-block builder** — no code, for indicator-style entries. Four sections: what to trade, entry rules, exits, limits. A plain-English box always shows exactly what will run, and the server re-checks the rules as you type.
- **Market data** — free end-of-day data from NSE's public files: **FII / DII** net buying and selling by day (with a chart); **Delivery and movers** (top gainers, losers, most traded, highest delivery share, and a stock finder); **F&O positioning** (open interest by strike and its change for Nifty, Bank Nifty, Fin Nifty and Midcap Nifty, put-call ratio, max pain, index futures, and long/short build-up in stock futures). Pick any day in the last 90 days.
- **Back-test** — replay one of your saved strategies over days this terminal has recorded (below).
- **Trade journal** — every trade placed here, paired into round trips, with win rate, profit factor, average win/loss, per-trade expectancy, worst drawdown, losing streak; running-total and per-day charts (each with "show as table"); breakdowns by strategy, instrument and entry hour; a note and tags on any trade.

## Build your own algo (strategy builder)

Start from a template (**Opening-range breakout**, **Short straddle**, or blank), change what you like, save, then press **Start** on *My strategies*.

- **What to trade:** index, expiry, lots, product (MIS/NRML), the time strikes are chosen (default 09:15), and the strike offset for Call and Put (0 = at the money).
- **Entry rules:** up to 4 rules, each Buy or Sell on the Call or Put when *all* / *any* of up to 6 conditions are true. A condition compares two values: last price, opening-range high/low (first N one-minute candles), a fixed candle by clock time, the previous candle, day high/low/open, previous close, EMA, SMA, RSI, or a number — each with an optional +/− points offset — using *is above / below / at or above / at or below / crosses above / crosses below*. A rule with no conditions enters when the entry window opens.
- **Exits:** stop-loss (none, points, percent, or a price level from the chart fixed at the moment you enter), target (none, points, percent), a closing time (not later than 15:15), optional "close early when…" conditions, and an optional real **exchange stop-loss order**.
- **Limits:** entry window, most entries per day, max loss for the strategy, and how far through the last price orders are placed so they fill. **Entries per day and max loss are your own rules: 0 = no limit**, and the templates start with none. Without an entry limit, a signal that turns false and true again can enter again after an exit; set it to 1 for one trade a day.
- **Selling with no stop-loss and no max loss is allowed**, but the screen shows a loud warning that nothing limits the loss.

How signals behave: a signal fires **once**. After that its conditions must turn false and true again before it can fire again. A signal that is already true when you press Start (or resume) is ignored. A value that is not available yet (for example a candle that has not formed) makes the condition **false** and the screen says why. If the price feed is more than 10 seconds old, entries wait.

Safety behaviour, all tested with a misbehaving broker:
- Unclear broker replies are reconciled by re-reading positions and sweeping open orders, never guessed.
- Exits cancel the exchange stop-loss first (verified), then close **only the quantity the strategy itself opened**, widening the limit price up to 3 attempts. If that still fails the strategy halts with **ACTION REQUIRED** and tells you to close by hand.
- Three refused or unfilled entries in a row halt the strategy.
- Max loss reached → the strategy closes its positions and stops. **Stop all** also stops every running strategy.
- If your positions cannot be read, entries are blocked.

Honest limits of the rule engine:
- **Candles exist only from the moment the terminal starts watching** (nothing is back-filled). Start it before the first candle a rule uses; otherwise that condition stays false and says so. Indicators (EMA/SMA/RSI) use completed candles since 09:15.
- **Strategies do not survive a restart.** If you restart the terminal, anything a strategy left open must be closed by hand (an exchange stop-loss stays at the broker).
- Positions are tracked per instrument. If you also trade the same option by hand, the strategy still only closes its own quantity, but your P&L figures for that strategy are estimates.
- The ORB template reproduces the built-in algo's decisions on six synthetic days (same side, stop level, exit type, within a couple of ticks), but it is **not bit-identical**. The built-in Algo page remains the more tested version of that strategy.

## Build leg-based strategies (leg builder)

*My strategies → + New strategy.* This is the AlgoTest-style builder. Pick a template (**Short straddle**, **Strangle by delta**, **Strangle by premium**, **Iron condor with hedges**, **Momentum entry with re-entry**, or a blank leg) and change anything. The plain-English box on the right always says exactly what will run, and the server re-checks it as you type. The older rule-block builder stays for indicator-based entries; both kinds run side by side.

**Instrument and timing:** Nifty, Bank Nifty, Fin Nifty, Sensex, Midcap Nifty; intraday only; entry time, exit time (not later than 15:15), last entry time; *no re-entry after*; *stop monitoring after*; **square off partial** (only the leg that hit its stop or target closes) or **complete** (everything closes and the strategy ends); **trail stop-loss to break-even** after a leg stops out (all legs, or only legs that have a stop-loss).

**Each leg (up to 8):** Options or Futures; lots; Buy or Sell; Call or Put; nearest, next or monthly expiry (monthly = the last listed expiry in the month of the nearest one); and

- **Strike criteria:** ATM / OTM n / ITM n; closest premium; premium ≥; premium ≤; premium range; closest delta; delta range; % of ATM; straddle width (a % of the ATM straddle price); synthetic future ± n strikes.
- **Entry trigger:** at the entry time; *simple momentum* (the price must first move up or down by n points or %); *range breakout* (the high or low of the option's or the index's range between the entry time and a time you set); or *conditions* (the same condition blocks as the rule builder, looking at the index or at this leg's own option).
- **Stop-loss** and **target** in points or percent; **trailing** (for every x in your favour, move the stop by y); **re-entry** after stop-loss or after target: *Re ASAP* (at once), *Re Cost* (when the price comes back to the entry price), *Re Momentum* (after a further move), each optionally *reversed* (take the other side), up to n times.

**Whole strategy:** overall stop-loss and target (rupees or % of the premium taken), overall profit lock and trailing (when profit reaches X, lock Y; for every Z more, raise it by W), a software max loss (optional, 0 = none), and the exchange stop-loss option. There is no entries-per-day limit: a leg's re-entry count is the rule.

Behaviour you should know:
- **Bought legs go first.** Hedges are placed before sold legs. If a hedge is refused, the sold leg is not placed.
- Strikes are chosen at the entry time. If the prices needed to choose are not there after about two minutes, the strategy halts having traded nothing, and says why.
- **Delta is not sent by Kotak.** The terminal estimates it from the option's price (Black-Scholes, no interest or dividend), so it can differ from the delta on other platforms.
- **The exchange stop-loss is a stop-limit order**, not a market order. If the price gaps far past the limit, the order does not fill. While the terminal is running it notices that and closes the position itself; if the terminal is off at that moment, nothing does. (The older rule-block builder and the built-in Algo do not have this fallback.)
- When a stop-loss is moved by trailing, the old exchange order is cancelled (verified) and a new one placed; if the new one cannot be placed the position is closed.
- If you start a strategy after its entry time, legs that would have entered more than 5 minutes ago are **skipped** rather than entered late.
- Two legs that resolve to the same option are refused before anything is traded.
- A momentum or range entry may never trigger; then that leg simply does not trade.
- Strategies do not survive a restart of the terminal (same as before). An exchange stop-loss stays at the broker.
- Not built (yet): BTST and positional, overall re-entry, "round strikes", choosing between index and futures as the price source.

Back-testing leg strategies works on recorded days, with the same limits as before. **Futures legs cannot be back-tested** (only the index and its options are recorded), and a strike rule can only choose among strikes that were recorded that day; on a day when the index moved far from where recording started, a far-out strike may be missing and the day is reported as "needs a look".

## Option chain for any F&O stock

On **Option chain** and **OI analysis**, the box next to the index buttons finds any underlying that has options: type `REL` and pick RELIANCE (it also matches company names, like "mahindra" for M&M). The chain opens with that stock's own price, lot size, monthly expiries, PCR, max pain and OI, and every price is tradable from the chain like an index option. The stocks you opened last stay as buttons next to the indices (the last six), and your choice is remembered after a reload.

- In **paper/live** mode the list comes from Kotak's F&O scrip master (every underlying that has call/put contracts), so it shows what Kotak lists. A name that has no options is refused with a message instead of being sent to Kotak.
- In **sample** mode ten invented stocks have options (RELIANCE, HDFCBANK, TCS, INFY, ICICIBANK, SBIN, ITC, AXISBANK, TATASTEEL, MARUTI) so you can try it with fake prices.
- **Every strike:** the strike buttons are **±6, ±10, ±15 and All**. All shows the whole chain in a box that scrolls with the header fixed; the chain opens centred on the at-the-money strike, and **Jump to ATM** returns there. The line beside the buttons says how many strikes the chain has. The terminal always asks Kotak for the whole chain (200 strikes, then 100, then 40 if Kotak refuses the larger size), so PCR, max pain and the biggest-OI strikes are computed from every strike you can see, whichever button is pressed. Live prices stream for the 60 strikes nearest the money; strikes further out show Kotak's snapshot, refreshed every few seconds. Not verified against the real Kotak: how many strikes it returns for a given count.
- Not verified against the real Kotak: the stock chain uses the same Kotak option-chain call as the indices, with the stock's name as the underlying. If Kotak answers differently for a stock, the page shows Kotak's own error text.

## Commodities (MCX): Gold, Silver, Crude Oil, Natural Gas and the rest

Everything Kotak lists in its MCX scrip master (segment `mcx_fo`) can be found, big and mini/micro: type `gold`, `gold mini`, `silver micro`, `crude`, `natural gas`, `goldm 120000 ce` in any search box. Results show friendly names ("Gold Mini 05 Dec FUT"); the exact contract names, lot sizes and tick sizes always come from Kotak's own file, never from this program.

- **Trade by hand:** futures and options go through the same watchlist, order ticket, orders, positions and journal as shares. Commodity orders are **Day validity only** (the Kotak SDK allows nothing else on `mcx_fo`), products are Intraday or Carry (no Delivery), and the ticket uses the contract's own lot and tick size.
- **Option chain and OI:** the quick buttons *Gold, Gold Mini, Silver, Silver Mini, Crude, Crude Mini, Nat Gas* open a chain; the search box finds any other commodity that has options. MCX options are options **on futures**, so the chain's "spot" is a future: the nearest future that expires on or after the option's expiry (an assumption, see below). PCR, max pain, OI and "All strikes" work as for shares.
- **Dashboard:** a *Commodities* row with the front-month future of Gold, Silver, Crude Oil and Natural Gas.
- **Leg-builder strategies:** pick a commodity instead of an index. Candles start at 09:00, strikes are chosen around the future the option is written on, the strike step is read from the chain, and stop-loss prices use the contract's tick. Defaults for a commodity strategy are entry 09:05, last entry 22:45, exit 23:15.
- **Hours:** 09:00 to **23:30 in US summer time and 23:55 in winter**. MCX switches on the Monday after the US clocks change: from **Monday 2 November 2026 non-agri contracts trade until 23:55**; cotton-type agri contracts until 21:00 and the other agri contracts until 17:00. The terminal works the seasonal close out by itself; to force a time, set `mcx_close_time: "23:30"` in `config.yaml`. The top bar has a separate MCX open/closed pill (by the clock only; MCX holidays and special sessions are not known).
- **Closing out is yours:** a strategy's exit time is whatever you set. The terminal refuses an exit time after that day's close and warns when it is within 10 minutes of it, because brokers usually square off intraday positions about 10 minutes before the close (Kotak's own time was not checked). It does not close positions at the market close for you.
- **Not available for commodities:** back-testing (the recorder stores NSE/BSE index prices only; the Back-test page refuses with a clear message), and the rule-block builder and the built-in opening-range algo (they are for indices).

**Not verified against the real Kotak (check these with 1 lot, or paper mode, before trusting them):**
1. **Quantity and money units.** The terminal sends quantity = lots × Kotak's lot size and shows value and profit as price × quantity. On MCX the price unit differs from the lot unit (Gold is quoted per 10 grams, a lot is 1 kg; Silver per kg; Crude per barrel). If Kotak's quantity is in grams/kg rather than in price units, the "Approx. value" in the ticket, the per-order value limit, and paper-mode profit and loss can be off by a large factor. Compare the first order with Kotak's own order value before using real size. The ticket says so on every commodity order.
2. The MCX scrip-master columns and codes (option type for futures is read as "anything that is not CE/PE", strike scaled by 100 and tick by 100 as for NSE, expiry as Unix seconds).
3. Kotak's `option_chain()` and `expiries()` answers for `mcx_fo` (date format, strike count, the lot field).
4. Which future an option is written on (assumed: the nearest future expiring on or after the option's expiry).
5. The 23:55 winter close and the 10-minute square-off come from broker and exchange notices (Zerodha bulletin for 2 Nov 2026), not from Kotak.
6. The sample world has ten invented commodity contracts (Gold, Gold Mini, Gold Petal, Silver, Silver Mini, Silver Micro, Crude Oil, Crude Oil Mini, Natural Gas, Natural Gas Mini) with invented prices; options exist for all but Gold Petal, Silver Micro and Natural Gas Mini.

## Charts

Kotak's API has **no price-history feed**, so the terminal builds the candles itself from the live prices it receives.

- **What gets recorded:** every instrument you open in the chart, and the 30 most recently opened ones, while the terminal is running and connected to prices. One-minute candles are saved to the local database (about every 8 seconds) and kept for 45 days. 5m, 15m, 1h and 1D candles are made from those; they start at the market open (09:15 shares and F&O, 09:00 commodities); a 1D candle is the Indian calendar day.
- **No history before you first looked:** a new instrument starts with an empty chart. The note under the chart ("Recording since …") tells you the first minute recorded. If the terminal was off, the laptop slept or the price feed dropped, there is a gap, not a filled-in line.
- **Sample mode:** the earlier candles are **invented** (a random walk behind the current sample price) so there is something to look at. They are labelled as invented on the page. Live and paper modes never invent anything.
- **Drawings:** horizontal lines and trend lines are saved on this computer for each instrument (up to 60), and come back when you reopen it. Click the bin on a chip under the chart to delete one. Click the second point of a trend line at least about half a second after the first; the chart library treats a faster second click as a double click.
- **Volume** is the change in Kotak's running day volume between ticks. The volume field in Kotak's feed has not been checked against a real feed; if it is missing the bars are empty. Indices have no volume.
- **Not here yet:** indicators (moving averages, RSI and so on) and placing an order by clicking on the chart. These candles are for looking at, not an exact copy of an exchange chart: a candle's high/low can miss a price that happened between two ticks.
- The chart uses TradingView's open-source **lightweight-charts** library (Apache-2.0, bundled in the built screens; its small "TV" mark stays on the chart as the licence asks).

## Phone layout

Below 720 px wide the left menu becomes a bottom bar with four categories: **Trade, Automate, Analyse, Settings**. Tap a category and a one-row list of its pages slides up; tap the same category again to close it (nothing else moves the list: not tapping the page, not Esc, not picking a page). The category that holds the page you are on stays highlighted and shows that page's icon and name. Swipe left or right on the open list to move to the next or previous category. Settings has a single page, so it opens directly.

Badges: Trade shows how many orders are open (refreshed every 10 seconds), Automate shows how many strategies are running (a dot if only the Algo runs), and Settings shows a dot when the Static IP check fails or the broker is not connected.

## Static IP (Settings)

Kotak accepts orders only from the IP address you registered in its API dashboard, and the terminal must go out through that same address. **Settings → Static IP** has two options, and both accept IPv4 and IPv6:

- **Option 1: detect my IP automatically.** The terminal finds the public IPv4 and IPv6 address of this computer's internet connection and shows them with a Copy button. You register them in Kotak (Trade API → API Dashboard), tick what you registered, and press **I registered it in Kotak**. The terminal records the address *it* detected (not one sent by the browser).
- **Option 2: I have my own static IP.** Type the IP you bought (IPv4, IPv6 or both, comma-separated) and register the same one in Kotak. Private addresses such as 192.168.x.x are refused with an explanation.

How it is used:
- **At live connect** Kotak reports the address it sees. If it is not one of your registered addresses, the terminal logs out again and does not start (IPv6 spellings and `::ffff:` forms are compared correctly). The Algo start does the same check. Paper and sample mode only show the status.
- **Order of priority:** the static IP saved on the connected account (Accounts form; may hold an IPv4 and an IPv6), then Settings → Static IP, then `static_ip` in `config.yaml`.
- **Every 5 minutes** (paper/live) the terminal looks up the address again. In live mode, if it no longer matches, a red banner appears on every page and a Telegram message "ACTION REQUIRED" is sent once.
- **No static IP set up in live mode:** connecting is allowed, but a red banner says Kotak will reject orders.

Honest limits:
- Nothing here has been run against the real Kotak; whether Kotak sees you over IPv4 or IPv6 is only known after the first login (Settings then shows "Kotak sees this computer as ..."). Register what it shows.
- Finding the address uses free "what is my IP" web services (ipify, icanhazip, ident.me). If they are blocked or you are offline the status says "Not checked"; nothing is blocked because of that. A VPN changes the address you appear to have.
- Home internet addresses usually change (router restart, provider). Option 1 only works while your provider keeps giving you the same address. A computer's outgoing IPv6 address can also change on its own every day. Kotak may limit how often you can change the registered IP; check their page.
- The SEBI rule is stated as you described it; check Kotak's current instructions for what they require.

## Kotak accounts (Settings)

*Settings → Kotak Neo accounts.* Add your Kotak Neo account inside the terminal instead of (or as well as) the `.env` file: account name, **API token (consumer key)**, **client ID (UCC)**, **mobile number**, **MPIN**, and optionally the **TOTP secret** and the account's **registered static IP**. You can save up to 10 accounts, make one the default, edit or delete them, and **connect one at a time** (paper and live modes). In sample mode you can save accounts but there is nothing to connect to.

- **Connect:** press *Connect* on an account (or choose it on the connect screen shown at start-up), type the 6-digit authenticator code (not needed if you saved the TOTP secret). In live mode you must tick a box naming the account, so you cannot trade the wrong one by accident.
- **Switch accounts:** *Disconnect*, then *Connect* another. Disconnecting is refused while a strategy or the Algo is running. **Positions and orders stay as they are at Kotak**; the terminal just stops watching them, and shows the other account's own positions and orders after you connect it.
- **Separate histories:** the trade journal is kept per Kotak client ID, so each account has its own journal. Strategies, watchlists and the recorded candles are shared by all accounts. Trades recorded before accounts existed stay with the account written in `.env`.
- **Your `.env` account still works:** if `NEO_CONSUMER_KEY`, `NEO_MOBILE_NUMBER`, `NEO_UCC` and `NEO_MPIN` are filled in, they appear as a read-only account called ".env file".
- **Different static IP per account:** each saved account can have its own static IP; in live mode the terminal refuses to connect if Kotak sees a different address (falls back to `static_ip` in `config.yaml`).
- **Wrong details:** after two failed logins for an account the message warns that Kotak can lock the MPIN; after three in a row the terminal waits a minute before asking Kotak again.

**How the secrets are stored (honest version).** In one file, `data/accounts.enc` (permissions 600), encrypted with a key made from your `DASHBOARD_PASSWORD`. Secrets are never sent back to the browser: the screens only show masked values (client ID and mobile partly hidden, so a screen recording does not expose them) and "saved" flags. This protects the file if someone copies or backs up the `data` folder without your `.env`. It does **not** protect you from someone who has the data folder **and** your `.env`, or who can run programs on this computer while the terminal runs. If you change `DASHBOARD_PASSWORD`, the saved accounts cannot be opened any more (the screen says so, nothing is deleted); use the old password again or add them again. Saving the TOTP secret means the terminal can log in without you: only do that on a computer you control. Never record the screen with the "show secret boxes" tick on.

**Not done:** trading two accounts at the same time. Each account would need its own connection, feed and strategy set; for now one account is active per running terminal.

**Not tested against the real thing:** the login itself still uses the same Kotak SDK calls as before. The account screens, storage, switching and error handling are tested with a fake Kotak; connecting a real account from here was not possible.

## Trade journal notes

The journal reads your fills from Kotak (and from the paper/sample broker), keeps them in a local database per mode, and pairs buys and sells first-in-first-out, including across days and position flips. A trade shows the strategy tag when the order was placed by this terminal; orders placed in the Kotak app show as **Other**. All figures are **before brokerage, taxes and charges**. In sample mode you can add and remove invented demo trades to see the screen.

## Market data from NSE (free, end of day)

NSE is not a proper API: files appear in the evening, endpoints change, and NSE blocks some automated access. So:
- Every number is **checked** before it is shown (columns present, numbers parse, buy − sell = net, the file is for the day you asked). If a check fails the screen says "unavailable" and why. It never shows a half-filled table.
- The terminal asks politely (one request at a time, at least a second apart), saves each file on disk (`data/nse/`) and does not ask again, and remembers a "not published yet" answer for ten minutes.
- Without a date it shows the **latest published weekday** (it starts from yesterday before 18:30, and from today after) and tells you which day it is. NSE holidays are not known; it simply walks back to the last file that exists.
- **FII/DII history** is kept from the days this terminal fetched (NSE gives only the latest day). It starts short and grows.
- Delivery and mover lists ignore stocks that traded under ₹10 crore, because tiny volumes mislead.
- Everything here is **description, not advice**. "Long build-up" and similar labels say what happened to price and open interest; they do not say what comes next.
- Sample mode shows **invented** figures in the same file formats so you can try the screens; they are labelled.

**Run `python tools/check_nse.py` once** (no login, no keys). It fetches each file the way the terminal does, tells you OK or FAILED with the reason, and shows the first lines so you can compare. I could not reach NSE from where this was built, so the file names and column names below are from NSE's published formats and a widely used open-source NSE library, **not confirmed against live NSE by me**:
`/api/fiidiiTradeReact` (fields `category`, `date`, `buyValue`, `sellValue`, `netValue`), `sec_bhavdata_full_DDMMYYYY.csv`, `BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip`. If one fails, send me the output of `check_nse.py`.

## Telegram alerts

Alerts tell you about problems without watching the screen. They go to **your own** Telegram bot.

1. In Telegram message **@BotFather**, send `/newbot`, and copy the token. Put it in `.env` as `TELEGRAM_BOT_TOKEN=…`.
2. Open your new bot, press **Start**, send it any message.
3. Run `python tools/telegram_chat_id.py`, and put the number it prints in `.env` as `TELEGRAM_CHAT_ID=…`.
4. Restart the terminal. In **Settings → Telegram alerts** press **Send a test message**.

What is sent (switches under `alerts:` in `config.yaml`): your strategies and the built-in algo (started, signal, in position, closed, finished), warnings, orders you place by hand, a **Stop all** press, a short summary after 15:35, and **errors and ACTION REQUIRED always**. Every message starts with the mode in brackets, for example `[LIVE]`. The built-in algo's demo day in sample mode does not send alerts.

How it is built to stay out of the way: sending happens on a background thread through a small queue, so a slow or broken Telegram can never delay an order. The same message is not repeated within two minutes, and if too many pile up the oldest are dropped. The bot token is never shown on screen or in logs. **Alerts are a convenience, not a safety system:** if your internet or Telegram is down you will not be told, so the exchange stop-loss and the loss limits remain the real protection. Messages contain your symbols and trades; use a bot and chat that only you can read.

## Back-test

**What it does.** Pick a saved strategy and some recorded days, press Run. For each day the terminal feeds the recorded prices, minute by minute, through the **same rule engine that runs your strategy live**, with a private simulated broker, and shows the trades, the running total, win rate, profit factor, drawdown and what the strategy said on each day. It never touches your broker, your positions or your real strategies.

**Where the history comes from (important).** Kotak only serves candles for contracts that are still listed, and weekly options expire every week, so yesterday's option prices cannot be downloaded afterwards. So the terminal **records its own 1-minute candles** for the index and its options (every strike it receives) while it runs in **paper or live mode** between 9:15 and 15:30, into `data/candles.db`. This means:

- Your history **starts the first day you run it and grows each market day the terminal is left running**. After a week there are 5 days; after a month about 20.
- A day recorded only from 10:30 is marked **Partial** and is not selected by default, because the opening range would be wrong.
- A minute with no price tick has no candle. Day coverage is shown on the page.
- Only the index and its options are recorded, not shares.
- In **sample mode** nothing is recorded; the page offers **invented days** (clearly labelled) so you can try it. Results from them mean nothing.

**How to read the results (honest limits).**

- Inside one minute the real order of high and low is unknown. Each minute is replayed as a straight-line path through open, low, high, close (low first for a rising candle, high first for a falling one). A stop-loss and an entry on the same minute may therefore resolve differently from real life.
- Fills use the paper broker: no queue, no partial fills, no slippage beyond the marketable-limit buffer. Real fills are usually a little worse.
- Brokerage, taxes and charges are **not** included unless you type a **cost per trade**.
- A few weeks is a small sample. A strategy that "worked" on 15 days may have been lucky, and tuning a strategy until the past looks good fits noise. Use this to catch strategies that clearly do not work, and to see how they behave, not to prove one will make money.
- It is not advice. Check the SEBI rules before you sell or share strategies.

To run it: **Back-test** in the menu → choose the strategy → tick days (Latest 5 / Latest 10 / All usable) → Run. You can Stop a run; the days finished so far are kept. One run at a time, at most 60 days.

## Risk controls (server side — the browser cannot bypass them)

- Max loss per day (`risk.max_daily_loss`, **default 0 = no limit**, set your own amount in `config.yaml`): when you set one and it is reached, **new positions are blocked**; closing orders always work. If positions cannot be read while a limit is set, new exposure is blocked rather than guessed. With no limit, only your own strategy rules, stop-losses and the per-order limits protect you.
- Max lots per order and max value per order (`manual:` in `config.yaml`).
- The lot size, trading symbol and tick size come from Kotak's instrument list, never from the browser. Indices cannot be traded.
- Double-click protection: a repeated order with the same ticket id returns the first result instead of placing a second order.
- Unclear broker replies are never guessed: the terminal re-reads your positions and says so if it cannot.
- Login: one password, HttpOnly cookie, 5 wrong tries lock the IP for 5 minutes, every state-changing call needs a custom header, the live-price socket checks the page origin. Kotak keys exist only in `.env`; the browser never sees them.

## The opening-range algo

| Step | Rule |
|---|---|
| Range | High / Low of the first **4 one-minute candles (09:15–09:18)** of the index, the ATM Call and the ATM Put. ATM = nearest strike to the index at the 09:15 open, fixed for the day. |
| Sell Put | Index **breaks the range high** AND ATM Put **breaks its range low** → sell the ATM Put. **SL = high of the Put's 09:16 candle.** |
| Sell Call | Index **breaks the range low** AND ATM Call **breaks its range low** → sell the ATM Call. **SL = high of the Call's 09:16 candle.** |
| Exit | No target. Stop-loss, or **15:10**, whichever is first. |
| Risk | Daily loss limit if you set one (0 = none); size = lots whose SL risk fits the remaining budget (default max 1 lot). |

Defaults I chose where your description was silent (all in `config.yaml`): 1 trade per day, no entries after 15:00, sell limit ₹1 below LTP, nearest weekly expiry, product MIS, plus a real exchange stop-loss order next to the software stop-loss.

> The stop-loss is the high of the **09:16 candle only**, not of the 4-candle range. On a 0DTE option that can be tight and get hit often. Paper-trade a few weeks and check the stop-out rate before risking money.

The algo and the screens share **one** Kotak price connection, so watching a watchlist never competes with the algo for data.

## What was tested here, and what was NOT

| | Status |
|---|---|
| Strategy logic, candles, SL, 15:10 exit, sizing, paper fills | ✅ automated tests on synthetic days |
| Order safety on unclear Kotak replies | ✅ tests that make the paper broker misbehave |
| Manual trading, limits, square-off, stop-all, search, watchlists, login security, live price socket | ✅ API tests against the sample world |
| Option chain / OI: PCR, max pain, build-up labels, and parsing of Kotak's `option_chain()` / `expiries()` replies exactly as the SDK docs show them (plus error, rate-limit and empty replies) | ✅ automated tests. The **real** reply is checked by `tools/discover.py`. |
| Rule engine (conditions, armed/fire-once, stops, targets, exchange SL, limits, stale feed, restarts, max-loss, stop-all) and the broker-misbehaviour cases above | ✅ 37 automated tests on a simulated market |
| ORB built from blocks vs the built-in algo | ✅ 6 scenario cross-checks (see limits above) |
| Journal: FIFO pairing, flips, multi-day, stats, notes, tags | ✅ 10 automated tests |
| NSE data: parsers (FII/DII, delivery, F&O), refusal of unknown or inconsistent data, polite client (cookies, spacing, caching, block pages, bad zips, network errors), day-walking | ✅ 30 automated tests with fake NSE replies. **Real NSE not reached** (see above). |
| Telegram: delivery, filters, de-duplication, token never leaks, bad token / bad chat / rate limit, never blocks | ✅ 10 automated tests with a fake Telegram, plus strategy and API hook tests. **A real message was not sent from here** (the build machine cannot reach Telegram); the Settings test button checks it on your computer. |
| Back-test: candle recorder (merge, market hours, only index and options, never raises), replay of a known day (entry at the breakout, stop at the 09:16 high, 15:10 exit, cost per trade, data ending early, missing data, Stop, repeatable results), job manager, API, nothing leaking into live state | ✅ 17 automated tests, on invented and hand-built days. **Never run on real recorded days** (none exist yet). |
| Leg builder (release 6): strike choice (ATM/OTM, premium, delta, % of ATM, straddle width, synthetic) incl. missing prices; leg runner on a simulated market — entry triggers (time, momentum, range, conditions), stop-loss/target in points and percent, trailing, re-entry (ASAP, cost, reverse, momentum), partial/complete square-off, break-even, overall stop/target/profit lock, max loss, stop-monitoring-after, hedges first, refused hedge, futures leg, exchange stop that cannot be placed, price gapping past the stop-limit, late start, pause/stop/kill, stale feed; API; replay in the back-test | ✅ 81 + 4 + 4 automated tests. **Never run against Kotak's real servers.** The back-test of leg strategies has only seen invented days. |
| Screens | ✅ clicked through in a headless browser (dark, light, phone width): login, dashboard, watchlist, search, ticket, orders, positions, close-all, algo demo day, strategy builder, start dialog, strategy details, journal, market data (three tabs), Telegram settings, back-test (run, results, day details), leg builder (dark, light, phone width), running leg strategy, stop-all |
| Daily-loss / entry-limit / max-loss made optional (0 = no limit): guard maths, config, rule-block and leg specs, warnings, runner keeps watching after an exit | ✅ 9 automated tests |
| Saved Kotak accounts: encrypted file (nothing readable inside, permissions, wrong password leaves it untouched), validation, masking, edit-keeps-secrets, defaults, .env account, API (no secrets in any reply), connect/switch/disconnect with a fake Kotak, live confirmation and per-account static IP, secret scrubbing, login back-off, separate journals | ✅ 41 automated tests. **Never logged in to a real account from here.** |
| Commodities (MCX): names and search, seasonal hours (23:30 / 23:55, agri), Kotak scrip-master rows (fake file), chain calls on `mcx_fo` and the future as spot, Day-only / no-Delivery order rules, tick-size check, strategy times and exit-after-close refusal, back-test refusal, a crude-oil strategy tick by tick, API | ✅ 21 automated tests. **Never run against the real MCX data or Kotak.** |
| Charts: candle building from ticks (only watched instruments, volume from the running total, merging a minute saved twice), timeframe alignment (09:15 / 09:00 / Indian calendar day), restart and pruning, drawing validation, sample-only invented backfill, API (candles, bad timeframe/key, unknown instrument, drawings save/load, CSRF) | ✅ 14 automated tests; the page itself was checked in a headless browser (dark, light, phone). **Never run against real Kotak prices.** |
| Paper / real switch in Settings: locked without ALLOW_LIVE, refused when exposed on a network, REAL typed word, sample runs refused, all mode readers change together, hot switch with a fake Kotak (broker swapped, journals apart), static-IP refusal changes nothing, leaving real refused with open positions / open orders / unreadable broker, blocked by running strategies, API (login, CSRF, errors) | ✅ 14 automated tests (including leaving the sample demo: feed stopped, prices / chains / watchlist contracts / recent charts cleared, sample candles kept apart, login works afterwards); the box and its confirmation window were checked in a headless browser. **Never switched on a real Kotak account.** |
| Whole suite | ✅ 432 tests pass |
| Kotak adapter vs the **official SDK** (method names, fields, message types, order validator) | ✅ checked against the SDK source and docs |
| **Kotak's live servers / your account** | ⚠️ **Not run.** Still unconfirmed until `tools/discover.py` is run: real scrip-master values, exact order-status words, whether the trade report returns a time and your order tag (the journal falls back to the time it first saw a fill and to tag "Other"), how Kotak handles the exchange stop-loss orders the rule engine places, whether F&O `quantity` is accepted in units (assumed), whether market orders are accepted, the index names for Fin Service / Midcap Select / India VIX, and the field names in the trade book and funds replies. If an index card stays blank, fix its name under `dashboard_indices` in `config.yaml`. |

**Go live in this order:** sample → paper for 2–3 weeks → live with 1 lot while watching it. Know how to square off in the Kotak app by hand. Selling options naked needs substantial margin; this terminal does not calculate it for you.

## Sharing with students or friends

Each person runs **their own copy** with **their own Kotak keys and their own static IP**. Do not run one copy for several people's accounts: Kotak/SEBI rules tie API access to the account holder's own registered IP, and a shared server would hold other people's keys. Check the legal side (SEBI's algo-provider rules) before charging for access or sharing strategies as advice.

## Known limits

- Market open/closed is by the clock only; NSE and MCX holidays (and special MCX sessions) are not known.
- Candles for the algo are built from tick arrival time, not exchange timestamps.
- If the terminal crashes mid-algo-trade, the exchange stop-loss stays at the broker but the terminal will not re-adopt the position on restart; it refuses to trade and tells you to handle it manually.
- Paper and sample fills ignore brokerage, taxes and queue priority. Margin is not modelled.
- Option-chain open interest comes from Kotak's chain snapshot (re-read every ~20 s) and is updated from live ticks in between; if Kotak's ticks carry no OI, the OI numbers only move with each snapshot. Implied volatility and greeks are not shown yet.
- Price trails on the cards only cover the time since you opened the page.
- Market data is end of day only; there is no live FII/DII, no stock-wise OI history, and NSE holidays are not known.
- The rule engine builds candles from tick arrival time, only while the terminal is running.
- Leg strategies: the delta is the terminal's own estimate; the exchange stop-loss is a stop-limit order (see the leg builder section); futures legs are not back-testable.
- Charts have no history before an instrument was first opened in the terminal, and have gaps whenever the terminal is off or not receiving prices (see Charts).
- The back-test only covers days the terminal itself recorded in paper or live mode (see Back-test); it cannot go back in time, and it is only as good as the ticks received (a laptop that slept leaves gaps).

## Files

```
run.py                       start here
config.yaml                  every setting
frontend/                    the screens (React + TypeScript source)
terminal/web/server.py       API + live-price socket
terminal/web/dist/           built screens
terminal/services.py         wires a mode together
terminal/market/             shared price hub, instrument search, option chain + OI analysis, sample market
terminal/trading/            manual trading: validation, limits, sim broker, Kotak gateway
terminal/strategy.py, engine.py   the built-in algo
terminal/rules/              rule engine: spec.py (validated strategy JSON), book.py (candles, indicators), runner.py (entries, exits, safety)
                             leg builder: legspec.py (spec v2 + plain English + templates), strikes.py (strike choice, delta estimate), legrunner.py (legs, triggers, stops, re-entries, overall rules), strategies.py (picks the right engine)
terminal/accounts.py         saved Kotak accounts: encrypted store, validation, masking
terminal/journal.py          trade journal: round trips and statistics
terminal/history.py          1-minute candle recorder, candle database, invented days for sample mode
terminal/backtest.py         replay engine and background back-test runner
terminal/nse.py              NSE public data: parsers, polite client, sample provider
terminal/alerts.py           Telegram alerts (queue, de-duplication, token scrubbing)
terminal/brokers/kotak.py    Kotak adapter (verified against the SDK)
tools/discover.py            read-only check against your real Kotak account
tools/check_nse.py           read-only check of NSE's free files (no login)
tools/telegram_chat_id.py    one-time helper to find your Telegram chat id
tests/                       automated tests
```

*Trading in F&O carries substantial risk of loss; SEBI's own study found most individual F&O traders lose money. This software is a tool, not advice, and comes with no guarantee.*
