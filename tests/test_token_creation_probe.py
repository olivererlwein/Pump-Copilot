import unittest
from unittest.mock import Mock

from scripts.probe_token_creation import CREATE_DISCRIMINATORS, probe, verify_receipt
from solana_rpc_fallback import PUMP_PROGRAM_ID, _base58_encode


MINT = "8deJ9xeUvXSJwicYptA9mHsU2rN2pDx37KWzkDkEXhU6"
SIGNATURE = "signature-test"
ROW = {"signature": SIGNATURE, "slot": 42, "blockTime": 1700000000, "err": None}


def receipt(discriminator, mint=MINT, program=PUMP_PROGRAM_ID):
    return {
        "slot": 42, "blockTime": 1700000000,
        "transaction": {
            "signatures": [SIGNATURE],
            "message": {"instructions": [{
                "programId": program, "accounts": [mint],
                "data": _base58_encode(discriminator + b"payload"),
            }]},
        },
        "meta": {"err": None},
    }


class TokenCreationProbeTests(unittest.TestCase):
    def test_both_official_create_instructions_verify(self):
        for discriminator, kind in CREATE_DISCRIMINATORS.items():
            with self.subTest(kind=kind):
                result = verify_receipt(MINT, ROW, receipt(discriminator))
                self.assertEqual(result["status"], "verified_create")
                self.assertEqual(result["instruction"], kind)

    def test_other_mint_or_program_cannot_pass(self):
        discriminator = next(iter(CREATE_DISCRIMINATORS))
        self.assertEqual(
            verify_receipt(MINT, ROW, receipt(discriminator, mint="other"))["status"],
            "oldest_not_pump_create",
        )
        self.assertEqual(
            verify_receipt(MINT, ROW, receipt(discriminator, program="other"))["status"],
            "oldest_not_pump_create",
        )

    def test_truncated_history_never_fetches_transaction(self):
        fetch_transaction = Mock()
        result = probe(
            MINT, "https://rpc.test",
            fetch_signatures=lambda *_args, **_kwargs: [ROW] * 1000,
            fetch_transaction=fetch_transaction,
        )
        self.assertEqual(result["status"], "history_truncated")
        fetch_transaction.assert_not_called()

    def test_missing_block_time_is_not_verified(self):
        discriminator = next(iter(CREATE_DISCRIMINATORS))
        candidate = receipt(discriminator)
        candidate["blockTime"] = None
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "missing_block_time",
        )

    def test_missing_meta_or_slot_is_not_verified(self):
        discriminator = next(iter(CREATE_DISCRIMINATORS))
        candidate = receipt(discriminator)
        del candidate["meta"]
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "missing_transaction_meta",
        )
        candidate = receipt(discriminator)
        del candidate["slot"]
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "slot_mismatch",
        )

    def test_failed_or_mismatched_transaction_is_not_verified(self):
        discriminator = next(iter(CREATE_DISCRIMINATORS))
        candidate = receipt(discriminator)
        candidate["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "failed_transaction",
        )
        candidate = receipt(discriminator)
        candidate["transaction"]["signatures"] = ["different"]
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "signature_mismatch",
        )
        candidate = receipt(discriminator)
        candidate["slot"] = 43
        self.assertEqual(
            verify_receipt(MINT, ROW, candidate)["status"],
            "slot_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
