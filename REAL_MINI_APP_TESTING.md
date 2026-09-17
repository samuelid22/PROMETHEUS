# Real Mini App Test Run

## Start

1. Set `GEMINI_API_KEY` in the backend environment.
2. Start FastAPI for the Vite proxy:

```powershell
.venv\Scripts\python -m uvicorn prometheus.api.app:app --host 127.0.0.1 --port 8765
```

3. In a second terminal, run `npm run dev`. The project script already enables network access with `--host 0.0.0.0`.
4. In Nimiq Pay, open the app menu and long-press settings for 10 seconds. Select Testnet and use **Get free NIM**.
5. Open Vite's Network URL, normally `http://<computer-lan-ip>:5173`, through **Mini Apps > Custom URL**.

The paid button must say `Pay 10 NIM` and the connection status must say `Nimiq Pay connected · testnet required`. Do not send a payment if the status says advanced analysis is unavailable or Nimiq Pay has not been manually switched to Testnet.

## Checklist

1. Open inside Nimiq Pay: page loads without horizontal scrolling or clipped controls.
2. Wallet connection: connected testnet status appears; no account or private-key data is displayed.
3. Video upload: select a small MP4; both valid actions become available.
4. Free analysis: completes without a wallet dialog and displays local metadata, scene cuts, and frames only.
5. Paid analysis: uploads and validates the video first, then requests exactly 10 NIM, and starts Gemini analysis only after verification. An upload error must never open the wallet.
6. Payment confirmation: approve once; the app waits for a block confirmation and does not show a second payment dialog.
7. Payment cancellation: reject the native dialog; the validated upload remains available for retry and no analysis starts.
8. Payment failure: test with insufficient testnet funds; the wallet error is displayed and no analysis starts.
9. AI failure: temporarily use an invalid Gemini key after confirming payment; the job fails, but retrying after restoring the key reuses the payment.
10. Network interruption: disable connectivity while payment verification or job polling is active; reconnect and use the same payment/job rather than paying again.
11. Reload during processing: reload the WebView; the processing screen resumes the saved job and eventually displays its result.
12. Reload after payment: reload immediately after approving the native payment; reselect the video if needed and continue without another wallet transaction. The backend should discover it from the unique quote memo.
13. Mobile viewport: verify at the device's narrowest orientation; buttons, scene cards, prompts, and copy controls remain within the viewport.
14. Long analysis results: use a multi-scene clip; scroll all results, expand scene details, copy prompts, and run remix.
15. Large/invalid upload: files over 200 MB are rejected before upload; renamed non-video files are rejected as invalid media before payment is reserved.

## Automated Checks

```powershell
.venv\Scripts\python -m pytest -q
npm run test:web
npm run build
npm audit
```

The native Nimiq Pay confirmation sheet, app suspension behavior, testnet balance, and live-chain propagation still require the physical-device checks above.
