"""Avalanche Fuji Checkpoint contract helpers (Web3)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.exceptions import Web3Exception

load_dotenv()

logger = logging.getLogger("fixpro_blockchain")

FUJI_RPC_URL = os.getenv("FUJI_RPC_URL")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
BACKEND_WALLET_KEY = os.getenv("BACKEND_WALLET_KEY")

_ABI_PATH = Path(__file__).resolve().parent / "Checkpoint.abi.json"


def _require_env(name: str, value: str | None) -> str:
    if not value or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def _load_abi() -> list:
    try:
        with open(_ABI_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise RuntimeError(f"Checkpoint ABI not found at {_ABI_PATH}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid Checkpoint ABI JSON at {_ABI_PATH}: {e}") from e


def _to_bytes32_id(value: str) -> bytes:
    """Encode a string as UTF-8 and pad/truncate to 32 bytes."""
    encoded = value.encode("utf-8")
    if len(encoded) > 32:
        return encoded[:32]
    return encoded.ljust(32, b"\x00")


def _to_bytes32_hash(value: str) -> bytes:
    """Convert a hex hash string to a 32-byte value."""
    raw = value[2:] if value.startswith(("0x", "0X")) else value
    try:
        data = bytes.fromhex(raw)
    except ValueError as e:
        raise ValueError(f"Invalid hex hash: {value!r}") from e
    if len(data) > 32:
        return data[:32]
    return data.rjust(32, b"\x00")


def _get_web3() -> Web3:
    rpc_url = _require_env("FUJI_RPC_URL", FUJI_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Failed to connect to Avalanche Fuji RPC at {rpc_url}")
    return w3


def _get_contract(w3: Web3):
    address = _require_env("CONTRACT_ADDRESS", CONTRACT_ADDRESS)
    checksum = Web3.to_checksum_address(address)
    return w3.eth.contract(address=checksum, abi=_load_abi())


def add_checkpoint(batch_id: str, data_hash: str, previous_hash: str) -> str:
    """
    Call addCheckpoint on the Checkpoint contract.

    Signs and sends the transaction with BACKEND_WALLET_KEY, waits for the
    receipt, and returns the transaction hash as a hex string.
    """
    try:
        private_key = _require_env("BACKEND_WALLET_KEY", BACKEND_WALLET_KEY)
        w3 = _get_web3()
        contract = _get_contract(w3)
        account = Account.from_key(private_key)

        batch_b32 = _to_bytes32_id(batch_id)
        data_b32 = _to_bytes32_hash(data_hash)
        prev_b32 = _to_bytes32_hash(previous_hash)

        nonce = w3.eth.get_transaction_count(account.address)
        tx = contract.functions.addCheckpoint(
            batch_b32, data_b32, prev_b32
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": w3.eth.chain_id,
            }
        )

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.get("status") == 0:
            raise RuntimeError(
                f"addCheckpoint transaction reverted: {tx_hash.hex()}"
            )

        logger.info(
            "Checkpoint added for batch_id=%r tx=%s", batch_id, tx_hash.hex()
        )
        return tx_hash.hex()
    except Web3Exception as e:
        logger.exception("Web3 error in add_checkpoint: %s", e)
        raise RuntimeError(f"Blockchain add_checkpoint failed: {e}") from e
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception("Unexpected error in add_checkpoint: %s", e)
        raise RuntimeError(f"Blockchain add_checkpoint failed: {e}") from e


def get_latest_hash(batch_id: str) -> str:
    """
    Call getLatestHash on the Checkpoint contract (view).

    Returns the latest hash as a 0x-prefixed hex string.
    """
    try:
        w3 = _get_web3()
        contract = _get_contract(w3)
        batch_b32 = _to_bytes32_id(batch_id)
        result = contract.functions.getLatestHash(batch_b32).call()

        if isinstance(result, (bytes, bytearray)):
            return "0x" + bytes(result).hex()
        return Web3.to_hex(result)
    except Web3Exception as e:
        logger.exception("Web3 error in get_latest_hash: %s", e)
        raise RuntimeError(f"Blockchain get_latest_hash failed: {e}") from e
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception("Unexpected error in get_latest_hash: %s", e)
        raise RuntimeError(f"Blockchain get_latest_hash failed: {e}") from e
