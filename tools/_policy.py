"""Shared loading + maps for the gateway policy (dogfood model-services schema).

A model-service (v2 AI Gateway endpoint) is a Unity Catalog securable. Its config:

    config:
      destinations:     [{name, pay_per_token_config:{model:"models/..."}, traffic_percentage, type}]
      service_policies: [{name, handler, policy_type, rank, options:{...}}]   # guardrails unified here
      tracing:          {enabled}
      usage_tracking:   {enabled}

Each service_policy's handler:
  custom    -> system.ai.invoke_llm_judge  (options.instruction = prompt, options.action)
  pii       -> system.ai.mask_pii          (native)
  jailbreak -> system.ai.block_jailbreak   (native)

`load()` imports PyYAML lazily so the pure functions stay unit-testable without it.
"""
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parent.parent / "gateway-policy"

# guardrail type -> service-policy handler
HANDLER = {
    "custom": "system.ai.invoke_llm_judge",
    "pii": "system.ai.mask_pii",
    "jailbreak": "system.ai.block_jailbreak",
}
# spec phase -> API phase token
PHASE = {"input": "pre_call", "output": "post_call"}


def load(policy_dir=POLICY_DIR):
    """Load the policy tree into a dict. Requires PyYAML."""
    import yaml

    policy_dir = Path(policy_dir)
    guardrails = {}
    for f in sorted((policy_dir / "guardrails").glob("*.yaml")):
        g = yaml.safe_load(f.read_text())
        guardrails[g["name"]] = g
    endpoints = {}
    for f in sorted((policy_dir / "endpoints").glob("*.yaml")):
        e = yaml.safe_load(f.read_text())
        endpoints[e["name"]] = e
    reg = policy_dir / "exceptions" / "register.yaml"
    exemptions = []
    if reg.exists():
        exemptions = (yaml.safe_load(reg.read_text()) or {}).get("exemptions", [])
    prompts = {}
    for f in sorted((policy_dir / "prompts").glob("*.md")):
        prompts[f"prompts/{f.name}"] = f.read_text()
    return {
        "guardrails": guardrails,
        "endpoints": endpoints,
        "exemptions": exemptions,
        "prompts": prompts,
    }
