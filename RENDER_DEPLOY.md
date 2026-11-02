# Deploying Punjab AI Terminal to Render (24/7)

This guide walks you through deploying your terminal live 24/7 on [Render](https://render.com).

---

## 1. Quick Overview

- **Service Type**: Web Service (Docker or Python)
- **Recommended Region**: `Singapore` (nearest cloud region to Indian stock exchanges NSE/BSE/MCX for lowest latency)
- **Recommended Plan**: **Starter** ($7/mo) — Required for true **24/7 uninterrupted trading** (Render's Free tier spins down / sleeps after 15 minutes of inactivity).
- **Persistent Disk**: 1 GB mounted at `/app/data` to preserve trade journals and settings across restarts.

---

## 2. Deploy Using Blueprint (`render.yaml`) - Easiest

1. Push your repository to **GitHub** or **GitLab**.
2. Log in to [dashboard.render.com](https://dashboard.render.com).
3. Click **New +** $\to$ **Blueprint**.
4. Connect your repository. Render will automatically read [`render.yaml`](render.yaml).
5. Fill in the prompted secret environment variables:
   - `DASHBOARD_PASSWORD`: Your secret web dashboard login password (min 8 characters).
   - `NEO_CONSUMER_KEY`: Your Kotak Neo API Consumer Key.
   - `NEO_CONSUMER_SECRET`: Your Kotak Neo API Consumer Secret.
   - `NEO_MOBILE_NUMBER`: Your registered phone number (e.g. `+919876543210`).
   - `NEO_PASSWORD`: Your Kotak login password.
   - `NEO_MPIN`: Your 6-digit MPIN.
   - `ALLOW_LIVE`: `yes`
   - `ALLOW_REMOTE`: `yes`
6. Click **Apply**. Render will automatically build the multi-stage Docker image and launch the terminal.

---

## 3. Manual Docker Deployment

If you prefer setting up the Web Service manually:

1. Click **New +** $\to$ **Web Service**.
2. Connect your Git repository.
3. Choose **Docker** as the Environment.
4. Set:
   - **Name**: `punjab-ai-terminal`
   - **Region**: `Singapore`
   - **Branch**: `main`
   - **Dockerfile Path**: `./Dockerfile`
   - **Health Check Path**: `/api/health`
5. Add Environment Variables under the **Environment** tab:
   | Variable | Value | Description |
   | :--- | :--- | :--- |
   | `HOST` | `0.0.0.0` | Binds to all network interfaces |
   | `PORT` | `10000` | Port Render routes traffic to |
   | `ALLOW_REMOTE` | `yes` | Allows accessing the dashboard from the internet |
   | `ALLOW_LIVE` | `yes` | Enables real trading mode |
   | `DASHBOARD_PASSWORD` | *(your strong password)* | Web login protection (min 8 chars) |
   | `NEO_ENVIRONMENT` | `prod` | Kotak Neo production API |
   | `NEO_CONSUMER_KEY` | *(your key)* | Kotak API key |
   | `NEO_CONSUMER_SECRET`| *(your secret)* | Kotak API secret |
   | `NEO_MOBILE_NUMBER` | `+91...` | Kotak registered mobile |
   | `NEO_PASSWORD` | *(your password)* | Kotak password |
   | `NEO_MPIN` | *(your 6-digit MPIN)* | Kotak MPIN |
   | `TELEGRAM_BOT_TOKEN` | *(optional)* | For order alerts |
   | `TELEGRAM_CHAT_ID` | *(optional)* | Your Telegram chat ID |

6. Under **Disks**, add a persistent disk:
   - **Name**: `terminal-data`
   - **Mount Path**: `/app/data`
   - **Size**: `1 GB`
7. Click **Create Web Service**.

---

## 4. Alternative: Native Python Deployment (Without Docker)

If you prefer Render's native Python runtime:
- **Build Command**: `./build.sh` (or `pip install -r requirements.txt && cd frontend && npm install && npm run build && cd ..`)
- **Start Command**: `./start.sh` (or `python run.py --host 0.0.0.0 --port $PORT`)

---

## 5. Important SEBI & Kotak Neo Static IP Rules

> [!IMPORTANT]
> Under SEBI guidelines for automated/API trading, Kotak Securities validates the outbound IP of the computer placing real orders against the IP registered in your Kotak Developer Portal.
>
> 1. In **Paper Trading** or **Sample Mode**, orders are simulated or data is streamed without IP restrictions.
> 2. In **Live Mode**, check the terminal's **Settings $\to$ Static IP** page once deployed on Render to view Render's outbound IP seen by Kotak (`session.my_ip`), and ensure that address is added to your Kotak Developer portal application.
> 3. If using an outbound proxy (such as QuotaGuard Static on cloud servers), configure your outbound proxy IP.

---

## 6. Keeping the Terminal 24/7 on Free Tier

If you are testing on Render's **Free Tier**:
- Render's free tier spins down after 15 minutes of inactivity.
- To keep it awake 24/7 during market hours for free, set up a free uptime monitor (e.g. [UptimeRobot](https://uptimerobot.com) or [Cron-job.org](https://cron-job.org)) to ping:
  `https://your-service.onrender.com/api/health` every **5 to 10 minutes**.
- For production trading with capital, Render's **Starter ($7/mo)** plan is strongly advised because it provides dedicated CPU, persistent disk, and zero spin-downs.
