from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class PaymentError(Exception):
    pass


class PaymentPending(PaymentError):
    pass


@dataclass(frozen=True)
class PaymentConfig:
    recipient: str
    amount_luna: int
    rpc_url: str
    network: str = "testnet"
    confirmations: int = 1
    quote_lifetime_seconds: int = 30 * 60

    def __post_init__(self) -> None:
        if self.network not in {"testnet", "mainnet"}:
            raise ValueError("Nimiq network must be 'testnet' or 'mainnet'.")
        if self.amount_luna <= 0:
            raise ValueError("Nimiq payment amount must be positive.")
        if self.confirmations < 0:
            raise ValueError("Nimiq confirmations must not be negative.")
        if not self.recipient.strip():
            raise ValueError("Nimiq payment recipient is required.")


RpcCall = Callable[[str, list[Any]], Any]


class PaymentService:
    def __init__(
        self,
        config: PaymentConfig,
        database: Path | str,
        rpc_call: RpcCall | None = None,
    ):
        self.config = config
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._rpc_call = rpc_call or self._call_rpc
        self._initialize()

    def public_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "recipient": self.config.recipient,
            "amount_luna": self.config.amount_luna,
            "amount_nim": self.config.amount_luna / 100_000,
            "network": self.config.network,
            "confirmations": self.config.confirmations,
        }

    def create_quote(self, source_job_id: str) -> dict[str, Any]:
        if not source_job_id:
            raise PaymentError("A completed basic analysis is required before payment.")
        quote_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        memo = f"prometheus:{quote_id}"
        created_at = int(time.time())
        expires_at = created_at + self.config.quote_lifetime_seconds
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO payment_quotes
                    (id, token_hash, memo, amount_luna, recipient, source_job_id, state, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    quote_id,
                    self._hash_token(token),
                    memo,
                    self.config.amount_luna,
                    self.config.recipient,
                    source_job_id,
                    created_at,
                    expires_at,
                ),
            )
        return {
            "id": quote_id,
            "source_job_id": source_job_id,
            "token": token,
            "memo": memo,
            "recipient": self.config.recipient,
            "amount_luna": self.config.amount_luna,
            "expires_at": expires_at,
            "state": "pending",
        }

    def verify(self, quote_id: str, tx_hash: str | None = None) -> dict[str, Any]:
        tx_hash = (tx_hash or "").strip().lower()
        quote = self._get_quote(quote_id)
        if quote is None:
            raise PaymentError("Unknown payment quote.")
        if quote["state"] in {"verified", "reserved", "consumed"}:
            if tx_hash and quote["tx_hash"] != tx_hash:
                raise PaymentError("This quote is already linked to another transaction.")
            return {"id": quote_id, "source_job_id": quote["source_job_id"], "state": quote["state"], "tx_hash": quote["tx_hash"]}
        if int(quote["expires_at"]) < int(time.time()):
            raise PaymentError("Payment quote has expired. Request a new payment quote.")
        network = self._rpc_call("getLatestBlock", [False])
        network_data = self._unwrap_rpc_data(network)
        expected_network = "TestAlbatross" if self.config.network == "testnet" else "MainAlbatross"
        if network_data.get("network") != expected_network:
            raise PaymentError("The configured RPC endpoint is on the wrong Nimiq network.")

        if tx_hash:
            try:
                result = self._rpc_call("getTransactionByHash", [tx_hash])
            except PaymentPending:
                raise
            transaction = self._normalize_transaction(self._unwrap_rpc_data(result))
        else:
            result = self._rpc_call(
                "getTransactionsByAddress", [quote["recipient"], 100, None]
            )
            transaction = {}
            for candidate in self._unwrap_rpc_items(result):
                normalized = self._normalize_transaction(candidate)
                data = normalized.get("recipientData", normalized.get("data", ""))
                if self._decode_data(data) == quote["memo"]:
                    transaction = normalized
                    break
            tx_hash = str(
                transaction.get("hash", transaction.get("transactionHash", ""))
            ).lower()
        if not transaction:
            raise PaymentPending("Transaction is not confirmed yet.")

        returned_hash = str(
            transaction.get("hash", transaction.get("transactionHash", ""))
        ).lower()
        if returned_hash != tx_hash:
            raise PaymentError("RPC returned a different transaction hash.")
        recipient = transaction.get("to", transaction.get("recipient", ""))
        if self._normalize_address(recipient) != self._normalize_address(quote["recipient"]):
            raise PaymentError("Transaction recipient does not match this quote.")
        if int(transaction.get("value", -1)) < int(quote["amount_luna"]):
            raise PaymentError("Transaction amount is below the quoted price.")
        transaction_data = transaction.get("recipientData", transaction.get("data", ""))
        if self._decode_data(transaction_data) != quote["memo"]:
            raise PaymentError("Transaction memo does not match this quote.")
        if transaction.get("executionResult") is not True:
            raise PaymentError("Transaction execution was not successful.")
        if transaction.get("blockNumber") is None:
            raise PaymentPending("Transaction has not been included in a block yet.")
        if int(transaction.get("confirmations") or 0) < self.config.confirmations:
            raise PaymentPending("Transaction needs more confirmations.")

        try:
            with self._connect() as db:
                cursor = db.execute(
                    """
                    UPDATE payment_quotes
                    SET state = 'verified', tx_hash = ?, verified_at = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (tx_hash, int(time.time()), quote_id),
                )
                if cursor.rowcount != 1:
                    current = db.execute(
                        "SELECT state, tx_hash FROM payment_quotes WHERE id = ?", (quote_id,)
                    ).fetchone()
                    if current is None or current["tx_hash"] != tx_hash:
                        raise PaymentError("This quote was verified by another transaction.")
        except sqlite3.IntegrityError as exc:
            raise PaymentError("This transaction has already been used for another quote.") from exc
        return {"id": quote_id, "source_job_id": quote["source_job_id"], "state": "verified", "tx_hash": tx_hash}

    def reserve(self, quote_id: str, token: str, source_job_id: str, job_id: str) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            quote = db.execute(
                "SELECT state, token_hash, source_job_id, job_id FROM payment_quotes WHERE id = ?", (quote_id,)
            ).fetchone()
            if quote is None or quote["token_hash"] != self._hash_token(token) or quote["source_job_id"] != source_job_id:
                raise PaymentError("A verified, unused payment is required for advanced analysis.")
            if quote["state"] in {"reserved", "consumed"} and quote["job_id"]:
                return str(quote["job_id"])
            cursor = db.execute(
                """
                UPDATE payment_quotes
                SET state = 'reserved', job_id = ?
                WHERE id = ? AND token_hash = ? AND state = 'verified'
                """,
                (job_id, quote_id, self._hash_token(token)),
            )
            if cursor.rowcount != 1:
                raise PaymentError("A verified, unused payment is required for advanced analysis.")
            return job_id

    def finalize(self, job_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE payment_quotes
                SET state = 'consumed', consumed_at = ?
                WHERE job_id = ? AND state = 'reserved'
                """,
                (int(time.time()), job_id),
            )

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_quotes (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT,
                    memo TEXT NOT NULL,
                    amount_luna INTEGER NOT NULL,
                    recipient TEXT NOT NULL,
                    state TEXT NOT NULL,
                    tx_hash TEXT UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    verified_at INTEGER,
                    consumed_at INTEGER,
                    job_id TEXT,
                    source_job_id TEXT
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(payment_quotes)")}
            if "token_hash" not in columns:
                db.execute("ALTER TABLE payment_quotes ADD COLUMN token_hash TEXT")
            if "job_id" not in columns:
                db.execute("ALTER TABLE payment_quotes ADD COLUMN job_id TEXT")
            if "source_job_id" not in columns:
                db.execute("ALTER TABLE payment_quotes ADD COLUMN source_job_id TEXT")

    def _get_quote(self, quote_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM payment_quotes WHERE id = ?", (quote_id,)
            ).fetchone()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _call_rpc(self, method: str, params: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
        request = urllib.request.Request(
            self.config.rpc_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise PaymentError("Could not reach the Nimiq RPC endpoint.") from exc
        if payload.get("error"):
            message = str(payload["error"])
            if method == "getTransactionByHash" and "not found" in message.lower():
                raise PaymentPending("Transaction is not confirmed yet.")
            raise PaymentError(f"Nimiq RPC error: {message}")
        return payload.get("result")

    @staticmethod
    def _unwrap_rpc_data(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        data = value.get("data", value)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _unwrap_rpc_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        data = value.get("data", value)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _normalize_transaction(value: dict[str, Any]) -> dict[str, Any]:
        nested = value.get("transaction")
        if not isinstance(nested, dict):
            return value
        transaction = dict(nested)
        if "executionResult" not in transaction and "executionResult" in value:
            transaction["executionResult"] = value["executionResult"]
        return transaction

    @staticmethod
    def _normalize_address(value: Any) -> str:
        return "".join(str(value).upper().split())

    @staticmethod
    def _decode_data(value: Any) -> str:
        if isinstance(value, list):
            try:
                return bytes(value).decode("utf-8")
            except (ValueError, TypeError, UnicodeDecodeError):
                return ""
        encoded = str(value or "")
        if encoded.startswith("0x"):
            encoded = encoded[2:]
        try:
            return bytes.fromhex(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()
