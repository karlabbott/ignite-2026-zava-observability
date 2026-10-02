[CmdletBinding()]
param(
    [string]$SubscriptionId = "1a0e0107-9711-458a-aed9-a21d2797109a",
    [string]$ResourceGroup = "rg-zava-rehearsal-wus2",
    [string]$AdminVm = "zava-cutover-admin",
    [string]$RemoteRepository = "https://github.com/karlabbott/ignite-2026-zava-observability.git",
    [string]$RemoteRoot = "/opt/zava-observability"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$requiredFiles = @(
    (Join-Path $repositoryRoot "inventory\production\admin-hosts.yml"),
    (Join-Path $repositoryRoot "inventory\production\group_vars\all.yml"),
    (Join-Path $repositoryRoot "generated\azure.yml")
)

foreach ($path in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required deployment file is absent: $path"
    }
}

function Invoke-AzureRunCommandFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$SuccessMarker
    )

    $result = az vm run-command invoke `
        --subscription $SubscriptionId `
        --resource-group $ResourceGroup `
        --name $AdminVm `
        --command-id RunShellScript `
        --scripts "@$Path" `
        --only-show-errors `
        --output json | ConvertFrom-Json

    $message = ($result.value | ForEach-Object message) -join "`n"
    Write-Host $message
    if ($message -notmatch [regex]::Escape($SuccessMarker)) {
        throw "Private deployment step failed before marker '$SuccessMarker'."
    }
}

function Write-LfFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$Content
    )

    [IO.File]::WriteAllText(
        $Path,
        $Content.Replace("`r`n", "`n"),
        [Text.UTF8Encoding]::new($false)
    )
}

$commit = (git -C $repositoryRoot rev-parse HEAD).Trim()
if (-not $commit) {
    throw "Could not resolve the local Git commit."
}

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) {
    throw "Could not select Azure subscription $SubscriptionId."
}

$temporaryFiles = @()
try {
    $setupPath = [IO.Path]::GetTempFileName()
    $temporaryFiles += $setupPath
    Write-LfFile -Path $setupPath -Content @"
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv git
if [ ! -d '$RemoteRoot/.git' ]; then
  git clone --quiet '$RemoteRepository' '$RemoteRoot'
fi
git -C '$RemoteRoot' fetch --quiet origin
git -C '$RemoteRoot' checkout --quiet --force --detach '$commit'
python3 -m venv '$RemoteRoot/.venv'
'$RemoteRoot/.venv/bin/pip' install --quiet --upgrade pip
'$RemoteRoot/.venv/bin/pip' install --quiet ansible-core==2.21.4
HOME=/home/estate '$RemoteRoot/.venv/bin/ansible-galaxy' collection install \
  -r '$RemoteRoot/requirements.yml' --force >/dev/null
chown -R estate:estate '$RemoteRoot' /home/estate/.ansible
echo ZAVA_PRIVATE_CONTROLLER_READY
"@
    Invoke-AzureRunCommandFile -Path $setupPath -SuccessMarker "ZAVA_PRIVATE_CONTROLLER_READY"

    $adminInventory = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes(
            [IO.File]::ReadAllText($requiredFiles[0])
        )
    )
    $groupVariables = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes(
            [IO.File]::ReadAllText($requiredFiles[1])
        )
    )
    $generatedVariables = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes(
            [IO.File]::ReadAllText($requiredFiles[2])
        )
    )

    $stagePath = [IO.Path]::GetTempFileName()
    $temporaryFiles += $stagePath
    Write-LfFile -Path $stagePath -Content @"
set -eu
mkdir -p '$RemoteRoot/inventory/production/group_vars' '$RemoteRoot/generated'
printf '%s' '$adminInventory' | base64 -d > '$RemoteRoot/inventory/production/admin-hosts.yml'
printf '%s' '$groupVariables' | base64 -d > '$RemoteRoot/inventory/production/group_vars/all.yml'
printf '%s' '$generatedVariables' | base64 -d > '$RemoteRoot/generated/azure.yml'
chown -R estate:estate '$RemoteRoot/inventory/production' '$RemoteRoot/generated'
echo ZAVA_PRIVATE_COORDINATES_READY
"@
    Invoke-AzureRunCommandFile -Path $stagePath -SuccessMarker "ZAVA_PRIVATE_COORDINATES_READY"

    $deployPath = [IO.Path]::GetTempFileName()
    $temporaryFiles += $deployPath
    Write-LfFile -Path $deployPath -Content @"
set -eu
cd '$RemoteRoot'
log=/home/estate/zava-observability-pre-session.log
if sudo -u estate -H .venv/bin/ansible-playbook \
    -i inventory/production/admin-hosts.yml \
    playbooks/private-estate.yml \
    -e @generated/azure.yml > "`$log" 2>&1; then
  tail -n 120 "`$log"
else
  rc=`$?
  tail -n 200 "`$log"
  exit "`$rc"
fi
echo ZAVA_PRIVATE_OBSERVABILITY_READY
"@
    Invoke-AzureRunCommandFile -Path $deployPath -SuccessMarker "ZAVA_PRIVATE_OBSERVABILITY_READY"
}
finally {
    foreach ($path in $temporaryFiles) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}
