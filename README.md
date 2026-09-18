# Prometheus

Prometheus reverse-engineers video structure locally and can add paid Gemini vision analysis through a Nimiq Pay Mini App.

**Network:** Prometheus is currently configured for Nimiq Testnet. All NIM payments in this deployment are Testnet transactions.

**Testing note:** Prometheus currently uses Nimiq Testnet for all NIM payments. Please test payment functionality using the Testnet environment. No real NIM is required.

## Tiers

- Free: FFmpeg metadata, scene detection, and one representative frame per scene.
- Advanced: Gemini scene/global analysis, reconstructed prompts, and remix controls after a confirmed 10 NIM payment.

## Architecture

Prometheus remains the existing Vite frontend and FastAPI backend. The frontend uses the official `@nimiq/mini-app-sdk` `init()` helper to access the provider injected by Nimiq Pay. Nimiq Pay retains custody of wallet keys and displays the native transaction confirmation.

The backend independently verifies the returned transaction hash through Nimiq JSON-RPC before unlocking advanced analysis. JSON-RPC is used only for server-side chain verification; it does not replace the official Mini App provider or implement wallet custody.

## Setup

Install Python and frontend dependencies, then build the Mini App bundle:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
npm install
npm run build
```

Set a Gemini API key on the backend. The advanced purchase button stays disabled without a real AI provider key.

```powershell
$env:GEMINI_API_KEY = "..."
```

Run the API:

```powershell
.venv\Scripts\python -m uvicorn prometheus.api.app:app --host 127.0.0.1 --port 8765
```

In a second terminal, start the official local Mini App development flow:

```powershell
npm run dev
```

Open Vite's Network URL, normally `http://<your-LAN-IP>:5173`, through **Mini Apps > Custom URL** inside Nimiq Pay. The Vite server proxies `/api` to FastAPI.

Open Nimiq Pay's app menu and long-press the settings button for 10 seconds. Select **Testnet**, then use **Get free NIM** on the empty home screen or in the Top Up modal. Nimiq Pay currently provides 110,000 testnet NIM per request.

The provider does not expose a documented testnet/mainnet selector to Mini Apps. The backend is therefore restricted to testnet, and the user must select Testnet in Nimiq Pay before approving payment.

## Payment Configuration

Defaults target testnet with the configured merchant address and a price of 1,000,000 Luna (10 NIM):

- `PROMETHEUS_NIMIQ_RECIPIENT`
- `PROMETHEUS_NIMIQ_AMOUNT_LUNA`
- `PROMETHEUS_NIMIQ_RPC_URL`
- `PROMETHEUS_NIMIQ_NETWORK` (`testnet` or `mainnet`)
- `PROMETHEUS_NIMIQ_CONFIRMATIONS`
- `PROMETHEUS_PAYMENT_DB`

Analyzer variables:

- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- `PROMETHEUS_PROVIDER` (optional; inferred from the configured key)
- `PROMETHEUS_MODEL` (optional)
- `PROMETHEUS_API_OUTPUT_DIR` (optional)
- `PROMETHEUS_API_UPLOAD_DIR` (optional)

Payment quotes and consumed transaction hashes are persisted in SQLite. The backend verifies the transaction independently through the configured RPC endpoint; client payment claims do not unlock analysis.

For paid analysis, the video is uploaded and validated before the native wallet request. The staged upload and any verified payment are reused after a WebView reload or interrupted start request, preventing duplicate charges.

The default testnet RPC is community-operated and intended for development. Configure a production-grade RPC service before accepting mainnet payments.

Production Mini Apps must be served over HTTPS. Mainnet is intentionally rejected by this development build.

## Development

Run Vite with an API proxy:

```powershell
npm run dev
```

Run tests:

```powershell
.venv\Scripts\python -m pytest -q
npm run test:web
```

Use `REAL_MINI_APP_TESTING.md` for the physical Nimiq Pay test run and expected results.
