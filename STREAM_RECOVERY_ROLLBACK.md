# PumpPortal stream recovery: rollback guard

The `processed_market_events.route_state` migration is additive. It does not
make older builds understand unfinished stream events. Before rolling back to
a build without `stream_event_recovery_worker`, stop stream ingestion and check:

```sql
SELECT route_state, COUNT(*)
FROM processed_market_events
WHERE source = 'live' AND route_state IN ('reserved', 'routing', 'uncertain')
GROUP BY route_state;
```

Do not roll back while this returns rows. `reserved` can be replayed by the new
build without live orders. `routing` and `uncertain` need manual reconciliation
against trades, positions, and execution orders; never blindly delete or replay
their identities. An old build will see the identities as processed and skip
them. Restoring the new build preserves the recovery worker and its alerts.
