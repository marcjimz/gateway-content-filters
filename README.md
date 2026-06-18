# Gateway Content Filters

Azure AI Content Safety parity on the **Databricks Unity AI Gateway** for **the customer's** Azure OpenAI → Databricks Foundation Models migration — as **governed config-as-code**.

Covers: Violence / Hate / Self-harm / Sexual (healthcare-tuned), Prompt Shields (jailbreak + indirect), PII/PHI — each **block** or **annotate**, with governance-committee exceptions. Plus **MCP service policies** (agent action governance) as SQL UC functions.

## Config-as-code, for real (the target workspace)

On the target workspace, an AI Gateway endpoint is a **Unity Catalog securable** (`MODEL_SERVICE`) with full CRUD via the public UC API. So the repo is the source of truth and **`apply.py` deploys via API — no UI**:

```bash
pip install -r requirements.txt
python tools/validate.py                                  # CI gate
python tools/render.py ih-guardrail-demo                  # -> model-services API body
python tools/apply.py  diff   ih-guardrail-demo           # repo vs live
python tools/apply.py  apply  ih-guardrail-demo           # create-or-replace via API
```

Guardrails and MCP service policies are **unified** as `config.service_policies[]` on the model-service.

## Layout

```
gateway-policy/
  guardrails/   reusable guardrail defs (type: custom|pii|jailbreak; prompt + action for custom)
  prompts/      judge prompts — the IP (healthcare carve-outs, PHI rules)
  endpoints/    model-services: parent + destinations + judge + tracing + policy bindings (phase, mode)
  exceptions/   committee exemption register (approver, ticket, expiry)
service-policies/ SQL UC functions for MCP action governance (no_destructive_ops, no_egress_tools, no_unsafe_code_exec)
tools/
  render.py     git spec -> model-services API config body
  apply.py      CRUD via the Unity Catalog model-services API (get/create/delete/apply/diff)
  validate.py   CI gate (shields always block, relaxations need non-expired exemptions, schema)
  test_guardrails.py / measure.py / tests/corpus.yaml   runtime behavior tests
docs/design.md · docs/poc-results.md
```

## Requirement → model

| Requirement | Where |
|---|---|
| Violence/Hate/Self-harm/Sexual + severity | `healthcare-safety` (custom judge, prompt) |
| Healthcare-context tolerance | the same prompt's "DO NOT flag" carve-outs |
| Prompt shields, always block | `jailbreak` (native `block_jailbreak`, `always_block`) |
| PII redaction | `pii` (native `mask_pii`) |
| PHI redaction | `phi` (custom judge; no native handler) |
| Block vs annotate | per-binding `mode` → `dry_run` |
| Input/output | per-binding `phases` → `pre_call`/`post_call` |
| Reuse across endpoints | one `guardrails/*.yaml`, referenced by many endpoints |
| Governance exceptions | `exceptions/register.yaml` (approver + ticket + expiry), CI-enforced |
| Agent action governance (MCP) | `service-policies/*.sql` UC functions |

## Status

✅ Config-as-code proven on **the target workspace** (`main.default`): render → API create → verify → delete round-trips. ✅ Service-policy SQL functions deployed. ⚠️ Beta caveats: the public model-services API is the target workspace-only today (customer-prod TBD); native output-`mask_pii` is flaky (bind PII input-only). Judge model + thresholds are documented productionization levers (see `docs/poc-results.md`).
