from __future__ import annotations

import pytest

from prometheus.api.payments import PaymentConfig, PaymentError, PaymentPending, PaymentService


RECIPIENT = "NQ43 HJUE 9G1D 5LQF C752 5T7H EJ9M 4QEM 1CB1"
TX_HASH = "ab" * 32
SOURCE_JOB = "basic-1"


def _service(tmp_path, transaction_overrides=None):
    transaction_overrides = transaction_overrides or {}
    holder = {}

    def rpc_call(method, params):
        if method == "getLatestBlock":
            return {"data": {"network": "TestAlbatross"}}
        quote = holder["quote"]
        transaction = {
            "hash": TX_HASH,
            "to": RECIPIENT,
            "value": 1_000_000,
            "recipientData": quote["memo"].encode().hex(),
            "executionResult": True,
            "blockNumber": 123,
            "confirmations": 1,
        }
        transaction.update(transaction_overrides)
        if method == "getTransactionsByAddress":
            return {"data": [transaction]}
        return {"data": transaction}

    service = PaymentService(
        PaymentConfig(
            recipient=RECIPIENT,
            amount_luna=1_000_000,
            rpc_url="https://rpc.test.invalid/",
        ),
        tmp_path / "payments.db",
        rpc_call=rpc_call,
    )
    holder["quote"] = service.create_quote(SOURCE_JOB)
    return service, holder["quote"]


def test_verifies_reserves_and_finalizes_payment_once(tmp_path):
    service, quote = _service(tmp_path)

    verified = service.verify(quote["id"], TX_HASH)
    assert verified["state"] == "verified"
    assert service.reserve(quote["id"], quote["token"], SOURCE_JOB, "job-1") == "job-1"
    service.finalize("job-1")

    with pytest.raises(PaymentError, match="verified, unused"):
        service.reserve(quote["id"], "wrong", SOURCE_JOB, "job-2")


def test_discovers_payment_by_quote_memo_without_transaction_hash(tmp_path):
    service, quote = _service(tmp_path)

    verified = service.verify(quote["id"])

    assert verified == {"id": quote["id"], "source_job_id": SOURCE_JOB, "state": "verified", "tx_hash": TX_HASH}


def test_verifies_official_nested_rpc_transaction_shape(tmp_path):
    holder = {}

    def rpc_call(method, params):
        if method == "getLatestBlock":
            return {"data": {"network": "TestAlbatross"}}
        quote = holder["quote"]
        return {
            "data": {
                "transaction": {
                    "hash": TX_HASH,
                    "to": RECIPIENT,
                    "value": 1_000_000,
                    "recipientData": list(quote["memo"].encode()),
                    "blockNumber": 123,
                    "confirmations": 1,
                },
                "executionResult": True,
            }
        }

    service = PaymentService(
        PaymentConfig(RECIPIENT, 1_000_000, "https://rpc.test.invalid/"),
        tmp_path / "payments.db",
        rpc_call=rpc_call,
    )
    holder["quote"] = service.create_quote(SOURCE_JOB)

    verified = service.verify(holder["quote"]["id"], TX_HASH)

    assert verified["state"] == "verified"


def test_rejects_expired_pending_quote_before_rpc(tmp_path):
    def unexpected_rpc(method, params):
        raise AssertionError("expired quote must not call RPC")

    service = PaymentService(
        PaymentConfig(
            RECIPIENT,
            1_000_000,
            "https://rpc.test.invalid/",
            quote_lifetime_seconds=-1,
        ),
        tmp_path / "payments.db",
        rpc_call=unexpected_rpc,
    )
    quote = service.create_quote(SOURCE_JOB)

    with pytest.raises(PaymentError, match="expired"):
        service.verify(quote["id"], TX_HASH)


def test_payment_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="network"):
        PaymentConfig(RECIPIENT, 1_000_000, "https://rpc.test.invalid/", network="typo")
    with pytest.raises(ValueError, match="positive"):
        PaymentConfig(RECIPIENT, 0, "https://rpc.test.invalid/")
    with pytest.raises(ValueError, match="confirmations"):
        PaymentConfig(RECIPIENT, 1_000_000, "https://rpc.test.invalid/", confirmations=-1)


def test_public_quote_id_cannot_consume_payment(tmp_path):
    service, quote = _service(tmp_path)
    service.verify(quote["id"], TX_HASH)

    with pytest.raises(PaymentError, match="verified, unused"):
        service.reserve(quote["id"], quote["id"], SOURCE_JOB, "job-1")

    assert service.reserve(quote["id"], quote["token"], SOURCE_JOB, "job-1") == "job-1"


def test_payment_remains_bound_to_original_analysis_job(tmp_path):
    service, quote = _service(tmp_path)
    service.verify(quote["id"], TX_HASH)
    assert service.reserve(quote["id"], quote["token"], SOURCE_JOB, "job-1") == "job-1"
    assert service.reserve(quote["id"], quote["token"], SOURCE_JOB, "job-2") == "job-1"
    with pytest.raises(PaymentError, match="verified, unused"):
        service.reserve(quote["id"], quote["token"], "basic-2", "job-2")
    service.finalize("job-1")
    assert service.reserve(quote["id"], quote["token"], SOURCE_JOB, "job-3") == "job-1"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"to": "NQ00 WRONG"}, "recipient"),
        ({"value": 999_999}, "amount"),
        ({"recipientData": "00"}, "memo"),
        ({"executionResult": False}, "successful"),
    ],
)
def test_rejects_mismatched_transaction(tmp_path, overrides, message):
    service, quote = _service(tmp_path, overrides)
    with pytest.raises(PaymentError, match=message):
        service.verify(quote["id"], TX_HASH)


def test_waits_for_confirmation(tmp_path):
    service, quote = _service(tmp_path, {"confirmations": 0})
    with pytest.raises(PaymentPending, match="confirmations"):
        service.verify(quote["id"], TX_HASH)


def test_transaction_hash_cannot_pay_two_quotes(tmp_path):
    service, first = _service(tmp_path)
    service.verify(first["id"], TX_HASH)
    second = service.create_quote("basic-2")

    original_rpc = service._rpc_call

    def second_rpc(method, params):
        result = original_rpc(method, params)
        if method == "getTransactionByHash":
            result["data"]["recipientData"] = second["memo"].encode().hex()
        return result

    service._rpc_call = second_rpc
    with pytest.raises(PaymentError, match="already been used"):
        service.verify(second["id"], TX_HASH)
