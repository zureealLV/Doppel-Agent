# Doppel Agent local load report

- Version / commit:
- Date (UTC):
- OS / CPU / memory:
- Python:
- Configuration: `max_active`, queue capacity, job count, synthetic delay

## Required evidence

| Scenario | Denominator | Invariant | Result | Raw report field |
|---|---:|---|---|---|
| Bounded burst | 100 submissions | peak active <= configured maximum | pending | `bounded_burst` |
| Queue saturation | 3 submissions | overflow is explicitly rejected | pending | `queue_rejection` |
| Workspace contention | 20 reads + 5 writes | writer peak = 1; no read/write overlap | pending | `workspace_contention` |
| Running cancellation | 1 running task | terminal status is cancelled | pending | `running_cancellation` |

Provider 429, MCP disconnect, slow SSE clients, process-tree cleanup, and restart lease recovery
must be reported separately. Do not infer those results from this local scheduler probe.
