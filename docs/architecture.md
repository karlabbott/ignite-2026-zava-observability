# Architecture

## Narrative intent

Zava did not arrive in Azure without monitoring. Its on-premises environment
already had infrastructure, database, and application checks, but those signals
were divided by team and tool. They could establish whether a component was
running without reliably answering whether shipments were moving through the
complete service fast enough.

The migration is therefore the point of convergence rather than the start of
observability. This package preserves the inherited operational view, maps the
mixed estate into a common Azure model, and adds the missing business-flow
metrics and agentic investigation path.

The observability act should demonstrate, without requiring a product slogan,
that Azure is an unusually strong place to operate Linux:

- Existing RHEL, SLES, Ubuntu, and Rocky workloads remain intact.
- Azure Monitor turns them into one measurable application estate.
- Azure Managed Grafana connects business flow to platform and guest evidence.
- Managed identity removes operational credentials from the telemetry path.
- GitHub Copilot can move from symptom to evidence, action, and validation.
- Azure Linux can be introduced as additional capacity without displacing the
  inherited distributions.

The audience should reach that conclusion from the evidence rather than being
asked to accept it as a claim.

## Why Ansible plus Bicep

Ansible owns the story:

- The same playbook configures RHEL, SLES, Ubuntu, and Rocky Linux.
- Roles are readable and portable to other VM estates.
- The live action is an explicit, reviewable automation step.

Bicep is limited to resources whose idempotent contract is Azure Resource
Manager:

- Azure Monitor Workspace
- Prometheus Data Collection Endpoint and Data Collection Rule
- Azure Managed Grafana
- Grafana-to-workspace integration and data-reader authorization

This keeps Azure resource schema details out of shell commands without turning
the session into an infrastructure-template demonstration.

## Durable metrics path

The API VM hosts a read-only exporter process that uses the existing
`shipping_app` SQL identity from `/etc/shipment/shipment.env`. It reads current
application state and publishes Prometheus metrics on the private subnet.

Every workload VM runs node_exporter. The private admin VM runs Prometheus and
scrapes the business and host exporters every ten seconds. Prometheus 3.15 uses
the admin VM's system-assigned identity to remote-write directly to the Azure
Monitor ingestion DCR.

Azure Managed Grafana is integrated with the Azure Monitor Workspace. Its
managed identity receives only monitoring read access.

## Live evidence path

Inspektor Gadget is installed but does not run a permanent collection workload.
The live playbook launches a selected gadget only after higher-level evidence
narrows the investigation:

| Evidence | Target | Intended use |
|---|---|---|
| `tcp` | Rocky worker | Failed worker-to-SQL connection attempts |
| `process` | Rocky worker | Worker termination and restart |
| `blockio` | RHEL database | SQL storage-device latency |
| `dns` | Affected tier | DNS request and response evidence |

Raw events stay in journald. This avoids high-cardinality eBPF data in the
manager dashboard and keeps the causal reveal readable.

## Business-first dashboard

The top row answers whether shipments are moving:

1. Current backlog
2. Oldest pending work
3. Work arrivals per minute
4. Work completions per minute
5. Queue latency p95
6. Work outside SLO

The second row shows flow over time and shipment status. The final row explains
capacity through worker contribution, tier heartbeat age, and host CPU.
