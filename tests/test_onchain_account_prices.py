import base64
import struct
import unittest

from solders.pubkey import Pubkey

import onchain_account_prices as prices


MINT = "CTPoyCwkjMvoJwU4xvZZqoD8tiYk6yDchySiN5gGpump"
BASE_VAULT = "5jMpkf4JF4noHftLgNKyPNh6roVfPSGSjuEk3U4eLKRa"
QUOTE_VAULT = "43DVcZR4kQFjh4Xm2i3DcneRxNjZp7HMud8yDrJWrDr8"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def account(raw, owner):
    return {"owner": owner, "data": [base64.b64encode(raw).decode(), "base64"]}


def mint_account():
    raw = bytearray(82)
    struct.pack_into("<Q", raw, 36, 1_000_000_000_000_000)
    raw[44] = 6
    return account(raw, TOKEN_PROGRAM)


def curve_account(complete=False, quote_mint=None):
    raw = bytearray(115)
    raw[:8] = prices.BONDING_CURVE_DISCRIMINATOR
    struct.pack_into("<QQ", raw, 8, 500_000_000_000_000, 50_000_000_000)
    raw[48] = int(complete)
    if quote_mint:
        raw[83:115] = bytes(Pubkey.from_string(quote_mint))
    return account(raw, prices.PUMP_PROGRAM_ID)


def pool_account(virtual_quote=10_000_000_000, base_mint=MINT):
    raw = bytearray(261)
    raw[:8] = prices.POOL_DISCRIMINATOR
    raw[43:75] = bytes(Pubkey.from_string(base_mint))
    raw[75:107] = bytes(prices.WSOL)
    raw[139:171] = bytes(Pubkey.from_string(BASE_VAULT))
    raw[171:203] = bytes(Pubkey.from_string(QUOTE_VAULT))
    raw[245:261] = virtual_quote.to_bytes(16, "little", signed=True)
    return account(raw, prices.PUMP_AMM_PROGRAM_ID)


def vault_account(mint, pool, amount):
    raw = bytearray(165)
    raw[:32] = bytes(Pubkey.from_string(mint))
    raw[32:64] = bytes(Pubkey.from_string(pool))
    struct.pack_into("<Q", raw, 64, amount)
    return account(raw, TOKEN_PROGRAM)


class AccountPriceTests(unittest.TestCase):
    def test_canonical_pool_derivation_matches_live_pool(self):
        self.assertEqual(
            prices.account_addresses(MINT)[1],
            "3dcwhqJp6JBTJPq8ga335HWgSQVS7uQmdmeX7iGjMNpj",
        )

    def test_curve_price_uses_one_batch_and_actual_mint_supply(self):
        calls = []

        def rpc(url, method, params):
            calls.append((url, method, params))
            return {"context": {"slot": 10}, "value": [
                mint_account(), curve_account(), None,
            ]}

        result = prices.fetch_account_prices("https://rpc.test", [MINT], rpc)
        self.assertEqual(result[MINT]["status"], "curve")
        self.assertAlmostEqual(result[MINT]["price_sol"], 1e-7)
        self.assertAlmostEqual(result[MINT]["market_cap_sol"], 100)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][2][0]), 3)

    def test_amm_includes_virtual_quote_and_checks_vault_owner(self):
        pool = prices.account_addresses(MINT)[1]
        calls = []

        def rpc(url, method, params):
            calls.append((method, params))
            if len(calls) == 1:
                return {"context": {"slot": 10}, "value": [
                    mint_account(), curve_account(complete=True), pool_account(),
                ]}
            return {"context": {"slot": 11}, "value": [
                vault_account(MINT, pool, 500_000_000_000_000),
                vault_account(prices.WSOL_MINT, pool, 100_000_000_000),
            ]}

        result = prices.fetch_account_prices("https://rpc.test", [MINT], rpc)
        self.assertEqual(result[MINT]["status"], "amm")
        self.assertAlmostEqual(result[MINT]["price_sol"], 2.2e-7)
        self.assertAlmostEqual(result[MINT]["market_cap_sol"], 220)
        self.assertEqual(calls[1][1][1]["minContextSlot"], 10)

    def test_missing_pool_and_unsupported_quote_do_not_invent_price(self):
        def missing_pool(url, method, params):
            return {"context": {"slot": 10}, "value": [
                mint_account(), curve_account(complete=True), None,
            ]}

        result = prices.fetch_account_prices("https://rpc.test", [MINT], missing_pool)
        self.assertEqual(result[MINT], {"status": "pool_missing", "slot": 10})

        def foreign_quote(url, method, params):
            return {"context": {"slot": 10}, "value": [
                mint_account(), curve_account(quote_mint=MINT), None,
            ]}

        result = prices.fetch_account_prices("https://rpc.test", [MINT], foreign_quote)
        self.assertEqual(result[MINT], {"status": "unsupported_quote", "slot": 10})

    def test_invalid_vault_does_not_produce_price(self):
        pool = prices.account_addresses(MINT)[1]
        calls = 0

        def rpc(url, method, params):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"context": {"slot": 10}, "value": [
                    mint_account(), curve_account(complete=True), pool_account(),
                ]}
            return {"context": {"slot": 11}, "value": [
                vault_account(MINT, pool, 500_000_000_000_000),
                vault_account(prices.WSOL_MINT, MINT, 100_000_000_000),
            ]}

        result = prices.fetch_account_prices("https://rpc.test", [MINT], rpc)
        self.assertEqual(result[MINT], {"status": "invalid_vaults", "slot": 11})


if __name__ == "__main__":
    unittest.main()
