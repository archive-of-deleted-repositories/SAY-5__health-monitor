# Alerting

This service does **not** send notifications. Alerting integration is
intentionally outside the scope (see README "What this is not"). The
audit table is the integration point.

## Tailing recoveries

```bash
health-monitor recoveries --since 1h --json
```

Pipe to `jq` for filtering:

```bash
health-monitor recoveries --since 24h --json \
  | jq '.[] | select(.exit_code != 0)'
```

## SQL access

```sql
-- Recent failures
SELECT
  e.name,
  ra.fired_at,
  ra.action_kind,
  ra.exit_code,
  ra.duration_ms
FROM recovery_actions ra
JOIN endpoints e ON ra.endpoint_id = e.id
WHERE ra.fired_at >= datetime('now', '-1 day')
ORDER BY ra.fired_at DESC;
```

## Wiring an external alerter

A small sidecar script can poll the audit table and forward to the
alerter of your choice. Pseudocode:

```python
last_seen = max_id_or_zero()
while True:
    new = sqlite.fetch("SELECT * FROM recovery_actions WHERE id > ?", last_seen)
    for row in new:
        if row["exit_code"] != 0:
            slack.post(row)
        last_seen = row["id"]
    sleep(30)
```

Keeping this in a separate process means a wedged Slack token can
never break the recovery loop.
