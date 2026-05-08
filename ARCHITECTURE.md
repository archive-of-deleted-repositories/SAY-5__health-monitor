# Architecture

## Failure-streak design: why N-consecutive instead of rate-based?

A rate-based threshold ("three failures in five minutes") sounds more
forgiving on paper but produces two consistent failure modes in
production:

1. **False positives on noise.** If your endpoint has a 1% baseline
   error rate from upstream blips, a five-minute window will regularly
   accumulate three failures without the service ever being down for a
   sustained period. Recovery fires, your service flaps, your audit log
   becomes useless.
2. **Recovery loops.** A rate window keeps a hot counter for the whole
   window. If the recovery itself takes 30s, the counter is already
   poisoned when the service comes back, and the next failure can fire
   recovery again immediately.

`N-consecutive` resets the counter on **any** success, so:

- A genuine outage walks the counter to N quickly and fires once.
- A flaky endpoint never reaches N because it intersperses successes.
- After firing, the counter is reset to zero. The next recovery
  requires N more consecutive failures, so the worst case is one
  recovery per (N x check_interval_s) window.

The tradeoff is that if your check interval is 10s and N=3, you wait
30s before recovery. That is the right tradeoff for an
auto-restart loop: you want it slower than human reaction time when
the situation is ambiguous, and you want it deterministic in the
audit log.

## Recovery hook Protocol

`RecoveryHook` is a `typing.Protocol` exposing `kind: str` and
`async def run() -> RecoveryResult`. Three implementations ship:

| Hook | Implementation |
|---|---|
| `BashHook` | `asyncio.create_subprocess_exec` with `timeout`, captured stdout/stderr |
| `SystemctlHook` | `systemctl restart <unit>` on Linux; explicit error on macOS |
| `NoopHook` | Records a row but does nothing; used by `simulate` and dry-run configs |

Adding a new hook (e.g. `kubectl rollout restart deployment/x`) is one
file: implement the Protocol, hand it back from `build_hook` for the
right `RecoveryKind`. The dispatcher itself does not change.

The dispatcher is the only place that calls a hook and the only place
that writes a `recovery_actions` row, so the audit log can never
disagree with what actually ran.

## Audit-log design: why every recovery is durable

`recovery_actions` is the single source of truth for "what happened?".
The columns capture:

| Column | Why |
|---|---|
| `endpoint_id` (FK) | Which endpoint triggered |
| `fired_at` | UTC timestamp; sortable, tz-aware |
| `action_kind` | bash / systemctl / noop / future |
| `action_args` (JSON) | Script path, unit name, args - reproducible |
| `exit_code` | 0 / non-zero / 124 (timeout) / 126 (perm) / 127 (missing) |
| `stdout_excerpt` | Up to 4 KiB; what the script said |
| `stderr_excerpt` | Up to 4 KiB; what went wrong |
| `duration_ms` | Triage budget |

Truncation prevents a chatty script from blowing up the table. Storing
the args separately from the kind means a future migration that
renames a script field doesn't break historical rows.

## Async loop with graceful shutdown

`run_loop` spawns one `asyncio.Task` per active endpoint. Each task is
a `while not stop.is_set(): poll, store, sleep(interval)`.

The `stop` Event is set in two cases:

- A `SIGTERM` or `SIGINT` arrives (registered via `loop.add_signal_handler`).
- The optional `duration_s` argument elapses (used by tests).

On shutdown, every task is cancelled and `await gather(..., return_exceptions=True)` drains any in-flight probes cleanly. The
shared `httpx.AsyncClient` is closed via its `async with`, so no
sockets leak.

## What's deliberately not here

- **Distributed coordination.** Two monitors against one fleet means
  two recoveries per failure. Run a single instance, or implement a
  lease layer (etcd, consul) before you scale out.
- **Backoff between recoveries.** The current contract is "fires when
  the streak hits N, then resets". A future version could grow an
  exponential cooldown per endpoint, but that adds a knob and the
  current behavior is simpler to reason about.
- **HTTPS verification options.** `httpx`'s defaults are used as-is
  (verify=True). Endpoints with self-signed certs need a small patch.
- **Persistent streak counters.** The streak detector lives in memory.
  A restart resets all counters, which is the *safer* default
  (nothing fires immediately on startup).
