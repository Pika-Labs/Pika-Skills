---
name: mnemopay-billing
description: |
  Billing layer for paid skills using MnemoPay (memory + wallet for AI agents).
  Replaces ensure_funded() with proper charge/settle/refund flow, Agent FICO
  credit scoring (300-850), fraud detection, and reputation tracking.
  Trigger: any skill that requires payment before executing a paid operation.
version: 1.0.0
author: Jerry Omiagbo <jeremiah@getbizsuite.com>
tags:
  - billing
  - payments
  - fico
  - fraud-detection
  - agent-wallet
metadata:
  openclaw:
    requires:
      env: ["MNEMOPAY_AGENT_ID"]
      bins: ["python3"]
    primaryEnv: "MNEMOPAY_AGENT_ID"
---

# MnemoPay Billing

Script: `SKILL_DIR=skills/mnemopay-billing`

Billing layer that gives any Pika skill proper payment handling. Instead of
redirecting users to raw Stripe checkout links and polling, MnemoPay provides
charge holds, settlement on success, automatic refunds on failure, and an Agent
FICO credit score (300-850) that tracks the agent's financial trustworthiness
over time.

## First-Time Setup

Run once when the skill is first loaded:

```bash
pip install -r $SKILL_DIR/requirements.txt
```

### Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `MNEMOPAY_AGENT_ID` | Yes | Unique agent identifier. Any stable string (e.g. `pika-agent-01`). |

If `MNEMOPAY_AGENT_ID` is not set, ask the user:

> I need an agent ID for MnemoPay billing. This is a stable identifier for this agent (e.g. `pika-agent-01`). What should I use?

Do not proceed until the user provides one. Set it in the environment before
running any billing commands.

---

## How Billing Works

MnemoPay uses a **charge/settle/refund** pattern instead of upfront payment:

1. **charge** -- Creates a hold (pending transaction) *before* the paid operation runs.
2. **settle** -- Finalizes the payment *after* the operation completes successfully.
3. **refund** -- Reverses the charge *if* the operation fails or the user cancels.

This is the same pattern that hotels and gas stations use: authorize first,
capture later. The agent never pays for work that was not delivered.

---

## Billing Flow (for other skills to follow)

When a skill needs to charge the user for an operation, follow this sequence:

### Step 1 -- Check credit before charging

```bash
python $SKILL_DIR/scripts/billing.py check-credit
```

This returns JSON with the agent's FICO score, available credit ceiling, wallet
balance, and reputation tier. If the FICO score is below 580 (POOR), warn the
user that the charge may require human approval.

### Step 2 -- Create a charge hold

```bash
python $SKILL_DIR/scripts/billing.py charge \
  --amount <dollars> \
  --description "Brief description of what the charge is for"
```

Exit 0: stdout contains JSON with `transaction_id` and `status: "pending"`.
Save the `transaction_id` -- you need it for settle or refund.

Exit non-zero: the charge was rejected (amount exceeds credit ceiling, or agent
reputation is too low). Show the error to the user and do not proceed with the
paid operation.

### Step 3 -- Execute the paid operation

Run whatever the skill does (join a meeting, generate a video, call an API,
etc.). This is the work that the charge is paying for.

### Step 4a -- Settle on success

If the operation completed successfully:

```bash
python $SKILL_DIR/scripts/billing.py settle --transaction-id <id>
```

This finalizes the payment, applies the platform fee (1.0-1.9% depending on
volume), credits the wallet, and boosts the agent's reputation.

### Step 4b -- Refund on failure

If the operation failed or was cancelled:

```bash
python $SKILL_DIR/scripts/billing.py refund \
  --transaction-id <id> \
  --reason "Brief explanation of why the refund is needed"
```

This reverses the charge. The user is not billed for failed work.

---

## Drop-In Replacement for ensure_funded()

If a skill currently uses Pika's `ensure_funded()` pattern (check balance, redirect
to Stripe checkout, poll for payment), replace it with:

```bash
python $SKILL_DIR/scripts/ensure_funded.py --amount <dollars> --description "what for"
```

This wraps the full charge/settle flow in a single call for backward
compatibility. Exit 0 means funded (stdout JSON contains `transaction_id`).
Exit non-zero means payment failed.

**Important:** When using the drop-in replacement, the caller is still
responsible for settling or refunding. The `transaction_id` in the output must
be passed to `billing.py settle` or `billing.py refund` after the operation.

---

## Querying History

```bash
python $SKILL_DIR/scripts/billing.py history --limit 20
```

Returns JSON array of recent transactions with amounts, statuses, timestamps,
and fees.

---

## Commands Reference

| Command | Description |
|---------|-------------|
| `billing.py check-credit` | FICO score, credit ceiling, balance, reputation |
| `billing.py charge --amount N --description "..."` | Create pending charge |
| `billing.py settle --transaction-id ID` | Finalize payment |
| `billing.py refund --transaction-id ID --reason "..."` | Reverse charge |
| `billing.py history [--limit N]` | Transaction history |
| `ensure_funded.py --amount N --description "..."` | Drop-in replacement |

All commands output JSON to stdout and diagnostic messages to stderr.
