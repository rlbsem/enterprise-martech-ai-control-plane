# Recorded incident timelines

Protocol times identify scheduled injection boundaries. Receiver journals identify actual service behavior; queue samples and independent ledger comparisons identify drain and reconciliation. The backlog alert can already be firing before the provider fault because offered load exceeds capacity. All effects are local and synthetic.

## run-0: healthy

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T00:55:14.330+00:00 | 2.985 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T00:57:50.992+00:00 | 159.647 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T00:59:41.986+00:00 | 270.641 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T00:59:46.180+00:00 | 274.835 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T01:00:15.725+00:00 | 304.381 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-0/observations.json.gz) · [Protocol](runs/run-0/protocol.json) · [Results](runs/run-0/summary.json)

## run-1: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T01:00:23.964+00:00 | 1.753 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:00:52.211+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T01:00:52.216+00:00 | 30.005 | Affected request rejected | 14 HTTP 429 and 15 HTTP 503 responses |
| 2026-10-24T01:00:52.311+00:00 | 30.100 | Healthy work continues | 291 successful healthy POSTs during the provider fault |
| 2026-10-24T01:00:57.994+00:00 | 35.783 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T01:01:52.211+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T01:01:53.602+00:00 | 91.390 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T01:04:53.077+00:00 | 270.865 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:04:57.276+00:00 | 275.065 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T01:05:25.705+00:00 | 303.493 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-1/observations.json.gz) · [Protocol](runs/run-1/protocol.json) · [Results](runs/run-1/summary.json)

## run-2: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T01:05:36.259+00:00 | 3.805 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:06:02.454+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T01:06:02.517+00:00 | 30.063 | Affected request rejected | 17 HTTP 429 and 15 HTTP 503 responses |
| 2026-10-24T01:06:02.555+00:00 | 30.101 | Healthy work continues | 298 successful healthy POSTs during the provider fault |
| 2026-10-24T01:06:03.939+00:00 | 31.485 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T01:07:02.454+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T01:07:04.326+00:00 | 91.872 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T01:10:17.868+00:00 | 285.414 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:10:23.069+00:00 | 290.614 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T01:10:35.471+00:00 | 303.017 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-2/observations.json.gz) · [Protocol](runs/run-2/protocol.json) · [Results](runs/run-2/summary.json)

## run-3: mixed

| UTC | Seconds from start | Event | Evidence |
|---|---:|---|---|
| 2026-10-24T01:10:45.019+00:00 | 3.013 | Backlog alert firing | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:11:12.006+00:00 | 30.000 | Fault scheduled | mixed |
| 2026-10-24T01:11:12.078+00:00 | 30.072 | Healthy work continues | 281 successful healthy POSTs during the provider fault |
| 2026-10-24T01:11:12.125+00:00 | 30.119 | Affected request rejected | 18 HTTP 429 and 13 HTTP 503 responses |
| 2026-10-24T01:11:22.962+00:00 | 40.956 | Shared request budget reached | 10 issued permits in a fixed epoch-second bucket |
| 2026-10-24T01:12:12.006+00:00 | 90.000 | Fault injection ends | Scheduled boundary; recovery still needs observation |
| 2026-10-24T01:12:13.611+00:00 | 91.605 | Affected service accepts work again | Independently journaled successful POST |
| 2026-10-24T01:15:15.788+00:00 | 273.782 | Backlog alert cleared | Authenticated local receiver; exact content independently read back |
| 2026-10-24T01:15:17.901+00:00 | 275.895 | Admitted backlog drained | No pending jobs in the queue sample |
| 2026-10-24T01:15:45.594+00:00 | 303.588 | Independent ledgers reconciled | safe_to_resume=true; exact healthy, affected and legacy reader scope |

[Observations](runs/run-3/observations.json.gz) · [Protocol](runs/run-3/protocol.json) · [Results](runs/run-3/summary.json)
