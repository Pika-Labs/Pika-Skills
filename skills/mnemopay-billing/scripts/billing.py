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

"""MnemoPay billing operations for Pika Skills.

Wraps the MnemoPay Python SDK to provide charge/settle/refund operations,
Agent FICO credit scoring, and transaction history. Designed to be called
by AI coding agents as part of skill workflows.

All output goes to stdout as JSON. Diagnostic messages go to stderr.

Environment:
  MNEMOPAY_AGENT_ID  -- Required. Stable agent identifier.

Usage:
  python billing.py check-credit
  python billing.py charge --amount 5.00 --description "API call"
  python billing.py settle --transaction-id <uuid>
  python billing.py refund --transaction-id <uuid> --reason "operation failed"
  python billing.py history [--limit 20]

Exit codes:
  0  -- Success (result in stdout JSON)
  1  -- Missing configuration (no agent ID)
  2  -- Validation error (bad input, amount too high)
  3  -- Operation error (transaction not found, wrong status)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from mnemopay import MnemoPay, AgentFICO
from mnemopay.types import (
    FICOInput,
    FICOTransaction,
    TransactionStatus,
)

# Persist state across calls within the same session via a JSON file.
# Each agent gets its own state file under ~/.mnemopay/
STATE_DIR = Path.home() / ".mnemopay" / "pika-skills"


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _state_path(agent_id: str) -> Path:
    return STATE_DIR / f"{agent_id}.json"


def _save_state(agent: MnemoPay) -> None:
    """Persist agent state to disk so subsequent commands can access it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "agent_id": agent.agent_id,
        "wallet": agent._wallet,
        "reputation": agent._reputation,
        "total_volume": agent._total_volume,
        "created_at": agent._created_at.isoformat(),
        "transactions": {},
        "memories": {},
    }
    for tid, tx in agent._transactions.items():
        state["transactions"][tid] = {
            "id": tx.id,
            "agent_id": tx.agent_id,
            "amount": tx.amount,
            "reason": tx.reason,
            "status": tx.status.value,
            "created_at": tx.created_at.isoformat(),
            "completed_at": tx.completed_at.isoformat() if tx.completed_at else None,
            "platform_fee": tx.platform_fee,
            "net_amount": tx.net_amount,
            "counterparty_id": tx.counterparty_id,
        }
    path = _state_path(agent.agent_id)
    path.write_text(json.dumps(state, indent=2))
    eprint(f"State saved: {path}")


def _load_agent(agent_id: str) -> MnemoPay:
    """Load agent from persisted state, or create a new one."""
    agent = MnemoPay(agent_id)
    path = _state_path(agent_id)

    if not path.exists():
        eprint(f"New agent: {agent_id}")
        return agent

    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        eprint(f"Warning: could not load state ({exc}), starting fresh")
        return agent

    agent._wallet = state.get("wallet", 0.0)
    agent._reputation = state.get("reputation", 0.5)
    agent._total_volume = state.get("total_volume", 0.0)

    created_str = state.get("created_at")
    if created_str:
        try:
            agent._created_at = datetime.fromisoformat(created_str)
        except ValueError:
            pass

    from mnemopay.types import Transaction as TxType

    for tid, tx_data in state.get("transactions", {}).items():
        created_at = datetime.fromisoformat(tx_data["created_at"])
        completed_at = None
        if tx_data.get("completed_at"):
            completed_at = datetime.fromisoformat(tx_data["completed_at"])

        tx = TxType(
            id=tx_data["id"],
            agent_id=tx_data["agent_id"],
            amount=tx_data["amount"],
            reason=tx_data["reason"],
            status=TransactionStatus(tx_data["status"]),
            created_at=created_at,
            completed_at=completed_at,
            platform_fee=tx_data.get("platform_fee"),
            net_amount=tx_data.get("net_amount"),
            counterparty_id=tx_data.get("counterparty_id"),
        )
        agent._transactions[tid] = tx

    eprint(f"Loaded agent: {agent_id} (wallet=${agent._wallet:.2f}, "
           f"{len(agent._transactions)} transactions)")
    return agent


def _get_agent_id() -> str:
    agent_id = os.environ.get("MNEMOPAY_AGENT_ID", "").strip()
    if not agent_id:
        eprint("Error: MNEMOPAY_AGENT_ID environment variable is required.")
        eprint("Set it to a stable agent identifier, e.g.: export MNEMOPAY_AGENT_ID=pika-agent-01")
        sys.exit(1)
    return agent_id


def _compute_fico(agent: MnemoPay) -> Dict[str, Any]:
    """Compute the Agent FICO score from current transaction history."""
    txs = list(agent._transactions.values())
    fico_txs = [
        FICOTransaction(
            id=tx.id,
            amount=tx.amount,
            status=tx.status,
            created_at=tx.created_at,
            reason=tx.reason,
            completed_at=tx.completed_at,
            counterparty_id=tx.counterparty_id,
        )
        for tx in txs
    ]

    disputes = list(agent._disputes.values()) if hasattr(agent, "_disputes") else []
    disputes_lost = sum(1 for d in disputes if getattr(d, "outcome", None) == "refund")

    fico_input = FICOInput(
        transactions=fico_txs,
        created_at=agent._created_at,
        fraud_flags=0,
        dispute_count=len(disputes),
        disputes_lost=disputes_lost,
        warnings=0,
        memories_count=len(agent._memories),
    )

    scorer = AgentFICO()
    result = scorer.compute(fico_input)
    return {
        "fico_score": result.score,
        "rating": result.rating.value,
        "trust_level": result.trust_level.value,
        "fee_rate": result.fee_rate,
        "requires_human_approval": result.requires_hitl,
        "stable": result.stable,
        "confidence": result.confidence,
    }


def _tx_to_dict(tx: Any) -> Dict[str, Any]:
    """Convert a Transaction to a JSON-serializable dict."""
    return {
        "id": tx.id,
        "agent_id": tx.agent_id,
        "amount": tx.amount,
        "reason": tx.reason,
        "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status),
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
        "completed_at": tx.completed_at.isoformat() if tx.completed_at else None,
        "platform_fee": tx.platform_fee,
        "net_amount": tx.net_amount,
    }


# ── Commands ──────────────────────────────────────────────────────────────


def cmd_check_credit(_args: argparse.Namespace) -> int:
    """Check agent credit: FICO score, balance, reputation, credit ceiling."""
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    balance = agent.balance()
    max_charge = 500 * balance.reputation

    result: Dict[str, Any] = {
        "agent_id": agent_id,
        "wallet_balance": balance.wallet,
        "reputation": balance.reputation,
        "credit_ceiling": round(max_charge, 2),
        "transaction_count": len(agent._transactions),
    }

    # Compute FICO if there is any transaction history
    if agent._transactions:
        fico = _compute_fico(agent)
        result["fico"] = fico
    else:
        result["fico"] = {
            "fico_score": 300,
            "rating": "poor",
            "trust_level": "minimal",
            "fee_rate": 0.025,
            "requires_human_approval": True,
            "stable": False,
            "confidence": 0.0,
            "note": "No transaction history yet. Score will improve with successful settlements.",
        }

    print(json.dumps(result, indent=2))
    return 0


def cmd_charge(args: argparse.Namespace) -> int:
    """Create a pending charge (hold) on the agent's account."""
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    amount = args.amount
    description = args.description

    if amount <= 0:
        eprint(f"Error: amount must be positive, got {amount}")
        print(json.dumps({"error": "Amount must be positive", "amount": amount}))
        return 2

    try:
        tx = agent.charge(amount, description)
    except ValueError as exc:
        eprint(f"Error: {exc}")
        print(json.dumps({"error": str(exc), "amount": amount}))
        return 2

    _save_state(agent)

    result = _tx_to_dict(tx)
    result["transaction_id"] = tx.id
    result["message"] = f"Charge of ${amount:.2f} created (pending). Settle after operation completes."

    print(json.dumps(result, indent=2))
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    """Settle (finalize) a pending transaction."""
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    tx_id = args.transaction_id

    try:
        tx = agent.settle(tx_id)
    except ValueError as exc:
        eprint(f"Error: {exc}")
        print(json.dumps({"error": str(exc), "transaction_id": tx_id}))
        return 3

    _save_state(agent)

    result = _tx_to_dict(tx)
    result["transaction_id"] = tx.id
    result["message"] = (
        f"Payment settled: ${tx.amount:.2f} "
        f"(fee: ${tx.platform_fee:.2f}, net: ${tx.net_amount:.2f})"
    )

    print(json.dumps(result, indent=2))
    return 0


def cmd_refund(args: argparse.Namespace) -> int:
    """Refund a transaction (pending or completed)."""
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    tx_id = args.transaction_id
    reason = args.reason or "No reason provided"

    try:
        tx = agent.refund(tx_id, reason)
    except ValueError as exc:
        eprint(f"Error: {exc}")
        print(json.dumps({"error": str(exc), "transaction_id": tx_id}))
        return 3

    _save_state(agent)

    result = _tx_to_dict(tx)
    result["transaction_id"] = tx.id
    result["refund_reason"] = reason
    result["message"] = f"Transaction ${tx.amount:.2f} refunded."

    print(json.dumps(result, indent=2))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Get transaction history for the agent."""
    agent_id = _get_agent_id()
    agent = _load_agent(agent_id)

    limit = args.limit or 50
    txs = agent.history(limit)

    result = {
        "agent_id": agent_id,
        "count": len(txs),
        "transactions": [_tx_to_dict(tx) for tx in txs],
    }

    print(json.dumps(result, indent=2))
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MnemoPay billing operations for Pika Skills"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check-credit
    sub.add_parser("check-credit", help="Check FICO score, balance, and credit ceiling")

    # charge
    charge_p = sub.add_parser("charge", help="Create a pending charge")
    charge_p.add_argument("--amount", type=float, required=True, help="Amount in dollars")
    charge_p.add_argument("--description", required=True, help="What the charge is for")

    # settle
    settle_p = sub.add_parser("settle", help="Settle a pending transaction")
    settle_p.add_argument("--transaction-id", required=True, help="Transaction UUID from charge")

    # refund
    refund_p = sub.add_parser("refund", help="Refund a transaction")
    refund_p.add_argument("--transaction-id", required=True, help="Transaction UUID to refund")
    refund_p.add_argument("--reason", help="Reason for the refund")

    # history
    history_p = sub.add_parser("history", help="Transaction history")
    history_p.add_argument("--limit", type=int, default=50, help="Max transactions to return")

    args = parser.parse_args()

    dispatch = {
        "check-credit": cmd_check_credit,
        "charge": cmd_charge,
        "settle": cmd_settle,
        "refund": cmd_refund,
        "history": cmd_history,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
