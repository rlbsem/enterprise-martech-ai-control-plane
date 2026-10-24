# Recorded incident timelines

Protocol times identify scheduled injection boundaries. Receiver journals identify actual service behavior; queue samples and independent ledger comparisons identify drain and reconciliation. The backlog alert can already be firing before the provider fault because offered load exceeds capacity. All effects are local and synthetic.

## run-0: healthy

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T16:45:41.484+00:00 | 2.113 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T16:45:42.939+00:00 | 3.568 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T16:49:58.269+00:00 | 258.898 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T16:50:02.424+00:00 | 263.054 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T16:50:44.452+00:00 | 305.081 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-0/observations.json.gz) · [Protocol](runs/run-0/protocol.json) · [Results](runs/run-0/summary.json)

## run-1: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T16:50:56.794+00:00 | 3.000 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T16:51:16.938+00:00 | 23.145 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T16:51:23.794+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T16:51:23.821+00:00 | 30.027 | Healthy work continues | 296 successful healthy POSTs during the provider fault |
| 2026-10-24T16:51:24.029+00:00 | 30.235 | Affected request rejected | 24 HTTP 429 and 24 HTTP 503 responses |
| 2026-10-24T16:52:23.794+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T16:52:25.580+00:00 | 91.786 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T16:55:13.664+00:00 | 259.870 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T16:55:15.753+00:00 | 261.960 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T16:55:57.678+00:00 | 303.884 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-1/observations.json.gz) · [Protocol](runs/run-1/protocol.json) · [Results](runs/run-1/summary.json)

## run-2: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T16:56:07.406+00:00 | 2.053 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T16:56:11.898+00:00 | 6.546 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T16:56:35.353+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T16:56:35.353+00:00 | 30.001 | Healthy work continues | 274 successful healthy POSTs during the provider fault |
| 2026-10-24T16:56:35.467+00:00 | 30.115 | Affected request rejected | 24 HTTP 429 and 22 HTTP 503 responses |
| 2026-10-24T16:57:35.353+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T16:57:36.887+00:00 | 91.534 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T17:00:24.512+00:00 | 259.159 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T17:00:26.606+00:00 | 261.253 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T17:01:09.378+00:00 | 304.025 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-2/observations.json.gz) · [Protocol](runs/run-2/protocol.json) · [Results](runs/run-2/summary.json)

## run-3: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T17:01:20.128+00:00 | 3.232 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T17:01:23.925+00:00 | 7.030 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T17:01:46.895+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T17:01:46.915+00:00 | 30.020 | Affected request rejected | 24 HTTP 429 and 24 HTTP 503 responses |
| 2026-10-24T17:01:47.017+00:00 | 30.122 | Healthy work continues | 292 successful healthy POSTs during the provider fault |
| 2026-10-24T17:02:46.895+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T17:02:48.187+00:00 | 91.291 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T17:05:35.736+00:00 | 258.841 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T17:05:38.858+00:00 | 261.963 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T17:06:20.850+00:00 | 303.955 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-3/observations.json.gz) · [Protocol](runs/run-3/protocol.json) · [Results](runs/run-3/summary.json)
