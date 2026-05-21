"""
Run this to verify Kotak Neo API v2 connectivity end-to-end.
Usage: python tests/test_kotak_connection.py

All six steps must show PASS before trading can proceed.
Step 0 (config) must pass before any API calls are attempted.
"""

import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PLACEHOLDERS = {"BASE32_TOTP_SECRET_FROM_QR_SCAN", "your_consumer_key_here", ""}

_PASS = "\033[92mPASS\033[0m"
_FAIL = "\033[91mFAIL\033[0m"


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = _PASS if ok else _FAIL
    print(f"  [{status}] {label}" + (f"  ->  {detail}" if detail else ""))
    return ok


def step0_config() -> bool:
    print("\n[0] Config check")
    from config.settings import settings

    required = {
        "kotak_consumer_key": settings.kotak_consumer_key,
        "kotak_mobile_number": settings.kotak_mobile_number,
        "kotak_ucc": settings.kotak_ucc,
        "kotak_mpin": settings.kotak_mpin,
        "kotak_totp_secret": settings.kotak_totp_secret,
    }
    all_ok = True
    for name, val in required.items():
        ok = bool(val) and val not in PLACEHOLDERS
        if not check(name, ok, val[:6] + "..." if ok else f"MISSING or placeholder: '{val}'"):
            all_ok = False
    return all_ok


def step1_auth() -> "KotakBroker | None":
    print("\n[1] Authentication")
    try:
        from broker.kotak_direct import KotakBroker
        broker = KotakBroker()
        broker._authenticate()
        check("totp_login + totp_validate", broker._client is not None, "client initialised")
        return broker
    except Exception as exc:
        check("authenticate", False, str(exc))
        return None


def step2_quote(broker) -> bool:
    print("\n[2] Quote fetch — RELIANCE")
    try:
        ltp = broker.get_ltp("RELIANCE", "nse_cm")
        return check("get_ltp(RELIANCE)", ltp is not None, f"LTP = Rs.{ltp}")
    except Exception as exc:
        return check("get_ltp", False, str(exc))


def step3_equity(broker) -> bool:
    print("\n[3] Account equity")
    try:
        equity = broker.get_account_equity()
        return check("get_account_equity()", equity > 0, f"Rs.{equity:,.0f}")
    except Exception as exc:
        return check("get_account_equity", False, str(exc))


def step4_positions(broker) -> bool:
    print("\n[4] Positions")
    try:
        positions = broker.get_positions()
        return check("get_positions()", isinstance(positions, list), f"{len(positions)} open position(s)")
    except Exception as exc:
        return check("get_positions", False, str(exc))


def step5_holdings(broker) -> bool:
    print("\n[5] Holdings")
    try:
        holdings = broker.get_holdings()
        return check("get_holdings()", isinstance(holdings, list), f"{len(holdings)} holding(s)")
    except Exception as exc:
        return check("get_holdings", False, str(exc))


def main():
    print("=" * 55)
    print("  Kotak Neo API v2 - Connection Test")
    print("=" * 55)

    if not step0_config():
        print("\nFill in missing .env values and re-run.\n")
        sys.exit(1)

    broker = step1_auth()
    if broker is None:
        print("\nAuthentication failed — check credentials and TOTP secret.\n")
        sys.exit(1)

    results = [
        step2_quote(broker),
        step3_equity(broker),
        step4_positions(broker),
        step5_holdings(broker),
    ]

    passed = sum(results)
    total = len(results) + 2  # +2 for config and auth
    print(f"\n{'=' * 55}")
    print(f"  Result: {passed + 2}/{total} steps passed")
    if all(results):
        print("  Kotak Neo API v2 is working correctly.")
    else:
        print("  Some steps failed — check details above.")
    print("=" * 55 + "\n")

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
