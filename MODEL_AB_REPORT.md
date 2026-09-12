# Chat model A/B result

Date: 2026-09-12. This was a measurement-only OpenRouter run: 15 identical
request scenarios per candidate (10 short neutral Russian replies and 5 calls
to a synthetic read-only tool schema). No Noema tool was executed, no database
or user setting was changed during the run, and no request or response text was
written to the report.

| Candidate | Provider constraint | Success | TTFT p50 / p95 | Total p50 / p95 | Tool calls | Russian replies | Measured cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5-Flash | Alibaba only | 15 / 15 | 884.8 / 1101.7 ms | 1297.3 / 1480.1 ms | 5 / 5 | 10 / 10 | $0.00042471 |
| DeepSeek V3.2 | StreamLake, then DeepInfra | 15 / 15 | 1530.5 / 1970.9 ms | 2346.4 / 2983.7 ms | 5 / 5 | 10 / 10 | $0.00041328 |

Qwen met the decision rule: its tool calls were stable and its median first
token was 645.7 ms faster; median completion was 1049.1 ms faster. It is now
the configured normal chat/voice default. DeepSeek V3.2 is the model fallback.

The router adds OpenRouter provider preferences only for these two model IDs:

- Qwen: `only/order = [alibaba]`, no provider fallback;
- DeepSeek: `only/order = [streamlake, deepinfra]`, provider fallback enabled.

No deployment occurred as part of this result. A production process will use
the configuration only after its environment is updated and it is redeployed.
