"use strict";

import { init } from "@nimiq/mini-app-sdk";
import { apiUrl } from "./api-url.js";

const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;
const RECOVERY_KEY = "prometheus.recovery.v2";
const REQUEST_TIMEOUT_MS = 15000;
const UPLOAD_TIMEOUT_MS = 10 * 60 * 1000;
const CONSENSUS_ATTEMPTS = 15;
const API_HEALTH_RETRY_MS = 2000;
const API_HEALTH_DEADLINE_MS = 100000;

const PROCESS_STEPS = [
  { id: "uploading", label: "Uploading" },
  { id: "framing", label: "Extracting frames" },
  { id: "scenes", label: "Understanding scenes" },
  { id: "analyzing", label: "Analyzing visual language" },
  { id: "prompt", label: "Reconstructing prompt" },
];

const RESULT_GROUPS = [
  { title: "Overall visual style", keys: ["overall_style"] },
  { title: "Subjects", keys: ["subject"] },
  { title: "Environment", keys: ["scene_environment"] },
  { title: "Composition", keys: ["composition"] },
  { title: "Camera", keys: ["camera_movement", "camera_angle", "lens_characteristics", "depth_of_field"] },
  { title: "Depth & space", keys: ["depth"] },
  { title: "Lighting", keys: ["lighting"] },
  { title: "Color palette", keys: ["color_palette"] },
  { title: "Materials & textures", keys: ["textures_materials"] },
  { title: "Motion", keys: ["motion", "animation_characteristics"] },
  { title: "Editing rhythm", keys: ["editing_rhythm"] },
  { title: "Visual effects", keys: ["visual_effects"] },
  { title: "Typography & graphics", keys: ["typography", "graphic_design"] },
  { title: "Transitions", keys: ["transitions"] },
];

const REMIX_FIELDS = [
  { key: "subject", label: "Subject", category: "subject", placeholder: "e.g. a chrome dragon soaring over a city" },
  { key: "environment", label: "Environment", category: "scene_environment", placeholder: "e.g. a neon-lit Tokyo alley at night" },
  { key: "style", label: "Visual style", category: "overall_style", placeholder: "e.g. cinematic film noir" },
  { key: "lighting", label: "Lighting", category: "lighting", placeholder: "e.g. harsh neon nightclub lighting" },
  { key: "mood", label: "Mood", category: null, placeholder: "e.g. tense and ominous (added to the style)" },
  { key: "color_palette", label: "Color palette", category: "color_palette", placeholder: "e.g. teal and orange blockbuster grade" },
  { key: "camera", label: "Camera", category: "camera_movement", placeholder: "e.g. slow orbital dolly around the subject" },
  { key: "motion", label: "Motion", category: "motion", placeholder: "e.g. heavy slow-motion with drifting particles" },
];

const screens = {
  upload: document.getElementById("screen-upload"),
  processing: document.getElementById("screen-processing"),
  results: document.getElementById("screen-results"),
  remix: document.getElementById("screen-remix"),
};

const els = {
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  fileCard: document.getElementById("file-card"),
  fileName: document.getElementById("file-name"),
  fileMeta: document.getElementById("file-meta"),
  uploadPreview: document.getElementById("upload-preview"),
  fileClear: document.getElementById("file-clear"),
  analyzeBtn: document.getElementById("analyze-btn"),
  advancedBtn: document.getElementById("advanced-btn"),
  paymentStatus: document.getElementById("payment-status"),
  retryInitBtn: document.getElementById("retry-init-btn"),
  uploadError: document.getElementById("upload-error"),
  phaseText: document.getElementById("phase-text"),
  phaseDetail: document.getElementById("phase-detail"),
  uploadBar: document.getElementById("upload-bar"),
  uploadProgress: document.querySelector(".progress"),
  stepList: document.getElementById("step-list"),
  jobError: document.getElementById("job-error"),
  retryBtn: document.getElementById("retry-btn"),
  resTitle: document.getElementById("res-title"),
  resChips: document.getElementById("res-chips"),
  resAnalyzer: document.getElementById("res-analyzer"),
  resVideo: document.getElementById("res-video"),
  resSummary: document.getElementById("res-summary"),
  resAnalysis: document.getElementById("res-analysis"),
  analysisCard: document.getElementById("analysis-card"),
  promptCard: document.getElementById("prompt-card"),
  upgradeCard: document.getElementById("upgrade-card"),
  upgradeBtn: document.getElementById("upgrade-btn"),
  upgradeError: document.getElementById("upgrade-error"),
  resPrompt: document.getElementById("res-prompt"),
  copyPrompt: document.getElementById("copy-prompt"),
  remixBtn: document.getElementById("remix-btn"),
  resScenes: document.getElementById("res-scenes"),
  againBtn: document.getElementById("again-btn"),
  remixFields: document.getElementById("remix-fields"),
  remixRun: document.getElementById("remix-run"),
  remixError: document.getElementById("remix-error"),
  remixOriginal: document.getElementById("remix-original"),
  copyOriginal: document.getElementById("copy-original"),
  remixFlowLabel: document.getElementById("remix-flow-label"),
  remixResult: document.getElementById("remix-result"),
  remixPrompt: document.getElementById("remix-prompt"),
  copyRemix: document.getElementById("copy-remix"),
  remixBack: document.getElementById("remix-back"),
};

let selectedFile = null;
let selectedPreviewUrl = null;
let pollTimer = null;
let recovery = readRecovery();
let savedJobId = typeof recovery.jobId === "string" && recovery.jobId ? recovery.jobId : null;
let currentJobId = null;
let sourceJobId = typeof recovery.sourceJobId === "string" && recovery.sourceJobId
  ? recovery.sourceJobId
  : null;
let currentResult = null;
let currentRemixText = "";
let paymentConfig = null;
let nimiqPromise = null;
let nimiqReady = false;
let nimiqInitTask = null;
let apiReady = false;
let apiReadyTask = null;
let pendingPayment = recovery.payment || null;
let pollFailures = 0;
let actionBusy = false;
let pollingJobId = null;
const inspectLabel = els.analyzeBtn.querySelector("span");
const inspectDetail = els.analyzeBtn.querySelector("small");

function readRecovery() {
  try {
    return JSON.parse(localStorage.getItem(RECOVERY_KEY) || "{}") || {};
  } catch (error) {
    return {};
  }
}

function persistRecovery() {
  recovery = { jobId: currentJobId, sourceJobId, payment: pendingPayment };
  try {
    if (currentJobId || pendingPayment) localStorage.setItem(RECOVERY_KEY, JSON.stringify(recovery));
    else localStorage.removeItem(RECOVERY_KEY);
  } catch (error) {
    // Recovery is best-effort when WebView storage is unavailable.
  }
}

async function fetchWithTimeout(url, options = {}, timeout = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function fetchWithRetry(url, options = {}, attempts = 3) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetchWithTimeout(url, options);
      if (response.status < 500 || attempt === attempts - 1) return response;
      lastError = new Error(`Server returned ${response.status}.`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
  }
  throw lastError;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function syncStartupState() {
  els.analyzeBtn.classList.toggle("initializing", !apiReady);
  inspectLabel.textContent = apiReady ? "Inspect video" : "Initializing";
  inspectDetail.textContent = apiReady ? "Free local inspection" : "Analysis service waking up";
  syncUploadButtons();
}

async function pingUploadPath() {
  const pingId = createUploadAttemptId();
  const form = new FormData();
  form.append(
    "file",
    new Blob(["prometheus-upload-ping"], { type: "application/octet-stream" }),
    "ping.bin",
  );
  const response = await fetchWithTimeout(
    apiUrl(`/api/upload-ping?upload_ping_id=${encodeURIComponent(pingId)}`),
    { method: "POST", body: form },
  );
  if (response.status === 204) return "ok";
  if (response.status === 404 || response.status === 405) return "unsupported";
  return "fail";
}

async function waitForApiReady() {
  if (apiReady) return true;
  if (apiReadyTask) return apiReadyTask;

  apiReadyTask = (async () => {
    const deadline = Date.now() + API_HEALTH_DEADLINE_MS;
    els.retryInitBtn.classList.add("hidden");
    els.paymentStatus.classList.remove("ready");
    els.paymentStatus.textContent = "Starting Prometheus… The analysis service is waking up. This may take a moment.";
    syncStartupState();
    while (Date.now() < deadline) {
      try {
        const response = await fetchWithTimeout(
          apiUrl("/api/health"), {}, Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now())
        );
        const health = await response.json().catch(() => ({}));
        if (response.ok && health.status === "ok") {
          els.paymentStatus.textContent = "Preparing the analysis service…";
          const readyResponse = await fetchWithTimeout(
            apiUrl("/api/ready"), {}, Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now())
          );
          const ready = await readyResponse.json().catch(() => ({}));
          if (readyResponse.ok && ready.status === "ready") {
            const ping = await pingUploadPath();
            if (ping === "ok" || ping === "unsupported") {
              apiReady = true;
              syncStartupState();
              return true;
            }
            els.paymentStatus.textContent = "Upload service is reconnecting…";
            syncStartupState();
          }
        }
      } catch (error) {
        // Render can accept a liveness request before its local upload dependencies are ready.
      }

      if (Date.now() < deadline) {
        await delay(Math.min(API_HEALTH_RETRY_MS, deadline - Date.now()));
      }
    }
    apiReady = false;
    els.analyzeBtn.classList.remove("initializing");
    inspectLabel.textContent = "Service unavailable";
    inspectDetail.textContent = "Connection disrupted";
    els.paymentStatus.textContent = "Connection disrupted. The analysis service did not become ready. Try again shortly.";
    els.retryInitBtn.classList.remove("hidden");
    syncUploadButtons();
    return false;
  })();

  try {
    return await apiReadyTask;
  } finally {
    apiReadyTask = null;
  }
}

async function isUploadReady() {
  if (!apiReady) return false;
  try {
    const response = await fetchWithTimeout(apiUrl("/api/ready"));
    const ready = await response.json().catch(() => ({}));
    if (response.ok && ready.status === "ready") return true;
  } catch (error) {
    // This check only decides whether it is safe to begin a new multipart upload.
  }
  apiReady = false;
  syncStartupState();
  void waitForApiReady();
  return false;
}

function createUploadAttemptId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `upload-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function uploadAttemptUrl(endpoint, attemptId) {
  const separator = endpoint.includes("?") ? "&" : "?";
  return apiUrl(`${endpoint}${separator}upload_attempt_id=${encodeURIComponent(attemptId)}`);
}

async function waitForConsensus(nimiq) {
  for (let attempt = 0; attempt < CONSENSUS_ATTEMPTS; attempt += 1) {
    try {
      const consensus = await nimiq.isConsensusEstablished();
      const consensusError = providerError(consensus);
      if (consensusError) throw consensusError;
      if (consensus) return;
    } catch (error) {
      if (attempt === CONSENSUS_ATTEMPTS - 1) throw error;
    }
    els.paymentStatus.textContent = "Connecting Nimiq Pay to testnet…";
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error("Nimiq Pay could not establish testnet consensus. Check its internet connection and testnet setting.");
}

function showScreen(name) {
  for (const key of Object.keys(screens)) {
    screens[key].classList.toggle("hidden", key !== name);
  }
  window.scrollTo(0, 0);
  screens[name].focus({ preventScroll: true });
}

function showError(box, message) {
  box.textContent = message;
  box.classList.remove("hidden");
}

function providerError(value) {
  if (typeof value !== "object" || value === null || !("error" in value)) return null;
  const error = new Error(value.error?.message || "Nimiq provider request failed.");
  error.name = value.error?.type || "ProviderError";
  return error;
}

function walletErrorKind(error) {
  const name = String(error?.type || error?.name || "").toUpperCase();
  const compactName = name.replace(/[^A-Z]/g, "");
  if (error?.code === 4001 || compactName.includes("PERMISSIONDENIED")) return "cancelled";
  if (error?.code === -32602 || compactName.includes("INVALIDTRANSACTION")) return "invalid";
  return "unknown";
}

function syncUploadButtons() {
  els.analyzeBtn.disabled = !selectedFile || actionBusy || !apiReady;
  els.advancedBtn.disabled = true;
  els.upgradeBtn.disabled = actionBusy || currentResult?.tier !== "basic";
}

function initializeNimiq() {
  if (nimiqReady) return Promise.resolve();
  if (nimiqInitTask) return nimiqInitTask;
  nimiqInitTask = (async () => {
    try {
    const backendReady = await waitForApiReady();
    if (!backendReady) return;
    if (!paymentConfig) {
      const response = await fetchWithRetry(apiUrl("/api/payments/config"));
      if (!response.ok) throw new Error("payment configuration unavailable");
      paymentConfig = await response.json();
      els.advancedBtn.textContent = `Pay ${paymentConfig.amount_nim} NIM & run advanced analysis`;
      els.upgradeBtn.textContent = `Pay ${paymentConfig.amount_nim} NIM & analyze`;
    }
    if (!paymentConfig.enabled) {
      els.paymentStatus.textContent = "Advanced AI analysis is not configured on this server.";
      syncUploadButtons();
      return;
    }
    nimiqPromise = init({ timeout: 10000 });
    await nimiqPromise;
    nimiqReady = true;
    els.paymentStatus.textContent = currentJobId && pendingPayment && pendingPayment.sourceJobId === sourceJobId
      ? "Payment recovery ready for this analysis."
      : `Nimiq Pay connected · ${paymentConfig.network} required`;
    els.paymentStatus.classList.add("ready");
    } catch (error) {
      nimiqPromise = null;
      if (!apiReady) {
        return;
      }
      els.paymentStatus.textContent = paymentConfig
        ? "Open this app inside Nimiq Pay to purchase advanced analysis."
        : "Connection disrupted. The analysis service did not respond. Check your connection and try again.";
    } finally {
      nimiqInitTask = null;
      syncUploadButtons();
    }
  })();
  return nimiqInitTask;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtTime(seconds) {
  return `${Number(seconds).toFixed(1)}s`;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  const original = button.textContent;
  button.textContent = "Copied";
  button.classList.add("done");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("done");
  }, 1600);
}

function setFile(file) {
  if (!file) return;
  if (!/\.(mp4|m4v|mov|webm)$/i.test(file.name)) {
    showError(els.uploadError, "Unsupported file type. Upload an MP4, MOV or WebM video.");
    return;
  }
  if (file.size === 0) {
    showError(els.uploadError, "That file is empty. Choose a different video.");
    return;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    showError(els.uploadError, "That video exceeds the 200 MB limit. Choose a smaller file.");
    return;
  }
  selectedFile = file;
  clearSelectedPreview();
  if (els.uploadPreview && typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
    selectedPreviewUrl = URL.createObjectURL(file);
    els.uploadPreview.src = selectedPreviewUrl;
  }
  els.uploadError.classList.add("hidden");
  els.dropzone.classList.add("hidden");
  els.fileCard.classList.remove("hidden");
  els.fileName.textContent = file.name;
  els.fileMeta.textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  syncUploadButtons();
}

function clearSelectedPreview() {
  if (selectedPreviewUrl && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(selectedPreviewUrl);
  }
  selectedPreviewUrl = null;
  if (els.uploadPreview) {
    els.uploadPreview.removeAttribute("src");
  }
}

function clearFile() {
  selectedFile = null;
  clearSelectedPreview();
  persistRecovery();
  els.fileInput.value = "";
  els.fileCard.classList.add("hidden");
  els.dropzone.classList.remove("hidden");
  syncUploadButtons();
}

function mapStage(stage) {
  if (!stage || stage === "Queued") return { step: "uploading", detail: "Waiting for a worker" };
  if (stage === "Inspecting video") return { step: "framing", detail: "Reading video metadata" };
  if (stage === "Detecting scene cuts") return { step: "scenes", detail: "Measuring frame-to-frame visual change" };
  if (stage.startsWith("Analyzing scenes")) return { step: "analyzing", detail: stage + " — sampling frames and calling the vision model" };
  if (stage === "Sampling representative frames") return { step: "framing", detail: "Extracting global representative frames" };
  if (stage === "Analyzing shared characteristics") return { step: "analyzing", detail: "Synthesizing characteristics shared across scenes" };
  if (stage === "Saving results" || stage === "Complete") return { step: "prompt", detail: "Building the reconstructed prompt" };
  return { step: "analyzing", detail: stage };
}

function renderSteps(activeStep, uploadDone) {
  const order = PROCESS_STEPS.map((s) => s.id);
  const activeIndex = order.indexOf(activeStep);
  els.stepList.innerHTML = "";
  PROCESS_STEPS.forEach((step, index) => {
    const li = document.createElement("li");
    let state = "pending";
    if (index < activeIndex || (uploadDone && activeStep === "prompt")) state = "done";
    if (step.id === activeStep) state = "active";
    li.className = `step-state-${state}`;
    li.innerHTML = `<span class="dot"></span>${escapeHtml(step.label)}`;
    els.stepList.appendChild(li);
  });
}

async function startAnalysis(endpoint) {
  if (!selectedFile || actionBusy || !apiReady) return;
  actionBusy = true;
  syncUploadButtons();
  if (!await isUploadReady()) {
    actionBusy = false;
    syncUploadButtons();
    return;
  }
  clearTimeout(pollTimer);
  currentJobId = null;
  sourceJobId = null;
  currentResult = null;
  pendingPayment = null;
  savedJobId = null;
  persistRecovery();
  els.uploadError.classList.add("hidden");
  els.analyzeBtn.disabled = true;
  showScreen("processing");
  els.jobError.classList.add("hidden");
  els.retryBtn.classList.add("hidden");
  els.uploadBar.style.width = "";
  els.uploadProgress.classList.add("is-indeterminate");
  els.uploadProgress.removeAttribute("aria-valuenow");
  renderSteps("uploading", false);
  els.phaseText.textContent = "Uploading video";
  els.phaseDetail.textContent = "Sending the file to the backend";

  const attemptId = createUploadAttemptId();
  const failUpload = (message) => {
    actionBusy = false;
    els.uploadProgress.classList.remove("is-indeterminate");
    showError(els.jobError, message);
    els.retryBtn.classList.remove("hidden");
    els.retryBtn.textContent = "Back to upload";
    syncUploadButtons();
  };
  // Diagnostic pre-check only: prove the first bytes are readable before the
  // request body is streamed. A passing pre-check says nothing about the rest
  // of the file or whether the upload will succeed. Where the read API is
  // absent the pre-check is skipped and the upload proceeds as before.
  let precheck = "skipped";
  let precheckError = "";
  let precheckErrorName = "";
  try {
    const probe = selectedFile.slice(0, 64 * 1024);
    if (typeof probe.arrayBuffer === "function") {
      await probe.arrayBuffer();
      precheck = "readable";
    }
  } catch (error) {
    precheck = "unreadable";
    precheckErrorName = error?.name || "UnknownError";
    precheckError = ` error=${error?.name || "unknown"} message=${String(error?.message ?? "").slice(0, 160)}`;
  }
  console.info(`upload_attempt=${attemptId} precheck=${precheck}${precheckError}`);
  if (precheck === "unreadable") {
    // Proven by device testing: picker sources such as Gallery can hand out
    // files whose bytes this browser context may not read, while the same
    // video selected through Files uploads fine. Guide only those cases
    // elsewhere; every other outcome keeps the previous behavior.
    const reason = precheckErrorName === "NotReadableError" || precheckErrorName === "NotFoundError"
      ? "This video couldn't be accessed through the selected source. Please select it again using Files or Browse instead of Gallery."
      : "The selected video could not be read from this device before upload.";
    failUpload(
      `${reason} (${precheckErrorName}) No upload was started and no retry was made. Reference: ${attemptId}.`,
    );
    return;
  }
  const form = new FormData();
  form.append("file", selectedFile);
  let response;
  try {
    // No Content-Type or upload listener: keep this FormData POST a simple CORS request.
    response = await fetchWithTimeout(
      uploadAttemptUrl(endpoint, attemptId),
      { method: "POST", body: form },
      UPLOAD_TIMEOUT_MS,
    );
  } catch (error) {
    const timedOut = error?.name === "AbortError";
    failUpload(
      timedOut
        ? `Upload timed out before Prometheus returned a response. No upload retry was made. Reference: ${attemptId}.`
        : `Upload connection was interrupted before Prometheus returned a response. No upload retry was made. Reference: ${attemptId}.`,
    );
    return;
  }

  els.uploadProgress.classList.remove("is-indeterminate");
  els.uploadBar.style.width = "100%";
  els.uploadProgress.setAttribute("aria-valuenow", "100");
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.job_id) {
    failUpload(`Could not start analysis: ${data.detail || `upload failed (${response.status})`}`);
    return;
  }
  currentJobId = data.job_id;
  sourceJobId = data.job_id;
  selectedFile = null;
  actionBusy = false;
  persistRecovery();
  startPolling(currentJobId);
}

async function startAdvancedAnalysis(payment) {
  if (!payment?.sourceJobId) throw new Error("The basic analysis for this payment is unavailable.");
  showScreen("processing");
  els.jobError.classList.add("hidden");
  els.retryBtn.classList.add("hidden");
  renderSteps("uploading", true);
  els.phaseText.textContent = "Starting analysis";
  els.phaseDetail.textContent = "Using the inspected video and confirmed payment";

  const form = new FormData();
  form.append("source_job_id", payment.sourceJobId);
  form.append("payment_quote_id", payment.quoteId);
  form.append("payment_token", payment.token);
  let response;
  try {
    response = await fetchWithTimeout(apiUrl("/api/analyze"), { method: "POST", body: form });
  } catch (error) {
    throw new Error("Connection interrupted while starting analysis. Retry to recover this job's payment.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.job_id) {
    throw new Error(data.detail || `could not start analysis (${response.status})`);
  }
  currentJobId = data.job_id;
  persistRecovery();
  startPolling(currentJobId);
}

async function waitForPayment(quoteId, txHash, expectedSourceJobId) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    let response;
    try {
      response = await fetchWithTimeout(apiUrl(`/api/payments/quotes/${quoteId}/verify`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tx_hash: txHash }),
      });
    } catch (error) {
      throw new Error("Network interrupted while checking the payment. Reconnect and retry verification.");
    }
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      if (data.source_job_id !== expectedSourceJobId || !["verified", "reserved", "consumed"].includes(data.state)) {
        throw new Error("Payment verification did not match this analysis job.");
      }
      if (txHash && data.tx_hash?.toLowerCase() !== txHash.toLowerCase()) {
        throw new Error("Payment verification returned a different transaction hash.");
      }
      return data;
    }
    if (response.status !== 409) {
      const error = new Error(data.detail || "Payment verification failed.");
      error.paymentExpired = response.status === 400 && /expired/i.test(error.message);
      throw error;
    }
    els.paymentStatus.textContent = "Payment sent. Waiting for confirmation…";
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error("Payment confirmation timed out. Try verifying again shortly.");
}

async function startPaidAnalysis(errorBox = els.uploadError) {
  if (actionBusy || currentResult?.tier !== "basic") return;
  const basicJobId = currentResult.job_id;
  if (!basicJobId) return;
  actionBusy = true;
  errorBox.classList.add("hidden");
  syncUploadButtons();
  let paymentVerified = false;
  try {
    if (pendingPayment && pendingPayment.sourceJobId !== basicJobId) pendingPayment = null;
    sourceJobId = basicJobId;
    persistRecovery();
    if (pendingPayment) {
      els.paymentStatus.textContent = "Checking the existing payment confirmation…";
      await waitForPayment(pendingPayment.quoteId, pendingPayment.txHash, basicJobId);
      paymentVerified = true;
      const payment = pendingPayment;
      els.paymentStatus.textContent = "Payment confirmed. Starting advanced analysis…";
      await startAdvancedAnalysis(payment);
      return;
    }
    if (!nimiqReady) await initializeNimiq();
    if (!nimiqReady || !nimiqPromise || !paymentConfig?.enabled) {
      throw new Error("Open this app inside Nimiq Pay to request advanced analysis.");
    }
    const nimiq = await nimiqPromise;
    els.paymentStatus.textContent = "Checking Nimiq Pay testnet connection…";
    await waitForConsensus(nimiq);
    els.paymentStatus.textContent = "Preparing a unique payment request…";
    const quoteResponse = await fetchWithRetry(apiUrl("/api/payments/quotes"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_job_id: basicJobId }),
    });
    const quote = await quoteResponse.json();
    if (!quoteResponse.ok) throw new Error(quote.detail || "Could not create payment request.");
    els.paymentStatus.textContent = "Confirm Nimiq Pay is on testnet, then approve the payment.";
    if (quote.source_job_id !== basicJobId) throw new Error("Payment quote was created for a different analysis job.");
    pendingPayment = { sourceJobId: basicJobId, quoteId: quote.id, token: quote.token, txHash: null };
    persistRecovery();
    const result = await nimiq.sendBasicTransactionWithData({
      recipient: quote.recipient,
      value: quote.amount_luna,
      data: quote.memo,
    });
    const resultError = providerError(result);
    if (resultError) {
      pendingPayment = null;
      persistRecovery();
      throw resultError;
    }
    if (typeof result !== "string" || !/^[0-9a-f]{64}$/i.test(result)) {
      throw new Error("Nimiq Pay did not return a valid transaction hash. The quote is saved for recovery; do not pay again.");
    }
    pendingPayment.txHash = result.toLowerCase();
    persistRecovery();
    await waitForPayment(quote.id, pendingPayment.txHash, basicJobId);
    paymentVerified = true;
    els.paymentStatus.textContent = "Payment confirmed. Starting advanced analysis…";
    await startAdvancedAnalysis(pendingPayment);
  } catch (error) {
    const errorKind = walletErrorKind(error);
    const cancelled = errorKind === "cancelled";
    if (cancelled || errorKind === "invalid" || error?.paymentExpired) {
      pendingPayment = null;
      persistRecovery();
    }
    const recoverable = Boolean(pendingPayment);
    if (!recoverable) {
      pendingPayment = null;
      persistRecovery();
    }
    const message = cancelled
      ? "Payment cancelled. No analysis was started."
      : error?.paymentExpired
        ? "The payment request expired. Tap the payment button to create a new testnet request."
      : recoverable
        ? `${error.message} Tap the payment button again to resume; do not pay again.`
        : error.message;
    showScreen("results");
    showError(errorBox, message);
    els.paymentStatus.textContent = cancelled ? "Payment cancelled."
      : paymentVerified ? "Payment verified for this analysis; retry without paying again."
        : recoverable ? "Payment pending for this analysis; resume verification without paying again."
          : "Advanced payment was not completed.";
    syncUploadButtons();
  } finally {
    actionBusy = false;
    syncUploadButtons();
  }
}

function startPolling(jobId, initialJob = null) {
  clearTimeout(pollTimer);
  pollFailures = 0;
  showScreen("processing");
  els.jobError.classList.add("hidden");
  els.retryBtn.classList.add("hidden");
  if (initialJob) {
    applyJobStatus(jobId, initialJob);
    return;
  }
  pollJob(jobId);
}

function schedulePoll(jobId, delay = 1500) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => pollJob(jobId), delay);
}

function queueStatusText(position) {
  if (position === 0) return "You're next in the queue. Your analysis will start automatically.";
  const jobs = position === 1 ? "1 job ahead of you" : `${position} jobs ahead of you`;
  return `${jobs}. Your analysis will start automatically.`;
}

async function pollJob(jobId) {
  if (pollingJobId === jobId) return;
  pollingJobId = jobId;
  try {
    await pollJobOnce(jobId);
  } finally {
    pollingJobId = null;
  }
}

async function pollJobOnce(jobId) {
  if (jobId !== currentJobId) return;
  let job;
  try {
    const response = await fetchWithTimeout(apiUrl(`/api/jobs/${jobId}`));
    if (response.status === 404 || response.status === 410) {
      discardSavedJob();
      return;
    }
    if (!response.ok) throw new Error(`Server returned ${response.status}.`);
    job = await response.json();
    if (jobId !== currentJobId) return;
  } catch (err) {
    pollFailures += 1;
    if (pollFailures < 3 && currentJobId === jobId) {
      schedulePoll(jobId, 1500 * pollFailures);
      return;
    }
    showError(els.jobError, `Connection interrupted: ${err.message || "could not check analysis status"}`);
    els.retryBtn.textContent = currentJobId ? "Retry status" : "Back to upload";
    els.retryBtn.classList.remove("hidden");
    return;
  }
  await applyJobStatus(jobId, job);
}

async function applyJobStatus(jobId, job) {
  if (jobId !== currentJobId) return;
  pollFailures = 0;
  if (Number.isInteger(job.queue_position) && job.queue_position >= 0) {
    els.phaseText.textContent = "Queued";
    els.phaseDetail.textContent = queueStatusText(job.queue_position);
    renderSteps("uploading", true);
    schedulePoll(jobId);
    return;
  }
  const mapped = mapStage(job.stage || "");
  els.phaseText.textContent = PROCESS_STEPS.find((s) => s.id === mapped.step).label;
  els.phaseDetail.textContent = job.stage || "";
  renderSteps(mapped.step, true);
  if (job.state === "complete") {
    clearTimeout(pollTimer);
    fetchResult(jobId);
  } else if (job.state === "error") {
    clearTimeout(pollTimer);
    if (pendingPayment && sourceJobId && jobId !== sourceJobId) {
      currentJobId = sourceJobId;
      persistRecovery();
      await fetchResult(sourceJobId);
      showError(els.upgradeError, `Advanced analysis failed: ${job.error || "unknown error"}. Retry this job with its existing payment.`);
    } else {
      showError(els.jobError, `Analysis failed: ${job.error || "unknown error"}`);
      currentJobId = null;
      sourceJobId = null;
      persistRecovery();
      els.retryBtn.textContent = "Back to upload";
      els.retryBtn.classList.remove("hidden");
    }
  } else if (job.state !== "processing") {
    discardSavedJob();
  } else {
    schedulePoll(jobId);
  }
}

function discardSavedJob() {
  clearTimeout(pollTimer);
  currentJobId = null;
  sourceJobId = null;
  currentResult = null;
  pendingPayment = null;
  savedJobId = null;
  persistRecovery();
  clearFile();
  els.jobError.classList.add("hidden");
  els.retryBtn.classList.add("hidden");
  showScreen("upload");
}

async function fetchResult(jobId) {
  try {
    const response = await fetchWithTimeout(apiUrl(`/api/jobs/${jobId}/result`));
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `status ${response.status}`);
    currentResult = result;
    if (result.tier === "advanced") {
      pendingPayment = null;
      sourceJobId = null;
    } else {
      sourceJobId = result.job_id;
    }
    persistRecovery();
    renderResults(result);
  } catch (err) {
    showError(els.jobError, `Could not load results: ${err.message}`);
    els.retryBtn.textContent = "Retry status";
    els.retryBtn.classList.remove("hidden");
  }
}

function groupCopyText(title, entries) {
  const lines = [title];
  for (const entry of entries) {
    lines.push(`[${entry.label} — confidence ${Math.round((entry.confidence || 0) * 100)}%]`);
    for (const o of entry.observations || []) lines.push(`Observed: ${o}`);
    for (const inf of entry.inferences || []) lines.push(`Inferred: ${inf}`);
  }
  return lines.join("\n");
}

function entriesHtml(entries) {
  return entries.map((entry) => {
    const parts = [`<div class="cat-head"><span>${escapeHtml(entry.label)}</span><span class="conf">${Math.round((entry.confidence || 0) * 100)}%</span></div>`];
    if (entry.observations && entry.observations.length) {
      parts.push(`<div class="k">Observed</div><ul>`);
      for (const o of entry.observations) parts.push(`<li>${escapeHtml(o)}</li>`);
      parts.push(`</ul>`);
    }
    if (entry.inferences && entry.inferences.length) {
      parts.push(`<div class="k">Inferred</div><ul>`);
      for (const inf of entry.inferences) parts.push(`<li>${escapeHtml(inf)}</li>`);
      parts.push(`</ul>`);
    }
    return `<div class="cat">${parts.join("")}</div>`;
  }).join("");
}

function renderResults(result) {
  const video = result.video || {};
  els.resTitle.textContent = video.name || "Analysis results";
  els.resChips.innerHTML = "";
  const chips = [
    video.width && video.height ? `${video.width}×${video.height}` : null,
    video.duration ? `${video.duration.toFixed(1)}s` : null,
    video.fps ? `${video.fps} fps` : null,
    result.scenes ? `${result.scenes.length} scenes` : null,
  ].filter(Boolean);
  for (const chip of chips) {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = chip;
    els.resChips.appendChild(span);
  }
  const analyzer = result.analyzer || {};
  if (result.tier === "basic") {
    els.resAnalyzer.className = "analyzer local";
    els.resAnalyzer.textContent = "FREE LOCAL INSPECTION · no AI model or payment used.";
  } else if (analyzer.mode === "mock") {
    els.resAnalyzer.className = "analyzer mock";
    els.resAnalyzer.textContent = "MOCK MODE — placeholder findings, no vision model was used.";
  } else {
    els.resAnalyzer.className = "analyzer";
    els.resAnalyzer.textContent = `Real vision model: ${analyzer.provider || "?"} / ${analyzer.model || "?"}`;
  }
  if (video.preview_url) {
    els.resVideo.src = apiUrl(video.preview_url);
    els.resVideo.classList.remove("hidden");
  } else {
    els.resVideo.classList.add("hidden");
  }
  els.resSummary.textContent = result.summary || "";
  const isBasic = result.tier === "basic";
  els.analysisCard.classList.toggle("hidden", isBasic);
  els.promptCard.classList.toggle("hidden", isBasic);
  els.upgradeCard.classList.toggle("hidden", !isBasic);
  syncUploadButtons();

  const byKey = {};
  for (const entry of result.breakdown || []) byKey[entry.key] = entry;
  els.resAnalysis.innerHTML = "";
  for (const group of RESULT_GROUPS) {
    const entries = group.keys.map((key) => byKey[key]).filter(Boolean);
    if (!entries.length) continue;
    const block = document.createElement("div");
    block.className = "group";
    block.innerHTML = `
      <div class="card-head">
        <h4>${escapeHtml(group.title)}</h4>
        <button class="copy copy-section" type="button">Copy section</button>
      </div>
      ${entriesHtml(entries)}`;
    block.querySelector(".copy-section").addEventListener("click", (event) =>
      copyText(groupCopyText(group.title, entries), event.currentTarget)
    );
    els.resAnalysis.appendChild(block);
  }

  els.resPrompt.textContent = result.prompt_markdown || "";

  els.resScenes.innerHTML = "";
  for (const scene of result.scenes || []) {
    const card = document.createElement("div");
    card.className = "scene";
    const thumb = scene.frames && scene.frames.length ? scene.frames[0] : "";
    const cats = entriesHtml(scene.breakdown || []);
    const details = isBasic ? "" : `
      <p class="scene-desc">${escapeHtml(scene.description || "")}</p>
      <details>
        <summary>Analysis details &amp; scene prompt</summary>
        ${cats}
        <div class="scene-prompt">
          <div class="card-head">
            <h4 style="margin:0">Scene prompt</h4>
            <button class="copy scene-copy" type="button">Copy</button>
          </div>
          <pre>${escapeHtml(scene.prompt_markdown || "")}</pre>
        </div>
      </details>`;
    card.innerHTML = `
      <div class="scene-top">
        ${thumb ? `<img loading="lazy" src="${escapeHtml(apiUrl(thumb))}" alt="Scene ${scene.index} thumbnail">` : ""}
        <div>
          <div class="scene-title">Scene ${scene.index}</div>
          <div class="scene-meta">${fmtTime(scene.start)} – ${fmtTime(scene.end)} &middot; ${Number(scene.duration).toFixed(1)}s</div>
        </div>
      </div>
      ${details}`;
    const sceneCopy = card.querySelector(".scene-copy");
    if (sceneCopy) sceneCopy.addEventListener("click", (event) =>
      copyText(scene.prompt_markdown || "", event.currentTarget)
    );
    els.resScenes.appendChild(card);
  }
  showScreen("results");
}

function inferencesFor(result, categoryKey) {
  const entry = (result.breakdown || []).find((e) => e.key === categoryKey);
  return entry ? (entry.inferences || []).join(" ") : "";
}

function openRemix() {
  if (!currentResult) return;
  els.remixOriginal.textContent = currentResult.prompt_markdown || "";
  els.remixFields.innerHTML = "";
  els.remixResult.classList.add("hidden");
  els.remixFlowLabel.classList.add("hidden");
  els.remixError.classList.add("hidden");
  currentRemixText = "";
  for (const field of REMIX_FIELDS) {
    const wrap = document.createElement("label");
    wrap.className = "remix-field";
    const prefill = field.category ? inferencesFor(currentResult, field.category) : "";
    wrap.innerHTML = `<span>${escapeHtml(field.label)}</span>
      <textarea rows="2" data-key="${field.key}" placeholder="${escapeHtml(field.placeholder)}">${escapeHtml(prefill)}</textarea>`;
    els.remixFields.appendChild(wrap);
  }
  showScreen("remix");
}

async function runRemix() {
  els.remixError.classList.add("hidden");
  const overrides = {};
  els.remixFields.querySelectorAll("textarea").forEach((area) => {
    const text = area.value.trim();
    if (text) overrides[area.dataset.key] = text;
  });
  if (!Object.keys(overrides).length) {
    showError(els.remixError, "Change at least one attribute to generate a remix.");
    return;
  }
  els.remixRun.disabled = true;
  els.remixRun.textContent = "Generating remix…";
  try {
    const response = await fetch(apiUrl(`/api/jobs/${currentJobId}/remix`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(overrides),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `remix failed (${response.status})`);
    currentRemixText = data.prompt_markdown || "";
    els.remixPrompt.textContent = currentRemixText;
    els.remixResult.classList.remove("hidden");
    els.remixFlowLabel.classList.remove("hidden");
  } catch (err) {
    showError(els.remixError, `Could not generate remix: ${err.message}`);
  } finally {
    els.remixRun.disabled = false;
    els.remixRun.textContent = "Generate remix";
  }
}

els.dropzone.addEventListener("click", (event) => {
  if (event.target !== els.fileInput) els.fileInput.click();
});
els.dropzone.addEventListener("keydown", (event) => {
  if (event.target === els.dropzone && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    els.fileInput.click();
  }
});
["dragover", "dragenter"].forEach((name) =>
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((name) =>
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.remove("dragover");
  })
);
els.dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) setFile(file);
});
els.fileInput.addEventListener("change", () => {
  if (els.fileInput.files && els.fileInput.files[0]) setFile(els.fileInput.files[0]);
});
els.fileClear.addEventListener("click", clearFile);
els.retryInitBtn.addEventListener("click", () => {
  if (apiReady || apiReadyTask) return;
  void waitForApiReady();
});
els.analyzeBtn.addEventListener("click", () => startAnalysis("/api/inspect"));
els.advancedBtn.addEventListener("click", () => startPaidAnalysis());
els.upgradeBtn.addEventListener("click", () => startPaidAnalysis(els.upgradeError));
els.copyPrompt.addEventListener("click", () =>
  copyText(els.resPrompt.textContent, els.copyPrompt)
);
els.remixBtn.addEventListener("click", openRemix);
els.remixRun.addEventListener("click", runRemix);
els.copyOriginal.addEventListener("click", () =>
  copyText(els.remixOriginal.textContent, els.copyOriginal)
);
els.copyRemix.addEventListener("click", () => copyText(currentRemixText, els.copyRemix));
els.remixBack.addEventListener("click", () => showScreen("results"));
els.againBtn.addEventListener("click", () => {
  clearTimeout(pollTimer);
  clearFile();
  currentResult = null;
  currentJobId = null;
  sourceJobId = null;
  pendingPayment = null;
  savedJobId = null;
  persistRecovery();
  showScreen("upload");
});
els.retryBtn.addEventListener("click", () => {
  if (currentJobId) {
    startPolling(currentJobId);
    return;
  }
  syncUploadButtons();
  showScreen("upload");
});

// A new launch always opens the dashboard. Saved jobs are not navigated to automatically.
if (Object.values(screens).some((screen) => window.location.hash === `#${screen.id}`)) {
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}
showScreen("upload");
syncStartupState();
if (!savedJobId) {
  sourceJobId = null;
  pendingPayment = null;
  persistRecovery();
}

window.addEventListener("focus", () => {
  if (!apiReady) void waitForApiReady();
  if (!nimiqReady) initializeNimiq();
});
window.addEventListener("online", () => {
  if (!apiReady) void waitForApiReady();
  if (!nimiqReady) initializeNimiq();
  if (currentJobId) startPolling(currentJobId);
});
initializeNimiq();
