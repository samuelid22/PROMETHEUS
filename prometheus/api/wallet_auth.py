"""TEST wallet display proof. No payment authorization or persistent login session."""
from __future__ import annotations

import hashlib
import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVXY"
MESSAGE_PREFIX = b"\x16Nimiq Signed Message:\n"
MAX_BODY_BYTES = 16_384
# Low-order compressed Edwards points (both sign bits), from libsodium ref10.
LOW_ORDER_Y = {0, 1, 2**255 - 20,
    2707385501144840649318225287225658788936804267575313519463743609750303402022,
    55188659117513257062467267217118295137698188065244968500265048394206261417927}


class WalletAuthError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(code)


def _mod97(value: str) -> int:
    remainder = 0
    for character in value:
        digits = character if character.isdigit() else str(ord(character) - ord("A") + 10)
        for digit in digits:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def canonical_address(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise WalletAuthError("invalid_address")
    compact = value.replace(" ", "").upper()
    if not re.fullmatch(r"NQ[0-9]{2}[" + ALPHABET + r"]{32}", compact):
        raise WalletAuthError("invalid_address")
    if _mod97(compact[4:] + compact[:4]) != 1:
        raise WalletAuthError("invalid_address")
    return compact


def format_address(compact: str) -> str:
    return " ".join(compact[index:index + 4] for index in range(0, len(compact), 4))


def address_from_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise WalletAuthError("malformed_public_key")
    raw = hashlib.blake2b(public_key, digest_size=32).digest()[:20]
    bits = int.from_bytes(raw, "big")
    encoded = "".join(ALPHABET[(bits >> shift) & 31] for shift in range(155, -1, -5))
    checksum = 98 - _mod97(encoded + "NQ00")
    return "NQ" + f"{checksum:02d}" + encoded


def message_digest(message: str) -> bytes:
    data = message.encode("utf-8")
    return hashlib.sha256(MESSAGE_PREFIX + str(len(data)).encode("ascii") + data).digest()


def _hex_bytes(value: object, length: int, code: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{" + str(length * 2) + r"}", value):
        raise WalletAuthError(code)
    return bytes.fromhex(value)


@dataclass(frozen=True)
class WalletAuthConfig:
    origin: str
    lifetime_seconds: int = 300
    max_challenges: int = 1000
    max_accounts: int = 64
    issuance_per_minute: int = 20
    verification_per_minute: int = 40
    global_per_minute: int = 300

    def __post_init__(self) -> None:
        parsed = urlsplit(self.origin)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or self.origin != f"{parsed.scheme}://{parsed.netloc}"):
            raise ValueError("Wallet authentication needs one explicit HTTP(S) origin.")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Remote wallet authentication origin must use HTTPS.")


class WalletAuthService:
    def __init__(self, config: WalletAuthConfig, database: Path | str,
                 clock: Callable[[], float] = time.time):
        self.config, self.database, self.clock = config, Path(database), clock
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_challenges (
                id TEXT PRIMARY KEY, message TEXT NOT NULL, accounts TEXT NOT NULL,
                expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS wallet_rate_limits (
                bucket TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cleanup(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM wallet_challenges WHERE expires <= ?", (int(self.clock()),))
            db.execute("DELETE FROM wallet_rate_limits WHERE window < ?", (int(self.clock()) // 60,))

    def rate_limit(self, client: str, operation: str) -> None:
        window = int(self.clock()) // 60
        limit = self.config.issuance_per_minute if operation == "challenge" else self.config.verification_per_minute
        # No forwarded header is trusted; a shared proxy bucket fails conservatively.
        client_hash = hashlib.sha256(client.encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM wallet_rate_limits WHERE window < ?", (window,))
            for bucket, maximum in [("global", self.config.global_per_minute),
                                    (operation + ":" + client_hash, limit)]:
                record = db.execute("SELECT count FROM wallet_rate_limits WHERE bucket = ?", (bucket,)).fetchone()
                if record and record["count"] >= maximum:
                    raise WalletAuthError("rate_limited", 429)
                if not record and db.execute("SELECT COUNT(*) FROM wallet_rate_limits").fetchone()[0] >= 2048:
                    raise WalletAuthError("rate_limited", 429)
                db.execute("""INSERT INTO wallet_rate_limits VALUES (?, ?, 1)
                    ON CONFLICT(bucket) DO UPDATE SET count = count + 1""", (bucket, window))

    def issue(self, accounts: object) -> dict:
        if not isinstance(accounts, list) or not 1 <= len(accounts) <= self.config.max_accounts:
            raise WalletAuthError("invalid_accounts")
        approved = sorted(set(canonical_address(address) for address in accounts))
        issued = int(self.clock())
        expires = issued + self.config.lifetime_seconds
        challenge_id = secrets.token_hex(16)
        nonce = secrets.token_hex(32)
        account_hash = hashlib.sha256("\n".join(approved).encode("ascii")).hexdigest()
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
        message = "\n".join([
            "Prometheus wallet verification", "Version: 1", f"Origin: {self.config.origin}",
            "Network context: TESTNET", f"Challenge ID: {challenge_id}", f"Nonce: {nonce}",
            f"Issued at: {stamp(issued)}", f"Expires at: {stamp(expires)}",
            f"Approved addresses SHA-256: {account_hash}",
        ])
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM wallet_challenges WHERE expires <= ?", (issued,))
            if db.execute("SELECT COUNT(*) FROM wallet_challenges").fetchone()[0] >= self.config.max_challenges:
                raise WalletAuthError("challenge_capacity", 429)
            db.execute("INSERT INTO wallet_challenges (id, message, accounts, expires) VALUES (?, ?, ?, ?)",
                       (challenge_id, message, json.dumps(approved), expires))
        return {"id": challenge_id, "message": message, "expires_at": expires}

    def verify(self, challenge_id: object, public_key: object, signature: object) -> dict:
        if not isinstance(challenge_id, str) or not re.fullmatch(r"[0-9a-f]{32}", challenge_id):
            raise WalletAuthError("missing_challenge", 404)
        key = _hex_bytes(public_key, 32, "malformed_public_key")
        sig = _hex_bytes(signature, 64, "malformed_signature")
        # Reject identity/default curve points even if a crypto backend accepts them.
        y = int.from_bytes(key, "little") & (2**255 - 1)
        if y >= 2**255 - 19 or y in LOW_ORDER_Y:
            raise WalletAuthError("invalid_signature", 401)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            challenge = db.execute("SELECT * FROM wallet_challenges WHERE id = ?", (challenge_id,)).fetchone()
            if challenge is None:
                raise WalletAuthError("missing_challenge", 404)
            if challenge["expires"] <= int(self.clock()):
                raise WalletAuthError("expired_challenge", 410)
            if challenge["used"]:
                raise WalletAuthError("used_challenge", 409)
            try:
                Ed25519PublicKey.from_public_bytes(key).verify(sig, message_digest(challenge["message"]))
            except (InvalidSignature, ValueError):
                raise WalletAuthError("invalid_signature", 401) from None
            signer = address_from_public_key(key)
            if signer not in json.loads(challenge["accounts"]):
                raise WalletAuthError("signer_not_approved", 403)
            # Recheck authoritative time after cryptographic work, inside the same transaction.
            if challenge["expires"] <= int(self.clock()):
                raise WalletAuthError("expired_challenge", 410)
            if db.execute("UPDATE wallet_challenges SET used = 1 WHERE id = ? AND used = 0", (challenge_id,)).rowcount != 1:
                raise WalletAuthError("used_challenge", 409)
        return {"verified": True, "signer_address": format_address(signer),
                "approved_address_count": len(json.loads(challenge["accounts"])), "scope": "current_page_signer"}


def create_auth_app(service: WalletAuthService | None = None) -> FastAPI:
    """Separate TEST service entry point; intentionally has no analysis/payment routes."""
    enabled = os.environ.get("PROMETHEUS_WALLET_AUTH_ENABLED", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("PROMETHEUS_WALLET_AUTH_ENABLED must be true or false.")
    if service is None and enabled == "true":
        if os.environ.get("PROMETHEUS_NIMIQ_NETWORK", "testnet") != "testnet":
            raise ValueError("TEST wallet authentication requires testnet configuration.")
        service = WalletAuthService(WalletAuthConfig(os.environ.get("PROMETHEUS_WALLET_AUTH_ORIGIN", "")),
                                    os.environ.get("PROMETHEUS_WALLET_AUTH_DB", "/tmp/prometheus-wallet-auth.db"))
    async def clean_expired():
        while True:
            await asyncio.sleep(30)
            try:
                await run_in_threadpool(service.cleanup)
            except sqlite3.Error:
                pass  # Failed cleanup never authorizes an expired proof.

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(clean_expired()) if service else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Prometheus TEST wallet verification", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    if service:
        app.add_middleware(CORSMiddleware, allow_origins=[service.config.origin], allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "wallet_auth_enabled": service is not None}

    async def payload(request: Request, operation: str, fields: set[str]) -> dict:
        if service is None:
            raise HTTPException(503, detail="wallet_auth_disabled")
        if request.headers.get("origin") != service.config.origin:
            raise HTTPException(403, detail="origin_not_allowed")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise HTTPException(415, detail="json_required")
        try:
            await run_in_threadpool(service.rate_limit, request.client.host if request.client else "unknown", operation)
        except WalletAuthError as error:
            raise HTTPException(error.status, detail=error.code) from None
        except sqlite3.Error:
            raise HTTPException(503, detail="wallet_auth_unavailable") from None
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, detail="request_too_large")
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(400, detail="invalid_request") from None
        if not isinstance(result, dict) or set(result) != fields:
            raise HTTPException(400, detail="invalid_request")
        return result

    @app.post("/api/wallet-auth/challenge")
    async def challenge(request: Request):
        values = await payload(request, "challenge", {"accounts"})
        try:
            return await run_in_threadpool(service.issue, values["accounts"])
        except WalletAuthError as error:
            raise HTTPException(error.status, detail=error.code) from None
        except sqlite3.Error:
            raise HTTPException(503, detail="wallet_auth_unavailable") from None

    @app.post("/api/wallet-auth/verify")
    async def verify(request: Request):
        values = await payload(request, "verify", {"challenge_id", "public_key", "signature"})
        try:
            return await run_in_threadpool(service.verify, values["challenge_id"], values["public_key"], values["signature"])
        except WalletAuthError as error:
            raise HTTPException(error.status, detail=error.code) from None
        except sqlite3.Error:
            raise HTTPException(503, detail="wallet_auth_unavailable") from None

    return app


app = create_auth_app()
