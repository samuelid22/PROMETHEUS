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

Render Free provides 512 MB RAM, can spin down after 15 minutes without inbound traffic, and has no persistent disk. When a visitor opens the Mini App after a spin-down, its initialization polls `/api/health` until Render is available, then enables analysis; a cold start normally takes about a minute. The current single-worker job manager, uploaded files, rendered frames, SQLite payment state, and completed results therefore last only while that backend instance stays alive. Active job polling keeps a running analysis awake, but a restart or idle spin-down cannot recover an old job. This is the retained hackathon-demo behavior; no paid persistence is configured.

Use short demo clips. The API enforces the existing 200 MB upload limit, but FFmpeg processing and the free instance's ephemeral disk make smaller videos more reliable.

## Public verification

1. Check `GET /api/health` on Render.
2. Open the Vercel HTTPS URL in a normal browser and run basic analysis with a short MP4.
3. In Nimiq Pay Testnet, open the Vercel URL using `nimiqpay://miniapp?url=<your-domain>` or `https://nimpay.app/miniapps/open/<your-domain>`.
4. Confirm one 10 NIM payment unlocks only its corresponding job; begin a new analysis and confirm it requires a new payment.

## Inspect upload transport experiment and revert

This is a frontend-only experiment. The last known production frontend is commit `287fc24` (`Gate uploads on backend readiness`). That build used `XMLHttpRequest` plus an upload-progress listener for `POST /api/inspect`, which forced a CORS preflight. Failed attempts showed `OPTIONS 200` on Render and no matching POST.

Production bundles on `https://prometheus-beta-liard.vercel.app/` immediately before this experiment:

- `/assets/index-DO7J5aCx.js`
- `/assets/index-B7y_FTlV.css`

If revert is needed, those filenames returning in the live `index.html` confirm Vercel is serving the previous frontend again.

### What the experiment commit contains

- `web/app.js` inspect uploads use `fetch(FormData)` with no `Content-Type` header and no upload-progress listener, so the browser should skip the OPTIONS preflight.
- Interrupted and timeout errors still include `upload_attempt_id` and still do not retry. One intentional click still creates one inspect request.
- Percent upload progress is replaced by an indeterminate bar (`web/styles.css`).
- Frontend tests in `web/app.test.js` cover the fetch inspect path.

The same commit also includes already-local frontend behavior that lived in those files: queue-position copy when the API sends `queue_position`, and treating a missing job (`404`/`410`) as a discarded recovery instead of a stuck processing screen. Backend `prometheus/api/jobs.py` is **not** in this commit. If the API does not send `queue_position`, the UI keeps using the existing stage text.

This commit does **not** include branding (`web/index.html`, `web/logo.js`, PNG marks), Gemini analyzer changes, or the jobs-manager changes. Those stay local until reviewed separately.

The backend API does not need a new Render deploy for this experiment. If GitHub auto-deploys Render anyway, the free instance will lose in-memory jobs, staged uploads, and the local payment database. That is existing Free-tier behavior, not part of the experiment.

### How to tell if it worked

Use a short MP4 in Chrome and in Nimiq Pay. Note the `upload_attempt_id` from the URL or the on-screen Reference. On Render:

- Success: no inspect OPTIONS, then `POST /api/inspect` and FastAPI `reached_fastapi=true`.
- Same failure with no OPTIONS: the preflight theory is wrong; revert and try same-origin on the Render URL next.
- OPTIONS still appears: the new frontend is not what the browser loaded (cache, wrong deployment, or a custom header reintroduced preflight).

### How to revert to the last working frontend

Prefer a revert commit so history stays intact. Do not `git reset --hard` on `main` if the experiment commit was already pushed.

```powershell
git revert <experiment-commit-sha>
git push origin main
```

`<experiment-commit-sha>` is the commit that introduced `fetch(FormData)` inspect uploads. After revert, Vercel should republish the `287fc24` XHR inspect path.

Emergency Vercel-only rollback, if Git is delayed: in the Vercel dashboard, open Deployments, find the production deployment for `287fc24`, and Promote it to Production. The API on Render can stay as-is.

After revert, confirm the production JS bundle no longer contains `is-indeterminate` inspect upload behavior, then retry one Chrome upload and compare Render logs again.
