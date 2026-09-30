# Zava Observability as Code

Public, secret-free automation for the observability act in Microsoft Ignite
session BRK427. Zava already had component-level monitoring on-premises; this
repository consolidates that inherited visibility into one Azure operating
model across RHEL, SLES, Ubuntu, and Rocky Linux.

The opening dashboard answers one question:

> **Are shipments moving fast enough?**

It begins with arrivals, completions, backlog, oldest work, and latency. Host
metrics and Inspektor Gadget appear only when they help explain why the business
flow changed.

This is observability convergence, not greenfield monitoring. Existing host,
database, and application checks answer whether individual components are
running. The Azure layer connects those signals to the service-level question,
preserves the estate's operational history, and adds the business-flow evidence
that was previously missing.

The larger demonstration is deliberately practical rather than promotional:
Azure accepts the inherited distribution choices, gives the mixed estate a
coherent operational view, provides managed investigation surfaces, and makes
Azure Linux a natural capacity extension instead of a forced replacement.

## Design

Ansible is the operator-facing entry point and the cross-distro deployment
engine. It invokes a small Bicep template only for Azure control-plane resources,
where Azure Resource Manager is the native idempotent API.

```text
Zava SQL state ──> Zava exporter ─┐
                                  │
Linux hosts ─────> node_exporter ─┼─> private Prometheus collector
                                  │          │
Inspektor Gadget ─> bounded trace ┘          │ managed identity
                                             v
                                  Azure Monitor Workspace
                                             │
                                             v
                                  Azure Managed Grafana
```

The collector runs on the private administration VM and remote-writes to Azure
Monitor with its system-assigned managed identity. Azure Managed Grafana reads
the Azure Monitor Workspace. No service-principal secret, Grafana token, SQL
password, SSH private key, or Azure credential is stored here.

The four application distributions remain visible throughout the dashboard and
investigation. Azure is the unifying operational layer, not an excuse to erase
the estate's history.

## Two playbooks

### Before the session

`playbooks/pre-session.yml` performs the slow and potentially failure-prone work:

- Deploys Azure Monitor Workspace, Prometheus ingestion DCE/DCR, and Azure
  Managed Grafana through Bicep.
- Enables the collector VM's system-assigned managed identity.
- Grants only `Monitoring Metrics Publisher` on the Prometheus DCR.
- Installs node_exporter on the four workload VMs.
- Installs the Zava business exporter on SLES.
- Installs Prometheus on the private admin VM.
- Configures managed-identity remote write.
- Installs Inspektor Gadget on the workload VMs.
- Publishes the Grafana dashboard.
- Requires every Prometheus target to be healthy.

### During the session

`playbooks/live-session.yml` launches one bounded evidence capture after the
business dashboard shows a problem. Every capture:

- Runs through a transient systemd unit.
- Has a 30–600 second timeout.
- Stops any prior Zava capture before starting.
- Writes to the guest journal.
- Has a deterministic `evidence=stop` path.

Examples:

```bash
# Worker cannot connect to SQL.
ansible-playbook playbooks/live-session.yml \
  -e evidence=tcp \
  -e live_target=zava_workers \
  -e duration=180

# Capture process termination and restart evidence.
ansible-playbook playbooks/live-session.yml \
  -e evidence=process \
  -e live_target=zava_workers

# Investigate SQL block-device latency.
ansible-playbook playbooks/live-session.yml \
  -e evidence=blockio \
  -e live_target=zava_database

# Stop all Zava evidence captures.
ansible-playbook playbooks/live-session.yml \
  -e evidence=stop \
  -e live_target=zava_workloads
```

## Authentication and secrets

The repository deliberately contains no credential mechanism.

| Boundary | Authentication used |
|---|---|
| Azure control plane | Existing interactive `az login` session |
| Prometheus remote write | Collector VM system-assigned managed identity |
| Azure Managed Grafana CLI | Current Microsoft Entra identity |
| Linux hosts | SSH agent or user-selected SSH configuration |
| SQL Server | Existing `/etc/shipment/shipment.env` on the API VM |

The exporter service reads the protected application environment file directly
on the API VM. Ansible never reads, registers, prints, copies, or templates its
contents.

Do not put any of the following in inventory or `group_vars`:

- Passwords
- API keys
- Presenter or load-generator keys
- SSH private keys
- Grafana service-account tokens
- Azure client secrets

## Prerequisites

Controller:

- Azure Linux 4 on WSL is the recommended control node
- Ansible Core 2.21 or later
- Azure CLI 2.75 or later
- Bicep support in Azure CLI
- An interactive Azure login with permission to deploy resources and role
  assignments
- SSH access to the private estate, normally through the admin VM or VPN

Targets:

- The five Zava VMs running and reachable privately
- Existing Zava application installation
- `/etc/shipment/shipment.env` on `sles-api`
- Linux kernel 5.4 or newer for the selected Inspektor Gadget traces

## Configure

From Windows, launch the Azure Linux 4 control node:

```powershell
wsl -d AzureLinux-4
```

Create the Python environment and install the controller dependencies:

```bash
sudo dnf install -y python3-pip git openssh-clients azure-cli
cd /mnt/c/Users/<windows-user>/ignite-2026-zava-observability
python3 -m venv .venv
source .venv/bin/activate
pip install "ansible-core>=2.21,<2.22" "ansible-lint>=26,<27"
```

Azure Linux 4 currently uses Python 3.14, which requires Ansible Core 2.21 or
later. Its WSL image does not currently include ICU, so configure .NET
invariant globalization before installing the Bicep CLI:

```bash
echo 'export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1' >> ~/.bashrc
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
az bicep install
```

When the repository is stored under `/mnt/c`, explicitly select its
configuration file because Ansible treats Windows-mounted directories as
world-writable:

```bash
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
```

Install collections:

```bash
ansible-galaxy collection install -r requirements.yml
```

Copy the example inventory. The production directory is ignored by Git:

```bash
cp -R inventory/example inventory/production
```

Edit only non-secret coordinates in:

```text
inventory/production/hosts.yml
inventory/production/group_vars/all.yml
```

Use the production inventory:

```bash
ansible-playbook \
  -i inventory/production/hosts.yml \
  playbooks/pre-session.yml
```

The first managed-identity role assignment can take up to 30 minutes to
propagate. Prometheus may log HTTP 403 responses during that interval even when
the configuration is correct.

## Metrics

The business exporter publishes:

- Shipment count by status
- Queue depth
- Oldest pending work age
- Total work arrivals and completions
- Backlog growth rate
- Queue latency p50, p95, and p99
- Work outside the configured age SLO
- Retry and retained-error counts
- Scan events by worker, site, and scan type
- Status transitions
- Tier heartbeat age

Prometheus also collects CPU, memory, filesystem, disk, network, process, and
systemd metrics from each Linux VM.

## Safety boundaries

- The pre-session playbook does not start, stop, or reset the Zava application.
- The live-session playbook does not inject or repair failures.
- Inspektor Gadget captures are time bounded.
- Metrics ports are allowed only from the private collector where firewalld or
  UFW is active.
- Grafana and Azure resources contain no application credentials.
- Production inventory and generated Azure coordinates are ignored by Git.

See [`docs/rehearsal.md`](docs/rehearsal.md) before relying on the playbooks on
stage.
