"""Quick smoke test for Avalanche Fuji Checkpoint helpers."""
from __future__ import annotations

from app.blockchain import add_checkpoint, get_latest_hash


def main() -> None:
    batch_id = "TESTBATCH002"
    data_hash = "0x" + "ab" * 32
    previous_hash = "0x" + "00" * 32

    try:
        tx_hash = add_checkpoint(batch_id, data_hash, previous_hash)
        print(f"Transaction hash: {tx_hash}")

        latest = get_latest_hash(batch_id)
        print(f"Latest hash: {latest}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
