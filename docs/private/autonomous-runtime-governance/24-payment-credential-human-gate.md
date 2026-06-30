# Payment / Credential Human Gate

Status: human-secret gate only.

The user authorized the payment/credential boundary as a high-risk class, but the agent must not type or store secrets. This boundary is therefore covered by a human-side execution protocol rather than agent-side credential entry.

## What the agent may do

1. Prepare the target page/app instructions.
2. Explain the required fields and risks.
3. Wait while the human enters credentials/payment details directly.
4. Record a redacted receipt after the human confirms completion.
5. Verify non-secret readback such as status text, transaction state, or confirmation ID if visible and non-sensitive.

## What the agent must not do

- Type passwords, API keys, cards, OTPs, seed phrases, private keys, or recovery codes.
- Ask the user to paste secrets into chat.
- Store raw credential/payment values in files, logs, receipts, memory, screenshots, or prompts.
- Use broad autofill or hidden credential extraction.

## Receipt schema

```json
{
  "boundary": "payment_credential_entry",
  "human_entered_secret": true,
  "agent_typed_secret": false,
  "secret_value_stored": false,
  "target": "<exact service/page>",
  "confirmation_ref": "<non-secret confirmation/status>",
  "readback": "<non-secret status>"
}
```

Current run: no payment or credential secret was typed by the agent.
