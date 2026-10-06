import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { initMock } = vi.hoisted(() => ({ initMock: vi.fn() }));
vi.mock("@nimiq/mini-app-sdk", () => ({ init: initMock }));

const html = readFileSync(resolve(process.cwd(), "web/index.html"), "utf8");
const HASH_A = "ab".repeat(32);
const HASH_B = "cd".repeat(32);
const RECOVERY_KEY = "prometheus.recovery.v2";
const WALLET_ADDRESS = "NQ07 0000 0000 0000 0000 0000 0000 0000 0000";
const SECOND_WALLET_ADDRESS = "NQ08 1111 1111 1111 1111 1111 1111 1111 1111";
const AUTH_BASE = "https://wallet-auth.test.invalid";
const AUTH_MESSAGE = "Prometheus wallet verification\nTEST fixture challenge";
const AUTH_ID = "12".repeat(16);
const WALLET_RPC = "https://rpc.testnet.nimiqwatch.com/";

function walletProvider(overrides = {}) {
  return {
    sign: vi.fn().mockResolvedValue({ publicKey: "ab".repeat(32), signature: "cd".repeat(64) }),
    listAccounts: vi.fn().mockResolvedValue([WALLET_ADDRESS]),
    isConsensusEstablished: vi.fn().mockResolvedValue(true),
    sendBasicTransactionWithData: vi.fn().mockResolvedValue(HASH_A),
    disconnect: vi.fn(),
    ...overrides,
  };
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function isInspectUrl(url) {
  return /\/api\/inspect(\?|$)/.test(String(url));
}

function isUploadPingUrl(url) {
  return /\/api\/upload-ping(\?|$)/.test(String(url));
}

function inspectCalls(fetchMock = globalThis.fetch) {
  return fetchMock.mock.calls.filter(([url]) => isInspectUrl(url));
}

function uploadPingCalls(fetchMock = globalThis.fetch) {
  return fetchMock.mock.calls.filter(([url]) => isUploadPingUrl(url));
}

async function flush() {
  for (let index = 0; index < 5; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

function chooseVideo(name = "clip.mp4") {
  const input = document.getElementById("file-input");
  Object.defineProperty(input, "files", {
    value: [new File(["video-data"], name, { type: "video/mp4" })],
    configurable: true,
  });
  input.dispatchEvent(new Event("change"));
}

async function completeBasic() {
  const analyze = document.getElementById("analyze-btn");
  analyze.click();
  await flush();
  const inspect = inspectCalls().at(-1);
  expect(inspect[0]).toMatch(/^\/api\/inspect\?upload_attempt_id=[A-Za-z0-9-]+$/);
  expect(inspect[1].method).toBe("POST");
  expect(inspect[1].body).toBeInstanceOf(FormData);
  expect(inspect[1].headers?.["Content-Type"]).toBeUndefined();
  expect(document.getElementById("screen-results").classList.contains("hidden")).toBe(false);
  expect(document.getElementById("upgrade-btn").disabled).toBe(false);
}

function basicResult(id) {
  return {
    job_id: id, tier: "basic", video: { name: "clip.mp4", duration: 1, width: 320, height: 180, fps: 24 },
    analyzer: { mode: "local" }, summary: "Basic result", scenes: [], breakdown: [], prompt_markdown: "",
  };
}

function advancedResult(id) {
  return {
    job_id: id, tier: "advanced", video: { name: "clip.mp4", duration: 1, width: 320, height: 180, fps: 24 },
    analyzer: { mode: "real", provider: "gemini", model: "gemini-flash-lite-latest" },
    summary: "Advanced result", scenes: [], breakdown: [], prompt_markdown: "Reconstructed prompt",
  };
}

function mockApi({ verifyError = null, inspectError = null, pingResponses = [], healthResponses = [], readyResponses = [], jobStatuses = [], jobStatusError = null,
  balanceError = null, balanceLuna = 12542000, balancesByAddress = null, rpcNetwork = "TestAlbatross",
  authError = null, authSigner = WALLET_ADDRESS, authHealthResponses = [] } = {}) {
  let authAccounts = [];
  let quoteNumber = 0;
  let inspectNumber = 0;
  const quotes = new Map();
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path === AUTH_BASE + "/api/health") {
      const status = authHealthResponses.length ? authHealthResponses.shift() : 200;
      return response({ status: "ok", wallet_auth_enabled: true }, status);
    }
    if (path === AUTH_BASE + "/api/wallet-auth/challenge") {
      authAccounts = JSON.parse(options.body).accounts;
      return response({ id: AUTH_ID, message: AUTH_MESSAGE, expires_at: 1800000300 });
    }
    if (path === AUTH_BASE + "/api/wallet-auth/verify") {
      if (authError instanceof Error) throw authError;
      if (authError) return response({ detail: authError }, 401);
      return response({ verified: true, signer_address: authSigner, approved_address_count: new Set(authAccounts.map((a) => a.replace(/\s+/g, "").toUpperCase())).size, scope: "current_page_signer" });
    }
    if (path === WALLET_RPC) {
      const request = JSON.parse(options.body);
      if (request.method === "getLatestBlock") return response({ result: { data: { network: rpcNetwork } } });
      if (request.method === "getAccountByAddress") {
        if (balanceError) throw balanceError;
        const balance = balancesByAddress?.[request.params[0]] ?? balanceLuna;
        if (balance instanceof Error) throw balance;
        return response({ result: { data: { balance } } });
      }
      throw new Error(`Unexpected RPC method: ${request.method}`);
    }
    if (path === "/api/health") return response(
      { status: "ok" },
      healthResponses.length ? healthResponses.shift() : 200
    );
    if (path === "/api/ready") return response(
      { status: "ready" },
      readyResponses.length ? readyResponses.shift() : 200
    );
    if (isUploadPingUrl(path)) {
      if (pingResponses.length) {
        const next = pingResponses.shift();
        if (next instanceof Error) throw next;
        return response({}, next);
      }
      return response({}, 204);
    }
    if (path === "/api/payments/config") return response({ enabled: true, amount_nim: 10, network: "testnet" });
    if (path === "/api/payments/quotes") {
      const sourceJobId = JSON.parse(options.body).source_job_id;
      quoteNumber += 1;
      const id = `quote-${quoteNumber}`;
      quotes.set(id, sourceJobId);
      return response({ id, source_job_id: sourceJobId, token: `token-${quoteNumber}`,
        recipient: "NQ43 TEST", amount_luna: 1_000_000, memo: `prometheus:${id}` });
    }
    if (path.includes("/verify")) {
      if (verifyError) throw verifyError;
      const quoteId = path.split("/")[4];
      const hash = JSON.parse(options.body).tx_hash;
      return response({ source_job_id: quotes.get(quoteId) || "basic-1", state: "verified", tx_hash: hash || HASH_A });
    }
    if (isInspectUrl(path)) {
      if (inspectError) throw inspectError;
      inspectNumber += 1;
      return response({ job_id: `basic-${inspectNumber}` }, 202);
    }
    if (path === "/api/analyze") {
      return response({ job_id: `advanced-${options.body.get("source_job_id")}` }, 202);
    }
    if (path.endsWith("/remix")) return response({ prompt_markdown: "Remixed prompt" });
    if (path.startsWith("/api/jobs/") && path.endsWith("/result")) {
      const id = path.split("/")[3];
      return response(id.startsWith("advanced-") ? advancedResult(id) : basicResult(id));
    }
    if (path.startsWith("/api/jobs/")) {
      if (jobStatusError) throw jobStatusError;
      const job = jobStatuses.length ? jobStatuses.shift() : { state: "complete", stage: "Complete" };
      if (job?.httpStatus) return response(job.body || {}, job.httpStatus);
      return response(job);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  globalThis.fetch = fetchMock;
  return fetchMock;
}

describe("basic inspection and per-job payment", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("VITE_WALLET_AUTH_BASE_URL", AUTH_BASE);
    initMock.mockReset();
    document.open();
    document.write(html);
    document.close();
    localStorage.clear();
    window.scrollTo = vi.fn();
    HTMLElement.prototype.focus = vi.fn();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("keeps the neon continue action outside the file-picker target", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    mockApi();
    await import("./app.js");
    await flush();
    const picker = vi.spyOn(document.getElementById("file-input"), "click");
    const dropzone = document.getElementById("dropzone");
    const action = document.getElementById("analyze-btn");
    expect(dropzone.contains(action)).toBe(false);
    expect(action.closest(".upload-shell")).not.toBeNull();
    document.querySelector(".upload-orb").click();
    expect(picker).toHaveBeenCalledTimes(1);
    dropzone.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(picker).toHaveBeenCalledTimes(2);
    picker.mockClear();
    chooseVideo();
    await completeBasic();
    expect(picker).not.toHaveBeenCalled();
    expect(inspectCalls()).toHaveLength(1);
    expect(provider.listAccounts).not.toHaveBeenCalled();
    expect(provider.sign).not.toHaveBeenCalled();
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("keeps readable readiness labels on the circular initialization action", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue(walletProvider());
    mockApi({ healthResponses: [503, 200] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();
    const action = document.getElementById("analyze-btn");
    expect(action.disabled).toBe(true);
    expect(action.classList.contains("initializing")).toBe(true);
    expect(action.querySelector("span").textContent).toBe("Initializing");
    expect(action.querySelector("small").textContent).toBe("Analysis service waking up");
    expect(action.querySelector(".initialization-dots").getAttribute("aria-hidden")).toBe("true");
    await vi.advanceTimersByTimeAsync(2000);
    expect(action.disabled).toBe(false);
    expect(action.classList.contains("initializing")).toBe(false);
    expect(action.querySelector("span").textContent).toBe("Inspect video");
    expect(inspectCalls()).toHaveLength(0);
  });

  it("starts with Connect and never requests accounts or balances on load, focus, or online", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-popover").classList.contains("hidden")).toBe(true);
    expect(provider.listAccounts).not.toHaveBeenCalled();
    expect(provider.sign).not.toHaveBeenCalled();
    expect(globalThis.fetch.mock.calls.filter(([url]) => String(url).startsWith(AUTH_BASE))).toHaveLength(0);
    expect(fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)).toHaveLength(0);
  });

  it("uses the backend-derived signer rather than accounts[0], separately from the approved-address sum", async () => {
    const provider = walletProvider({ listAccounts: vi.fn().mockResolvedValue([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]) });
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ authSigner: SECOND_WALLET_ADDRESS, balancesByAddress: { [WALLET_ADDRESS]: 0, [SECOND_WALLET_ADDRESS]: 11005000000 } });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ 110,050.00 NIM · NQ08…1111");
    expect(document.getElementById("wallet-address").textContent).toBe(SECOND_WALLET_ADDRESS);
    expect(provider.sign).toHaveBeenCalledExactlyOnceWith(AUTH_MESSAGE);
    const authCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith(AUTH_BASE));
    expect(authCalls.map(([url]) => url)).toEqual([AUTH_BASE + "/api/health", AUTH_BASE + "/api/wallet-auth/challenge", AUTH_BASE + "/api/wallet-auth/verify"]);
    expect(JSON.parse(authCalls[1][1].body)).toEqual({ accounts: [WALLET_ADDRESS, SECOND_WALLET_ADDRESS] });
    expect(JSON.parse(authCalls[2][1].body)).toEqual({ challenge_id: AUTH_ID, public_key: "ab".repeat(32), signature: "cd".repeat(64) });
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("does not mark the wallet verified merely because native signing succeeded", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    let finishVerification;
    globalThis.fetch = vi.fn((url, options) => String(url).endsWith("/wallet-auth/verify")
      ? new Promise((resolve) => { finishVerification = resolve; }) : fetchMock(url, options));
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(provider.sign).toHaveBeenCalledTimes(1);
    expect(document.getElementById("wallet-connect").textContent).toBe("Verifying…");
    expect(document.getElementById("wallet-address").textContent).toBe("");
    finishVerification(response({ verified: true, signer_address: WALLET_ADDRESS, approved_address_count: 1, scope: "current_page_signer" }));
    await flush();
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
  });

  it.each(["invalid_signature", "expired_challenge", "used_challenge", "missing_challenge", "signer_not_approved", new TypeError("Network unavailable")])("fails closed for backend verification error %s", async (authError) => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ authError });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-address").textContent).toBe("");
    expect(document.getElementById("wallet-heading").textContent).not.toContain("Verified");
    expect(fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)).toHaveLength(0);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/wallet-auth/verify"))).toHaveLength(1);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("handles rejected native signing without verification, balances, or payment requests", async () => {
    const provider = walletProvider({ sign: vi.fn().mockResolvedValue({ error: { type: "PermissionDeniedError", message: "Denied" } }) });
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-message").textContent).toContain("Signature approval was declined");
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/wallet-auth/verify"))).toBe(false);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("disconnect discards an in-flight verification response and never creates a server session", async () => {
    initMock.mockResolvedValue(walletProvider());
    const fetchMock = mockApi();
    let finishVerification;
    globalThis.fetch = vi.fn((url, options) => String(url).endsWith("/wallet-auth/verify")
      ? new Promise((resolve) => { finishVerification = resolve; }) : fetchMock(url, options));
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    document.getElementById("wallet-disconnect").click();
    finishVerification(response({ verified: true, signer_address: WALLET_ADDRESS, approved_address_count: 1, scope: "current_page_signer" }));
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-address").textContent).toBe("");
    expect(JSON.stringify(localStorage)).not.toContain("verified");
  });

  it("wakes only the TEST auth service, then requests exactly one challenge and signature", async () => {
    vi.useFakeTimers();
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ authHealthResponses: [503, 503, 200] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(1000);
    document.getElementById("wallet-connect").click();
    await vi.advanceTimersByTimeAsync(6000);
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(fetchMock.mock.calls.filter(([url]) => url === AUTH_BASE + "/api/health")).toHaveLength(3);
    expect(fetchMock.mock.calls.filter(([url]) => url === AUTH_BASE + "/api/wallet-auth/challenge")).toHaveLength(1);
    expect(provider.sign).toHaveBeenCalledTimes(1);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("connects once on an intentional click and displays Testnet balance, short address, and separate consensus", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    const button = document.getElementById("wallet-connect");
    button.click();
    expect(button.textContent).toBe("Connecting…");
    expect(button.disabled).toBe(true);
    button.click();
    await flush();
    expect(button.textContent).toBe("✓ 125.42 NIM · NQ07…0000");
    expect(button.disabled).toBe(false);
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(document.getElementById("wallet-address").textContent).toBe(WALLET_ADDRESS);
    expect(document.getElementById("wallet-balance").textContent).toBe("125.42 NIM");
    expect(document.getElementById("wallet-account-count").classList.contains("hidden")).toBe(false);
    expect(document.querySelector(".wallet-network").textContent).toBe("TESTNET");
    expect(document.getElementById("wallet-consensus").textContent).toBe("Connected");
    expect(provider.listAccounts).toHaveBeenCalledTimes(1);
    expect(provider.sign).toHaveBeenCalledExactlyOnceWith(AUTH_MESSAGE);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
    expect(initMock).toHaveBeenCalledTimes(1);
    const rpcCalls = fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC);
    expect(rpcCalls.map(([, options]) => JSON.parse(options.body).method)).toEqual(["getLatestBlock", "getAccountByAddress"]);
    expect(JSON.parse(rpcCalls[1][1].body).params).toEqual([WALLET_ADDRESS]);
    expect(rpcCalls[1][1].credentials).toBe("omit");
    expect(fetchMock.mock.calls.filter(([url]) => url !== WALLET_RPC && !String(url).startsWith(AUTH_BASE)).some(([, options]) => String(options?.body).includes(WALLET_ADDRESS))).toBe(false);
    expect(JSON.stringify(localStorage)).not.toContain(WALLET_ADDRESS);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/quotes" || url === "/api/analyze")).toHaveLength(0);
    button.click();
    expect(document.getElementById("wallet-popover").classList.contains("hidden")).toBe(true);
    button.click();
    expect(document.getElementById("wallet-popover").classList.contains("hidden")).toBe(false);
    expect(provider.listAccounts).toHaveBeenCalledTimes(1);
  });

  it("shows the sum across approved addresses while preserving the first selected address and payment source choice", async () => {
    const provider = walletProvider({ listAccounts: vi.fn().mockResolvedValue([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]) });
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ balancesByAddress: { [WALLET_ADDRESS]: 0, [SECOND_WALLET_ADDRESS]: 11005000000 } });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ 110,050.00 NIM · NQ07…0000");
    expect(document.getElementById("wallet-address").textContent).toBe(WALLET_ADDRESS);
    expect(document.getElementById("wallet-balance").textContent).toBe("110,050.00 NIM");
    expect(document.getElementById("wallet-account-count").textContent).toBe("2");
    expect(document.getElementById("wallet-account-count").classList.contains("hidden")).toBe(false);
    expect(document.getElementById("wallet-account-count-label").textContent).toBe("Approved addresses");
    expect(document.getElementById("wallet-details").textContent).toContain("Testnet on-chain balance");
    expect(document.querySelector(".wallet-network").textContent).toBe("TESTNET");
    expect(document.getElementById("wallet-diagnostic-toggle")).toBeNull();
    const queried = fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)
      .map(([, options]) => JSON.parse(options.body)).filter((request) => request.method === "getAccountByAddress");
    expect(queried.map((request) => request.params[0])).toEqual([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]);
    expect(fetchMock.mock.calls.filter(([url]) => url !== WALLET_RPC && !String(url).startsWith(AUTH_BASE)).some(([, options]) =>
      String(options?.body).includes(SECOND_WALLET_ADDRESS))).toBe(false);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledExactlyOnceWith({
      recipient: "NQ43 TEST", value: 1000000, data: "prometheus:quote-1",
    });
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/analyze")).toHaveLength(1);
  });

  it("shows a partial-balance warning rather than the selected address's balance when another lookup fails", async () => {
    initMock.mockResolvedValue(walletProvider({ listAccounts: vi.fn().mockResolvedValue([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]) }));
    mockApi({ balancesByAddress: { [WALLET_ADDRESS]: 12542000, [SECOND_WALLET_ADDRESS]: new Error("RPC unavailable") } });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ Partial balance unavailable · NQ07…0000");
    expect(document.getElementById("wallet-balance").textContent).toBe("Partial balance unavailable");
    expect(document.getElementById("wallet-account-count").textContent).toBe("2");
    expect(document.getElementById("wallet-address").textContent).toBe(WALLET_ADDRESS);
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
  });

  it("counts and queries a duplicated approved address only once", async () => {
    initMock.mockResolvedValue(walletProvider({ listAccounts: vi.fn().mockResolvedValue([
      WALLET_ADDRESS, WALLET_ADDRESS, SECOND_WALLET_ADDRESS,
    ]) }));
    const fetchMock = mockApi({ balancesByAddress: { [WALLET_ADDRESS]: 100000, [SECOND_WALLET_ADDRESS]: 200000 } });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ 3.00 NIM · NQ07…0000");
    expect(document.getElementById("wallet-account-count").textContent).toBe("2");
    const queried = fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)
      .map(([, options]) => JSON.parse(options.body)).filter((request) => request.method === "getAccountByAddress");
    expect(queried.map((request) => request.params[0])).toEqual([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]);
  });

  it("keeps the wallet connected with balance unavailable when every approved address lookup fails", async () => {
    const provider = walletProvider({ listAccounts: vi.fn().mockResolvedValue([WALLET_ADDRESS, SECOND_WALLET_ADDRESS]) });
    initMock.mockResolvedValue(provider);
    mockApi({ balanceError: new TypeError("RPC unavailable") });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ Balance unavailable · NQ07…0000");
    expect(document.getElementById("wallet-balance").textContent).toBe("Balance unavailable");
    expect(document.getElementById("wallet-account-count").textContent).toBe("2");
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it.each([0, 1])("renders an authoritative balance of %s luna without inventing funds", async (balanceLuna) => {
    initMock.mockResolvedValue(walletProvider());
    mockApi({ balanceLuna });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-balance").textContent).toBe(balanceLuna === 0 ? "0.00 NIM" : "0.00001 NIM");
  });

  it.each(["false", "rejected", "timeout"])("keeps the wallet and Testnet balance when consensus is %s", async (state) => {
    if (state === "timeout") vi.useFakeTimers();
    const consensus = state === "false" ? vi.fn().mockResolvedValue(false)
      : state === "rejected" ? vi.fn().mockRejectedValue(new Error("Unavailable")) : vi.fn(() => new Promise(() => {}));
    initMock.mockResolvedValue(walletProvider({ isConsensusEstablished: consensus }));
    mockApi();
    await import("./app.js");
    if (state === "timeout") await vi.advanceTimersByTimeAsync(0); else await flush();
    document.getElementById("wallet-connect").click();
    if (state === "timeout") await vi.advanceTimersByTimeAsync(10000); else await flush();
    expect(document.getElementById("wallet-consensus").textContent).toBe("Not established");
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ 125.42 NIM · NQ07…0000");
  });

  it("keeps the wallet connected and the 10 NIM payment flow usable when balance lookup fails", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ balanceError: new TypeError("RPC unavailable") });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ Balance unavailable · NQ07…0000");
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(document.getElementById("wallet-consensus").textContent).toBe("Connected");
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledExactlyOnceWith({
      recipient: "NQ43 TEST", value: 1000000, data: "prometheus:quote-1",
    });
    expect(document.getElementById("res-summary").textContent).toBe("Advanced result");
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/analyze")).toHaveLength(1);
    expect(initMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a Mainnet balance source without querying the account or disconnecting the wallet", async () => {
    initMock.mockResolvedValue(walletProvider());
    const fetchMock = mockApi({ rpcNetwork: "MainAlbatross" });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-heading").textContent).toBe("Verified signer ✓");
    expect(document.getElementById("wallet-balance").textContent).toBe("Balance unavailable");
    expect(fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)).toHaveLength(1);
  });

  it.each([undefined, -1, "12542000", 1.5])("does not invent a balance when the RPC value is invalid: %s", async (balanceLuna) => {
    initMock.mockResolvedValue(walletProvider());
    // undefined represents a missing balance, not the mock's default.
    const fetchMock = mockApi({ balanceLuna: balanceLuna === undefined ? null : balanceLuna });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-balance").textContent).toBe("Balance unavailable");
    expect(document.getElementById("wallet-address").textContent).toBe(WALLET_ADDRESS);
  });

  it("shows useful guidance outside Nimiq Pay without a fake connected state", async () => {
    initMock.mockRejectedValue(new Error("Nimiq provider not found"));
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-message").textContent).toBe("Open in Nimiq Pay to connect.");
    expect(document.getElementById("wallet-details").classList.contains("hidden")).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)).toHaveLength(0);
  });

  it("handles rejected account access without balance reads or transactions", async () => {
    const provider = walletProvider({ listAccounts: vi.fn().mockResolvedValue({ error: { type: "PermissionDeniedError", message: "Denied" } }) });
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-message").textContent).toContain("Account access was declined");
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(fetchMock.mock.calls.filter(([url]) => url === WALLET_RPC)).toHaveLength(0);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("handles no available account", async () => {
    initMock.mockResolvedValue(walletProvider({ listAccounts: vi.fn().mockResolvedValue([]) }));
    mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-message").textContent).toContain("No Nimiq account is available");
  });

  it("shares in-progress SDK initialization between Connect and payment readiness", async () => {
    let finishInit;
    initMock.mockImplementation(() => new Promise((resolve) => { finishInit = resolve; }));
    const provider = walletProvider();
    mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    expect(initMock).toHaveBeenCalledTimes(1);
    expect(provider.listAccounts).not.toHaveBeenCalled();
    finishInit(provider);
    await flush();
    expect(provider.listAccounts).toHaveBeenCalledTimes(1);
    expect(document.getElementById("wallet-connect").textContent).toBe("✓ 125.42 NIM · NQ07…0000");
  });

  it("locally disconnects, clears the address, and leaves existing payment recovery usable", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    mockApi({ verifyError: new TypeError("Verification unavailable") });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    const savedPayment = JSON.parse(localStorage.getItem(RECOVERY_KEY)).payment;
    expect(savedPayment.txHash).toBe(HASH_A);
    document.getElementById("wallet-connect").click();
    document.getElementById("wallet-disconnect").click();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-address").textContent).toBe("");
    expect(document.getElementById("wallet-popover").classList.contains("hidden")).toBe(true);
    expect(provider.disconnect).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).payment).toEqual(savedPayment);
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledTimes(1);
  });

  it("does not restore the address when a balance read completes after local disconnect", async () => {
    const provider = walletProvider();
    initMock.mockResolvedValue(provider);
    const baseFetch = mockApi();
    let finishBalance;
    globalThis.fetch = vi.fn((url, options) => {
      if (url === WALLET_RPC && JSON.parse(options.body).method === "getAccountByAddress") {
        return new Promise((resolve) => { finishBalance = resolve; });
      }
      return baseFetch(url, options);
    });
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    document.getElementById("wallet-disconnect").click();
    finishBalance(response({ result: { data: { balance: 12542000 } } }));
    await flush();
    expect(document.getElementById("wallet-connect").textContent).toBe("Connect");
    expect(document.getElementById("wallet-address").textContent).toBe("");
  });

  it("closes the wallet popover with Escape and an outside click", async () => {
    initMock.mockResolvedValue(walletProvider());
    mockApi();
    await import("./app.js");
    await flush();
    document.getElementById("wallet-connect").click();
    await flush();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(document.getElementById("wallet-connect").getAttribute("aria-expanded")).toBe("false");
    document.getElementById("wallet-connect").click();
    document.body.click();
    expect(document.getElementById("wallet-popover").classList.contains("hidden")).toBe(true);
  });

  it("opens the file chooser and starts basic analysis on the first click only", async () => {
    const provider = { isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() };
    initMock.mockResolvedValue(provider);
    mockApi();
    await import("./app.js");
    await flush();
    const input = document.getElementById("file-input");
    const click = vi.spyOn(input, "click");
    document.getElementById("dropzone").click();
    expect(click).toHaveBeenCalledTimes(1);
    chooseVideo();
    const analyze = document.getElementById("analyze-btn");
    analyze.click();
    analyze.click();
    await flush();
    expect(inspectCalls()).toHaveLength(1);
    expect(provider.sendBasicTransactionWithData).not.toHaveBeenCalled();
  });

  it("keeps the upload dashboard on a fresh first launch", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();

    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
  });

  it("keeps the upload dashboard when reopening with no saved job", async () => {
    localStorage.setItem(RECOVERY_KEY, JSON.stringify({}));
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();

    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    expect(localStorage.getItem(RECOVERY_KEY)).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
  });

  it.each(["expired", "complete", "failed", "cancelled", "queued", "running"])(
    "opens the dashboard without fetching or restoring a saved %s job", async (state) => {
      localStorage.setItem(RECOVERY_KEY, JSON.stringify({ jobId: `${state}-1`, sourceJobId: `${state}-1` }));
      window.history.replaceState(null, "", "#screen-processing");
      initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
      const fetchMock = mockApi();
      await import("./app.js");
      await flush();

      expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
      expect(document.getElementById("screen-processing").classList.contains("hidden")).toBe(true);
      expect(window.location.hash).toBe("");
      expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
      expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).jobId).toBe(`${state}-1`);
    }
  );

  it("keeps saved recovery data when the backend is temporarily unreachable at startup", async () => {
    localStorage.setItem(RECOVERY_KEY, JSON.stringify({ jobId: "recoverable-1", sourceJobId: "recoverable-1" }));
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    mockApi({ jobStatusError: new TypeError("offline") });
    await import("./app.js");
    await flush();

    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    expect(document.getElementById("screen-processing").classList.contains("hidden")).toBe(true);
    expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).jobId).toBe("recoverable-1");
  });

  it("waits for a sleeping backend to pass its health check before initialization", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ healthResponses: [503, 200] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();

    expect(document.getElementById("payment-status").textContent).toContain("Starting Prometheus");
    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(document.getElementById("analyze-btn").textContent).toContain("Initializing");
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(2);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(1);
    expect(document.getElementById("payment-status").textContent).toContain("Nimiq Pay connected");
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(document.getElementById("analyze-btn").textContent).toContain("Inspect video");
    vi.useRealTimers();
  });

  it("wakes automatically after more than 90 seconds without duplicate requests", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ healthResponses: Array(47).fill(503) });
    await import("./app.js");
    chooseVideo();
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(92000);
    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(document.getElementById("payment-status").textContent).not.toContain("Connection disrupted");
    await vi.advanceTimersByTimeAsync(2000);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(48);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/quotes" || url === "/api/analyze")).toHaveLength(0);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("continues immediately when the backend is already awake", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(1);
    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    chooseVideo();
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
  });

  it("keeps Local Inspection disabled until upload readiness succeeds", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ readyResponses: [503, 200] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();

    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(2000);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(2);
    expect(uploadPingCalls(fetchMock)).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(1);
  });

  it("enables Local Inspection only after a successful upload-path canary", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();

    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(uploadPingCalls(fetchMock)).toHaveLength(1);
    const ping = uploadPingCalls(fetchMock)[0];
    expect(ping[0]).toMatch(/^\/api\/upload-ping\?upload_ping_id=[A-Za-z0-9-]+$/);
    expect(ping[1].method).toBe("POST");
    expect(ping[1].body).toBeInstanceOf(FormData);
    expect(ping[1].headers).toBeUndefined();
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("keeps Inspect disabled while the canary fails then recovers without retrying a video upload", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ pingResponses: [503, 204] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();

    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(document.getElementById("payment-status").textContent).toContain("Upload service is reconnecting");
    expect(inspectCalls(fetchMock)).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(2000);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(document.getElementById("analyze-btn").textContent).toContain("Inspect video");
    expect(uploadPingCalls(fetchMock)).toHaveLength(2);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("offers Retry initialization after the canary keeps failing and does not require a refresh", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ pingResponses: Array(50).fill(503) });
    await import("./app.js");
    chooseVideo();
    await vi.advanceTimersByTimeAsync(100000);

    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(document.getElementById("payment-status").textContent).toContain("Connection disrupted");
    expect(document.getElementById("retry-init-btn").classList.contains("hidden")).toBe(false);
    expect(inspectCalls(fetchMock)).toHaveLength(0);

    document.getElementById("retry-init-btn").click();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(document.getElementById("retry-init-btn").classList.contains("hidden")).toBe(true);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("treats a missing upload-ping route as compatible with an older backend", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ pingResponses: [404] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();

    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(uploadPingCalls(fetchMock)).toHaveLength(1);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("does not create analysis jobs from canary requests", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();

    expect(uploadPingCalls(fetchMock).length).toBeGreaterThan(0);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/analyze")).toHaveLength(0);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
  });

  it("shows the network state only after the backend stays unavailable for 100 seconds", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ healthResponses: Array(60).fill(503) });
    await import("./app.js");
    chooseVideo();
    await vi.advanceTimersByTimeAsync(98000);

    expect(document.getElementById("payment-status").textContent).toContain("Starting Prometheus");
    expect(document.getElementById("analyze-btn").disabled).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(2000);
    expect(document.getElementById("payment-status").textContent).toContain("Connection disrupted");
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(50);
    expect(fetchMock.mock.calls.filter(([url]) => isInspectUrl(url) || url === "/api/analyze")).toHaveLength(0);
  });

  it("retries readiness without sending analysis requests", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ healthResponses: [503, 503, 200] });
    await import("./app.js");
    chooseVideo();
    document.getElementById("analyze-btn").click();
    await vi.advanceTimersByTimeAsync(4000);

    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/health")).toHaveLength(3);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/ready")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/config")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => isInspectUrl(url) || url === "/api/analyze")).toHaveLength(0);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
  });

  it("does not start a multipart upload when readiness is lost before the click", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi({ readyResponses: [200, 503, 200] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();
    document.getElementById("analyze-btn").click();
    await vi.advanceTimersByTimeAsync(0);

    expect(inspectCalls(fetchMock)).toHaveLength(0);
    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);

    await vi.advanceTimersByTimeAsync(2000);
    expect(document.getElementById("analyze-btn").disabled).toBe(false);
    expect(inspectCalls(fetchMock)).toHaveLength(0);
  });

  it("creates one correlated upload request for one intentional click", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    const analyze = document.getElementById("analyze-btn");
    analyze.click();
    analyze.click();
    await flush();

    expect(inspectCalls()).toHaveLength(1);
    expect(inspectCalls()[0][0]).toMatch(/^\/api\/inspect\?upload_attempt_id=[A-Za-z0-9-]+$/);
    expect(inspectCalls()[0][1].method).toBe("POST");
    expect(inspectCalls()[0][1].body).toBeInstanceOf(FormData);
    expect(inspectCalls()[0][1].headers).toBeUndefined();
  });

  it("labels a real multipart transport failure without claiming the user's network is broken", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    mockApi({ inspectError: new TypeError("Failed to fetch") });
    await import("./app.js");
    await flush();
    chooseVideo();
    document.getElementById("analyze-btn").click();
    await flush();

    const message = document.getElementById("job-error").textContent;
    expect(message).toContain("Upload connection was interrupted");
    expect(message).toContain("before Prometheus returned a response");
    expect(message).toContain("No upload retry was made");
    expect(message).toContain("Reference:");
    expect(message).not.toContain("Check your connection");
    expect(inspectCalls()).toHaveLength(1);
  });

  it("labels an aborted inspect upload as a timeout without retrying", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    mockApi({ inspectError: Object.assign(new Error("The operation was aborted."), { name: "AbortError" }) });
    await import("./app.js");
    await flush();
    chooseVideo();
    document.getElementById("analyze-btn").click();
    await flush();

    const message = document.getElementById("job-error").textContent;
    expect(message).toContain("Upload timed out");
    expect(message).toContain("No upload retry was made");
    expect(message).toContain("Reference:");
    expect(inspectCalls()).toHaveLength(1);
  });

  it("blocks the upload when the file cannot be read, without contacting the backend", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    const file = document.getElementById("file-input").files[0];
    vi.spyOn(file, "slice").mockReturnValue({
      arrayBuffer: () => Promise.reject(new DOMException("The file could not be read", "NotReadableError")),
    });
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    document.getElementById("analyze-btn").click();
    await flush();

    expect(inspectCalls(fetchMock)).toHaveLength(0);
    const message = document.getElementById("job-error").textContent;
    expect(message).toContain("couldn't be accessed through the selected source");
    expect(message).toContain("using Files or Browse instead of Gallery");
    expect(message).toContain("(NotReadableError)");
    expect(message).toContain("No upload was started");
    expect(message).toContain("Reference:");
    const logged = info.mock.calls.map(([line]) => String(line)).join("\n");
    expect(logged).toContain("precheck=unreadable");
    expect(logged).toContain("NotReadableError");
    expect(logged).toContain("The file could not be read");
    expect(logged).not.toContain("clip.mp4");
  });

  it("shows the Files guidance for a NotFoundError read failure", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    const file = document.getElementById("file-input").files[0];
    vi.spyOn(file, "slice").mockReturnValue({
      arrayBuffer: () => Promise.reject(new DOMException("gone", "NotFoundError")),
    });
    document.getElementById("analyze-btn").click();
    await flush();

    expect(inspectCalls(fetchMock)).toHaveLength(0);
    const message = document.getElementById("job-error").textContent;
    expect(message).toContain("using Files or Browse instead of Gallery");
    expect(message).toContain("(NotFoundError)");
    expect(message).toContain("Reference:");
  });

  it("shows UnknownError when the read failure has no error name", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    const file = document.getElementById("file-input").files[0];
    vi.spyOn(file, "slice").mockReturnValue({ arrayBuffer: () => Promise.reject("boom") });
    document.getElementById("analyze-btn").click();
    await flush();

    expect(inspectCalls(fetchMock)).toHaveLength(0);
    const message = document.getElementById("job-error").textContent;
    expect(message).toContain("could not be read from this device");
    expect(message).toContain("(UnknownError)");
    expect(message).not.toContain("instead of Gallery");
  });

  it("proceeds with the upload when the file pre-check passes", async () => {
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    const file = document.getElementById("file-input").files[0];
    vi.spyOn(file, "slice").mockReturnValue({ arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    document.getElementById("analyze-btn").click();
    await flush();

    expect(inspectCalls(fetchMock)).toHaveLength(1);
    expect(info).toHaveBeenCalledWith(expect.stringContaining("precheck=readable"));
  });

  it("shows the server-provided queue position and clears it when the worker starts", async () => {
    vi.useFakeTimers();
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn(), sendBasicTransactionWithData: vi.fn() });
    mockApi({ jobStatuses: [
      { state: "processing", stage: "Queued", queue_position: 2 },
      { state: "processing", stage: "Queued", queue_position: 1 },
      { state: "processing", stage: "Queued", queue_position: 0 },
      { state: "processing", stage: "Starting analysis" },
      { state: "complete", stage: "Complete" },
    ] });
    await import("./app.js");
    await vi.advanceTimersByTimeAsync(0);
    chooseVideo();
    document.getElementById("analyze-btn").click();
    await vi.advanceTimersByTimeAsync(0);

    expect(document.getElementById("phase-text").textContent).toBe("Queued");
    expect(document.getElementById("phase-detail").textContent).toBe(
      "2 jobs ahead of you. Your analysis will start automatically."
    );

    await vi.advanceTimersByTimeAsync(1500);
    expect(document.getElementById("phase-detail").textContent).toBe(
      "1 job ahead of you. Your analysis will start automatically."
    );

    await vi.advanceTimersByTimeAsync(1500);
    expect(document.getElementById("phase-detail").textContent).toBe(
      "You're next in the queue. Your analysis will start automatically."
    );

    await vi.advanceTimersByTimeAsync(1500);
    expect(document.getElementById("phase-text").textContent).toBe("Analyzing visual language");

    await vi.advanceTimersByTimeAsync(1500);
  });

  it("honors the first upgrade click while Nimiq is still initializing", async () => {
    let finishInitialization;
    initMock.mockImplementation(() => new Promise((resolve) => { finishInitialization = resolve; }));
    const provider = { isConsensusEstablished: vi.fn().mockResolvedValue(true),
      sendBasicTransactionWithData: vi.fn().mockResolvedValue(HASH_A) };
    mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    expect(document.getElementById("upgrade-btn").disabled).toBe(true);
    finishInitialization(provider);
    await flush();
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledTimes(1);
    expect(document.getElementById("res-summary").textContent).toBe("Advanced result");
  });

  it("runs basic, requests one payment, verifies its hash, shows advanced result, and remixes on one action", async () => {
    const provider = { isConsensusEstablished: vi.fn().mockResolvedValue(true),
      sendBasicTransactionWithData: vi.fn().mockResolvedValue(HASH_A) };
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    await completeBasic();
    const upgrade = document.getElementById("upgrade-btn");
    upgrade.click();
    upgrade.click();
    await flush();
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledTimes(1);
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledWith({
      recipient: "NQ43 TEST", value: 1_000_000, data: "prometheus:quote-1",
    });
    const quoteRequest = fetchMock.mock.calls.find(([url]) => url === "/api/payments/quotes");
    expect(JSON.parse(quoteRequest[1].body).source_job_id).toBe("basic-1");
    const verifyRequest = fetchMock.mock.calls.find(([url]) => String(url).includes("/verify"));
    expect(JSON.parse(verifyRequest[1].body).tx_hash).toBe(HASH_A);
    const analyzeRequest = fetchMock.mock.calls.find(([url]) => url === "/api/analyze");
    expect(analyzeRequest[1].body.get("source_job_id")).toBe("basic-1");
    expect(document.getElementById("res-summary").textContent).toBe("Advanced result");
    expect(document.getElementById("res-analyzer").textContent).toBe("Prometheus Analysis Engine");
    expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).payment).toBeNull();
    document.getElementById("remix-btn").click();
    document.querySelector("#remix-fields textarea").value = "dragon";
    document.getElementById("remix-run").click();
    await flush();
    expect(document.getElementById("remix-prompt").textContent).toBe("Remixed prompt");
  });

  it("requires a new quote and transaction for the next video", async () => {
    const provider = { isConsensusEstablished: vi.fn().mockResolvedValue(true),
      sendBasicTransactionWithData: vi.fn().mockResolvedValueOnce(HASH_A).mockResolvedValueOnce(HASH_B) };
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    await completeBasic("basic-1");
    document.getElementById("upgrade-btn").click();
    await flush();
    document.getElementById("again-btn").click();
    chooseVideo("second.mp4");
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    const quoteCalls = fetchMock.mock.calls.filter(([url]) => url === "/api/payments/quotes");
    expect(quoteCalls).toHaveLength(2);
    expect(JSON.parse(quoteCalls[1][1].body).source_job_id).toBe("basic-2");
    expect(provider.sendBasicTransactionWithData).toHaveBeenCalledTimes(2);
  });

  it("clears a cancelled or failed wallet request without starting advanced analysis", async () => {
    const provider = { isConsensusEstablished: vi.fn().mockResolvedValue(true),
      sendBasicTransactionWithData: vi.fn().mockRejectedValue(Object.assign(new Error("User rejected"), { code: 4001 })) };
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(document.getElementById("upgrade-error").textContent).toContain("Payment cancelled");
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/analyze")).toBe(false);
    expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).payment).toBeNull();
    provider.sendBasicTransactionWithData.mockResolvedValue({ error: { type: "InvalidTransactionError", message: "Insufficient balance" } });
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(document.getElementById("upgrade-error").textContent).toContain("Insufficient balance");
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/analyze")).toBe(false);
  });

  it("does not navigate to a saved completed job or submit its payment after refresh", async () => {
    localStorage.setItem(RECOVERY_KEY, JSON.stringify({
      jobId: "basic-1", sourceJobId: "basic-1",
      payment: { sourceJobId: "basic-1", quoteId: "quote-saved", token: "saved-token", txHash: HASH_A },
    }));
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn().mockResolvedValue(true), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    expect(document.getElementById("screen-upload").classList.contains("hidden")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/analyze")).toHaveLength(0);
  });

  it("does not use a saved payment tied to a different job on launch", async () => {
    localStorage.setItem(RECOVERY_KEY, JSON.stringify({
      jobId: "basic-2", sourceJobId: "basic-1",
      payment: { sourceJobId: "basic-1", quoteId: "old-quote", token: "old-token", txHash: HASH_A },
    }));
    initMock.mockResolvedValue({ isConsensusEstablished: vi.fn().mockResolvedValue(true), sendBasicTransactionWithData: vi.fn() });
    const fetchMock = mockApi();
    await import("./app.js");
    await flush();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("old-quote/verify"))).toBe(false);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/payments/quotes")).toHaveLength(0);
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/jobs/"))).toBe(false);
  });

  it("holds the same job payment through a verification interruption", async () => {
    const provider = { isConsensusEstablished: vi.fn().mockResolvedValue(true),
      sendBasicTransactionWithData: vi.fn().mockResolvedValue(HASH_A) };
    initMock.mockResolvedValue(provider);
    const fetchMock = mockApi({ verifyError: new TypeError("offline") });
    await import("./app.js");
    await flush();
    chooseVideo();
    await completeBasic();
    document.getElementById("upgrade-btn").click();
    await flush();
    expect(document.getElementById("upgrade-error").textContent).toContain("do not pay again");
    expect(JSON.parse(localStorage.getItem(RECOVERY_KEY)).payment.sourceJobId).toBe("basic-1");
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/analyze")).toBe(false);
  });
});
