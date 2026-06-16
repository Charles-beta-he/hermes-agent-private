# Stablecoin / Wallet Payment Preflight Report Template (Read-only, Non-custodial)

Date: 2026-06-16

Source dry-run: `/Users/charles/Desktop/folder/stablecoin_wallet_payment_public_pain_dryrun.md`

## Boundary

This template is for **read-only research and preflight reporting**. It must not:

- sign transactions;
- custody funds or keys;
- request, store, or process seed phrases/private keys;
- bypass exchange withdrawal holds or platform risk controls;
- provide legal, tax, investment, or financial advice;
- automate payments, refunds, gas sponsorship, or withdrawals.

## Input

```yaml
asset: USDC|USDT|other
network: ethereum|solana|tron|polygon|base|other
payment_context: merchant_checkout|supplier_payment|personal_transfer|treasury_ops
exchange_or_wallet: string
amount: string
jurisdiction: optional string
known_constraints:
  - string
source_evidence_required: true
```

## Report sections

### 1. Transferability / withdrawal availability

Questions:

- Were funds recently bought/deposited with ACH/card/PayPal/digital wallet?
- Does the exchange/wallet impose temporary withdrawal holds?
- Is the visible balance different from withdrawable balance?

Evidence requirements:

- exchange/wallet support URL;
- quoted hold duration or withdrawal restriction;
- timestamp of evidence capture.

### 2. Network / gas / pending risk

Questions:

- Is the payment network correct for the recipient?
- Does sender hold enough native token for gas?
- Is there a stuck pending nonce/transaction?
- Is order/payment quote close to expiry?

Evidence requirements:

- wallet support URL or chain explorer URL;
- excerpt describing gas/pending behavior;
- no private keys or signing requests.

### 3. Scam / self-custody warning

Questions:

- Is any support actor asking for seed phrase/private key?
- Is there a fake客服/impersonation pattern?
- Is the destination address newly introduced or unverified?

Evidence requirements:

- official wallet safety doc;
- source warning excerpt;
- address-risk source if public and compliant.

### 4. Peg / reserve / issuer risk

Questions:

- Is the stablecoin issuer/reserve status relevant to this transfer?
- Has there been a recent depeg/reserve/regulatory event?
- Is redemption assumption being presented as guaranteed?

Evidence requirements:

- issuer / regulator / credible news source URL;
- quoted risk statement;
- explicit “not financial advice” boundary.

### 5. Merchant exception handling

Questions:

- Could the user underpay/overpay/late-pay?
- Is refund address collection safe and verified?
- Is the exchange-rate quote window explicit?

Evidence requirements:

- payment processor support docs;
- refund/underpay/overpay policy excerpt;
- accounting/export needs.

## Output schema

```yaml
report_schema: stablecoin_payment_preflight_v1
read_only: true
non_custodial: true
asset: string
network: string
payment_context: string
risk_summary:
  transferability: low|medium|high|unknown
  gas_pending: low|medium|high|unknown
  scam_self_custody: low|medium|high|unknown
  peg_reserve_regulatory: low|medium|high|unknown
  merchant_exception: low|medium|high|unknown
findings:
  - id: string
    title: string
    severity: 1-5
    evidence:
      - url: string
        excerpt: string
        captured_at: string
    user_safe_next_step: string
    prohibited_actions:
      - string
next_validation:
  - string
```

## First dry-run category candidates

- USDC/USDT merchant checkout on EVM chain;
- freelancer/supplier stablecoin payment after exchange buy;
- wallet pending payment diagnosis;
- merchant underpay/overpay/refund exception report.

## Required disclaimer

This report is informational and read-only. It does not execute transactions, custody assets, bypass platform controls, or provide legal/tax/investment advice.
