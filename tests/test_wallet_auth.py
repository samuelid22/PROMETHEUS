"""Public test vectors and generated test-only keys; never real wallet credentials."""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.testclient import TestClient

from prometheus.api.wallet_auth import (
    LOW_ORDER_Y, WalletAuthConfig, WalletAuthError, WalletAuthService,
    address_from_public_key, canonical_address, create_auth_app, format_address, message_digest,
)

ORIGIN = "https://wallet-preview.example.invalid"
KEY = Ed25519PrivateKey.from_private_bytes(bytes([1]) * 32)  # Publicly documented test fixture only.
PUBLIC = KEY.public_key().public_bytes_raw()
ADDRESS = format_address(address_from_public_key(PUBLIC))
OTHER = "NQ07 0000 0000 0000 0000 0000 0000 0000 0000"


@pytest.fixture
def service(tmp_path):
    clock = [1_800_000_000]
    instance = WalletAuthService(WalletAuthConfig(ORIGIN), tmp_path / "auth.db", lambda: clock[0])
    instance.test_clock = clock
    return instance


def signature(message):
    # Independent preimage construction, not the implementation's helper.
    data = message.encode("utf-8")
    digest = hashlib.sha256(b"\x16Nimiq Signed Message:\n" + str(len(data)).encode("ascii") + data).digest()
    return KEY.sign(digest).hex()


def verify(service, challenge, signed_message=None):
    return service.verify(challenge["id"], PUBLIC.hex(), signature(signed_message or challenge["message"]))


def test_known_nimiq_message_signature_vector():
    # Fixed UTF-8 format vector independently generated with Node's Ed25519/SHA256.
    message = "Prometheus wallet verification — TEST"
    digest = bytes.fromhex("02c7d4f8874904662e00e6dbbf9c3c0f848bd18fa26d6796915367a0ca1ed2e7")
    sig = bytes.fromhex("9be8beb8b4f423b825082f573ff8d88fe5e90b891067af43ca25ecbe267cbb61ee0a8112cc974e40e6d763ec2a9494267ae1225399a03ee0ecef4cb6b1095509")
    assert message_digest(message) == digest
    Ed25519PublicKey.from_public_bytes(bytes.fromhex("8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c")).verify(sig, digest)


def test_official_public_key_address_vector():
    # core-rs-albatross/wallet/tests/wallet.rs (public key/address pair).
    public = bytes.fromhex("7f07b8a4c2f6c2f7cb56584a00672af88733cb6f80f5d6e6cf4043a3d4aeec05")
    assert format_address(address_from_public_key(public)) == "NQ58 KKC2 JJ35 1N82 T5EM 5SHE R4FA UTFB 1JFX"


@pytest.mark.parametrize("address", [OTHER, "NQ05 563U 530Y XDRT L7GQ M6HE YRNU 20FE 4PNR", ADDRESS])
def test_address_format_checksum_normalization(address):
    assert format_address(canonical_address(address.lower())) == address
    assert canonical_address(address) == address.replace(" ", "")


@pytest.mark.parametrize("address", ["NQ08" + "0" * 32, "NQ07" + "I" * 32, "NQ07" + "0" * 31, "bad", None])
def test_invalid_addresses_fail_closed(address):
    with pytest.raises(WalletAuthError, match="invalid_address"):
        canonical_address(address)


def test_success_stored_message_and_canonical_set(service):
    challenge = service.issue([OTHER, ADDRESS.lower(), ADDRESS.replace(" ", "")])
    assert "Prometheus wallet verification" in challenge["message"]
    assert f"Origin: {ORIGIN}" in challenge["message"]
    assert "Nonce:" in challenge["message"]
    assert "Issued at:" in challenge["message"]
    assert challenge["expires_at"] == service.test_clock[0] + 300
    result = verify(service, challenge)
    assert result == {"verified": True, "signer_address": ADDRESS, "approved_address_count": 2, "scope": "current_page_signer"}
    assert "token" not in result


@pytest.mark.parametrize("change", ["tampered", "raw", "wrong_key"])
def test_invalid_or_modified_signature_does_not_consume(service, change):
    challenge = service.issue([ADDRESS])
    sig = signature(challenge["message"] + "tampered") if change == "tampered" else KEY.sign(challenge["message"].encode()).hex()
    if change == "wrong_key":
        sig = Ed25519PrivateKey.generate().sign(message_digest(challenge["message"])).hex()
    with pytest.raises(WalletAuthError, match="invalid_signature"):
        service.verify(challenge["id"], PUBLIC.hex(), sig)
    assert verify(service, challenge)["verified"]


def test_expiry_and_fresh_challenge(service):
    old = service.issue([ADDRESS])
    service.test_clock[0] += 300
    with pytest.raises(WalletAuthError, match="expired_challenge"):
        verify(service, old)
    fresh = service.issue([ADDRESS])
    assert fresh["id"] != old["id"] and fresh["message"] != old["message"]
    assert verify(service, fresh)["verified"]


def test_replay_is_rejected_in_another_service_instance(service):
    challenge = service.issue([ADDRESS])
    verify(service, challenge)
    second = WalletAuthService(service.config, service.database, service.clock)
    with pytest.raises(WalletAuthError, match="used_challenge"):
        verify(second, challenge)


def test_concurrent_replay_only_one_success(service):
    challenge = service.issue([ADDRESS])
    barrier = Barrier(8)
    def compete(_):
        barrier.wait()
        try:
            return verify(service, challenge)["verified"]
        except WalletAuthError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(compete, range(8)))
    assert results.count(True) == 1
    assert results.count("used_challenge") == 7


def test_signer_not_in_stored_approved_set(service):
    challenge = service.issue([OTHER])
    with pytest.raises(WalletAuthError, match="signer_not_approved"):
        verify(service, challenge)
    with sqlite3.connect(service.database) as db:
        assert db.execute("SELECT used FROM wallet_challenges").fetchone()[0] == 0


@pytest.mark.parametrize("key,sig,error", [("00", "00" * 64, "malformed_public_key"),
    (PUBLIC.hex(), "zz" * 64, "malformed_signature"), (PUBLIC.hex(), None, "malformed_signature")])
def test_malformed_crypto_input(service, key, sig, error):
    challenge = service.issue([ADDRESS])
    with pytest.raises(WalletAuthError, match=error):
        service.verify(challenge["id"], key, sig)


@pytest.mark.parametrize("y", sorted(LOW_ORDER_Y))
def test_low_order_keys_are_not_proof_of_ownership(service, y):
    public = y.to_bytes(32, "little")
    challenge = service.issue([format_address(address_from_public_key(public))])
    with pytest.raises(WalletAuthError, match="invalid_signature"):
        service.verify(challenge["id"], public.hex(), (b"\x01" + bytes(63)).hex())


def test_missing_and_cleanup(service):
    with pytest.raises(WalletAuthError, match="missing_challenge"):
        service.verify("f" * 32, PUBLIC.hex(), "00" * 64)
    service.issue([ADDRESS])
    service.test_clock[0] += 301
    service.cleanup()
    with sqlite3.connect(service.database) as db:
        assert db.execute("SELECT COUNT(*) FROM wallet_challenges").fetchone()[0] == 0


def test_bounded_issuance_and_verification_rate_limits(service):
    for _ in range(20):
        service.rate_limit("client", "challenge")
    with pytest.raises(WalletAuthError, match="rate_limited"):
        service.rate_limit("client", "challenge")
    for _ in range(40):
        service.rate_limit("client", "verify")
    with pytest.raises(WalletAuthError, match="rate_limited"):
        service.rate_limit("client", "verify")
    service.test_clock[0] += 60
    service.rate_limit("client", "challenge")


def test_http_contract_origin_tampering_and_payment_isolation(service):
    with TestClient(create_auth_app(service)) as client:
        assert client.get("/api/health").json()["wallet_auth_enabled"] is True
        assert client.post("/api/wallet-auth/challenge", json={"accounts": [ADDRESS]}).status_code == 403
        headers = {"Origin": ORIGIN}
        challenge = client.post("/api/wallet-auth/challenge", headers=headers, json={"accounts": [ADDRESS]}).json()
        body = {"challenge_id": challenge["id"], "public_key": PUBLIC.hex(), "signature": signature(challenge["message"])}
        tampered = client.post("/api/wallet-auth/verify", headers=headers, json={**body, "message": "tampered"})
        assert tampered.status_code == 400
        result = client.post("/api/wallet-auth/verify", headers=headers, json=body)
        assert result.status_code == 200 and result.json()["verified"] is True
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["access-control-allow-origin"] == ORIGIN
        assert client.post("/api/wallet-auth/verify", headers=headers, json=body).status_code == 409
        assert client.post("/api/payments/quotes", json={}).status_code == 404
        assert client.post("/api/inspect").status_code == 404
        assert client.post("/api/wallet-auth/challenge", headers=headers, content=b"x" * 16_385).status_code == 415
        assert client.post("/api/wallet-auth/challenge", headers={**headers, "Content-Type": "application/json"}, content=b"x" * 16_385).status_code == 413


def test_disabled_by_default_and_invalid_configuration(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_WALLET_AUTH_ENABLED", raising=False)
    client = TestClient(create_auth_app())
    assert client.post("/api/wallet-auth/challenge", json={}).status_code == 503
    monkeypatch.setenv("PROMETHEUS_WALLET_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_NIMIQ_NETWORK", "mainnet")
    with pytest.raises(ValueError, match="testnet"):
        create_auth_app()
    for origin in ["", "http://public.example", "https://user:pass@example.invalid", "https://example.invalid/path"]:
        with pytest.raises(ValueError):
            WalletAuthConfig(origin)
