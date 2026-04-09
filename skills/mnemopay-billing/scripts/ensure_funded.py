#!/usr/bin/env python3
# Copyright 2026 Jerry Omiagbo / MnemoPay
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drop-in replacement for Pika's ensure_funded() using MnemoPay.

Pika's original ensure_funded() flow:
  1. Check DevKey exists
  2. Check balance via Pika API
  3. If low, create a Stripe checkout URL
  4. Poll the balance API until payment lands

This replacement uses MnemoPay's charge/settle pattern instead:
  1. Load the agent's MnemoPay state
  2. Check Agent FICO score and credit ceiling
  3. Create a pending charge (hold) for the requested amount
  4. Return the transaction_id for the caller to settle or refund

The caller is responsible for calling billing.py settle or billing.py refund
after the paid operation completes or fails.

Environment:
  MNEMOPAY_AGENT_ID  -- Required. Stable agent identifier.

Usage:
  python ensure_funded.py --amount 5.00 --description "Video meeting (30 min)"

Exit codes:
  0  -- Funded. stdout JSON contains transaction_id.
  1  -- Missing configuration.
  2  -- Credit check failed (amount exceeds ceiling).
  3  -- Charge creation failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

# Import from the billing module in the same directory
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from billing import (
    _get_agent_id,
    _load_agent,
    _save_state,
    _compute_fico,
    _tx_to_dict,
    eprint,
)


def ensure_funded(amount: float, description: str) -> Dict[str, Any]:
    """MnemoPay replacement for Pika's ensure_funded().

    Instead of check-balance -> Stripe checkout -> poll, this does:
      1. Load agent state
      2. Check FICO score and credit ceiling
      3. Create a charge hold

    Returns a dict with status and transaction details.
    """
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    # Step 1: Check credit
    balance = agent.balance()
    max_charge = 500 * balance.reputation

    eprint(f"Agent: {agent_id}")
    eprint(f"Wallet: ${balance.wallet:.2f}, Reputation: {balance.reputation:.2f}")
    eprint(f"Credit ceiling: ${max_charge:.2f}")

    fico_data: Dict[str, Any] = {}
    if agent._transactions:
        fico_data = _compute_fico(agent)
        eprint(f"FICO: {fico_data['fico_score']} ({fico_data['rating']})")
    else:
        eprint("FICO: 300 (no history)")

    if amount > max_charge:
        return {
            "status": "credit_exceeded",
            "amount": amount,
            "credit_ceiling": max_charge,
            "reputation": balance.reputation,
            "message": (
                f"Amount ${amount:.2f} exceeds credit ceiling ${max_charge:.2f}. "
                f"Complete more transactions to build reputation."
            ),
        }

    # Step 2: Create the charge hold
    try:
        tx = agent.charge(amount, description)
    except ValueError as exc:
        return {
            "status": "charge_failed",
            "amount": amount,
            "error": str(exc),
        }

    _save_state(agent)

    result = {
        "status": "funded",
        "transaction_id": tx.id,
        "amount": amount,
        "description": description,
        "wallet_balance": balance.wallet,
        "reputation": balance.reputation,
        "message": (
            f"Charge of ${amount:.2f} created. "
            f"Settle with: billing.py settle --transaction-id {tx.id}"
        ),
    }

    if fico_data:
        result["fico_score"] = fico_data["fico_score"]
        result["fico_rating"] = fico_data["rating"]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drop-in replacement for Pika ensure_funded() using MnemoPay"
    )
    parser.add_argument(
        "--amount",
        type=float,
        required=True,
        help="Amount in dollars to authorize",
    )
    parser.add_argument(
        "--description",
        required=True,
        help="What the payment is for",
    )
    args = parser.parse_args()

    if args.amount <= 0:
        eprint("Error: amount must be positive")
        print(json.dumps({"status": "error", "message": "Amount must be positive"}))
        return 2

    result = ensure_funded(args.amount, args.description)
    print(json.dumps(result, indent=2))

    if result["status"] == "funded":
        return 0
    elif result["status"] == "credit_exceeded":
        return 2
    else:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
