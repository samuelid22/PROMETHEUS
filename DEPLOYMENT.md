# Prometheus Hackathon Deployment

## Architecture

- Vercel Hobby hosts the Vite static bundle.
- Render Free hosts the Dockerized FastAPI API, including FFmpeg and ffprobe.
- Gemini and Nimiq RPC calls run only from the backend.

## Render

Create a **Blueprint** from this repository's `render.yaml`. Keep the plan set to **Free**. Do not add a payment method or upgrade a plan.

Set these service environment variables in the Render dashboard; do not add them to Git:

- `GEMINI_API_KEY`
- `PROMETHEUS_NIMIQ_RECIPIENT`
- `PROMETHEUS_NIMIQ_RPC_URL`
- `PROMETHEUS_CORS_ORIGINS` — the final HTTPS Vercel production origin, with no trailing slash

The blueprint already pins the deployment to Testnet and 10 NIM (`1000000` Luna), uses `/tmp` for the Free service's ephemeral processing files, and checks `/api/health`.

## Vercel

Import the same repository as a Vite project. Keep the detected build command and output directory from `vercel.json`.

Set this production environment variable before deployment:

- `VITE_API_BASE_URL` — the public HTTPS Render API origin, with no trailing slash

This is intentionally public browser configuration. Do not set backend keys or Nimiq payment secrets in Vercel.

After Vercel gives the production URL, set the same exact HTTPS origin in Render's `PROMETHEUS_CORS_ORIGINS` and redeploy the backend once.

## Free-tier behavior

Render Free provides 512 MB RAM, can spin down after 15 minutes without inbound traffic, and has no persistent disk. The current single-worker job manager, uploaded files, rendered frames, SQLite payment state, and completed results therefore last only while that backend instance stays alive. Active job polling keeps a running analysis awake, but a restart or idle spin-down cannot recover an old job. This is the retained hackathon-demo behavior; no paid persistence is configured.

Use short demo clips. The API enforces the existing 200 MB upload limit, but FFmpeg processing and the free instance's ephemeral disk make smaller videos more reliable.

## Public verification

1. Check `GET /api/health` on Render.
2. Open the Vercel HTTPS URL in a normal browser and run basic analysis with a short MP4.
3. In Nimiq Pay Testnet, open the Vercel URL using `nimiqpay://miniapp?url=<your-domain>` or `https://nimpay.app/miniapps/open/<your-domain>`.
4. Confirm one 10 NIM payment unlocks only its corresponding job; begin a new analysis and confirm it requires a new payment.
