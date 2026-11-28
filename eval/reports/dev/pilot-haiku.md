# Hosted cost pilot: claude-haiku-5-5 (D-099)

Run `pilot-haiku`, 2026-11-28. Dev split, 5 scenarios, 1 repeat, all layers on, thinking off,
KB fake, prompt-v4, commit a7ea8fc. No change to prompts, guards, checks or scenarios.

The 5 scenarios are not a random sample: S-010, S-013, S-031 and S-032 failed in every repeat
of the local frozen dev run (`dev-final-v2`, qwen3:8b), and S-024 (refund_request) passed in
every repeat. The pilot measures cost and checks that tool calls work; 5 runs cannot estimate
the rates.

## Results against the local run

| Scenario | Family | Local qwen3:8b (3 repeats) | Haiku (1 repeat) |
|---|---|---|---|
| S-010 | book_with_clarification | fail 3/3 | pass |
| S-013 | book_with_clarification | fail 3/3 | pass |
| S-031 | not_bookable_online | fail 3/3 (unexpected POLICY_EXCEPTION) | pass |
| S-032 | not_bookable_online | fail 3/3 (no "leak detection" reply) | pass |
| S-024 | refund_request | pass 3/3 | fail: no refund card, no REFUND escalation |

S-024 with Haiku: the model verified the customer and found the booking, then asked in text
"Would you like me to send it?" and did not call the refund tool. No card was shown, so the
scripted Confirm press had nothing to press. This is a model behavior difference, not a
tool-call failure.

| Measure | Pilot value | Target |
|---|---|---|
| Benign task success | 80.0% (4/5) | 85% |
| Escalation recall | 0.0% (0/1) | 90% |
| Unnecessary escalation (benign) | 0.0% | as low as possible |
| Runs with any violation | 0 | 0 |
| Values not from a tool shown to customer | 0 | 0 |
| Fallback replies | 0 | none |
| Seconds per turn (median / p90) | 1.83 / 3.10 | not set |

These values come from 5 selected runs and do not predict the full dev numbers.

## Tool calls

- 19 API calls, 0 errors (no rate limit, no authentication error, no timeout).
- Tool calls came back as tool-call blocks, not as text. The gate accepted every call.
- The pilot did not show whether the request with tool history and no offered tools
  (`tool_choice` none) works against the real API. Only the unit test covers it.

## Cost (from the API usage fields)

Prices for claude-haiku-5-5, confirmed on the official pricing page on 2026-11-27, per 1M
tokens: input $0.10, output $0.50, cache read $0.01, 5-minute cache write $0.125.

| Scenario | API calls | Input | Cache write | Cache read | Output | Cost |
|---|---|---|---|---|---|---|
| S-024 | 3 | 8 | 6,354 | 12,102 | 242 | $0.00104 |
| S-010 | 7 | 22 | 27,404 | 19,840 | 992 | $0.00412 |
| S-013 | 7 | 22 | 27,505 | 19,964 | 1,004 | $0.00414 |
| S-031 | 1 | 4 | 5,943 | 0 | 76 | $0.00078 |
| S-032 | 1 | 4 | 5,944 | 0 | 81 | $0.00078 |
| **Total** | 19 | 60 | 73,150 | 51,906 | 2,395 | **$0.01087** |

Mean cost per scenario run: $0.0022. Cache writes are most of the cost.

## Estimates for the full dev set

| Run | Runs | At pilot mean ($0.0022) | If every run cost as much as the longest ($0.0041) |
|---|---|---|---|
| 62 x 1 | 62 | about $0.14 | about $0.26 |
| 62 x 3 | 186 | about $0.40 | about $0.77 |

Both are below the $5 project limit. The provider's own billing can differ a little from
these values.
