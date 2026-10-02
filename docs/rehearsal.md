# Rehearsal checklist

The repository can be built and syntax-checked while the estate is deallocated,
but it is not stage-ready until the following checks pass against the running
VMs.

## First deployment

1. Start the private Zava estate.
2. Confirm SSH access to all inventory hosts.
3. Run the pre-session playbook with `--check` where supported.
4. Run the full pre-session playbook.
5. Wait for managed-identity role propagation if Prometheus remote write
   initially receives HTTP 403.
6. Confirm every Prometheus scrape target is `up`.
7. Confirm Zava metrics appear in Azure Monitor PromQL.
8. Confirm the Grafana datasource variable resolves to the integrated Azure
   Monitor Prometheus datasource.
9. Confirm no metrics port is publicly reachable.

## Baseline measurements

Record rather than assume:

- Normal arrivals per minute
- Single-worker completions per minute
- Queue latency p50/p95/p99
- Normal oldest-work age
- Normal tier heartbeat age
- Time for a second worker to change backlog direction

Replace the provisional 10-second queue-latency and 30-second oldest-work
thresholds only after these measurements.

## Failure branches

Before injecting any fault:

- Confirm both entries in `Continuous IG collector health` are green.
- Confirm normal API-to-SQL connection activity is visible.
- Confirm the RHEL database has completed a recent block-I/O profile.

### Silence worker

- Start `evidence=process` before injecting the failure.
- Verify `trace_signal` records the termination.
- Verify worker heartbeat becomes stale.
- Verify completions fall to zero and backlog rises.
- Verify `trace_exec` records the recovery start.
- Verify the dashboard remains healthy for the agreed observation window.

### Cut database route

- Inject the rehearsed route/firewall failure.
- Establish normal continuous API-to-SQL connection evidence before the fault.
- Verify successful SQL connection evidence stops when the route is cut while
  queue age and backlog continue rising in Grafana.
- Do not claim that v0.56.1 reliably emits refused or silently dropped
  connection attempts; use the application symptom and the disappearance of
  successful connection events together.
- Verify the route rollback and sustained business recovery.

### Investigate database latency

- Use `Database block I/O latency observed by IG` as the continuous kernel
  signal; it displays the approximate p95 upper histogram bound from each
  55-second profile.
- Correlate a latency change with queue latency and oldest-work age rather than
  treating disk latency alone as a business incident.
- Use the completed-profile count and collector-health panel to distinguish a
  quiet device from a failed collector.

### Crush with demand

- Verify arrivals exceed single-worker capacity.
- Verify completions remain nonzero and the worker heartbeat remains current.
- Add the Azure Linux worker through the separate scale-out automation.
- Verify both workers contribute scans and backlog direction turns negative.

## Stop and reset

Stop captures:

```bash
ansible-playbook playbooks/live-session.yml \
  -e evidence=stop \
  -e live_target=zava_workloads
```

The observability playbooks intentionally do not reset application disks or
fault state. Use the estate's presentation baseline controls for that boundary.
