[CmdletBinding()]
param(
    [string]$SubscriptionId,
    [string]$ResourceGroup = "rg-zava-rehearsal-wus2",
    [string]$AdminVm = "zava-cutover-admin",
    [string]$RemoteRepository = "https://github.com/karlabbott/ignite-2026-zava-observability.git",
    [string]$RemoteRoot = "/opt/zava-observability",
    [ValidateSet("Full", "Prepare", "Stage")]
    [string]$Phase = "Full"
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

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to execute the private deployment step."
    }
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

$commitOutput = git -C $repositoryRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or -not $commitOutput) {
    throw "Could not resolve the local Git commit."
}
$commit = $commitOutput.Trim()

$implementationChanges = git -C $repositoryRoot status --porcelain -- `
    roles playbooks tools azure dashboards requirements.yml ansible.cfg
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the local implementation."
}
if ($implementationChanges) {
    throw "Commit and push the reviewed implementation before running the private deployment."
}

if (-not $SubscriptionId) {
    $activeSubscription = az account show --query id --output tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or -not $activeSubscription) {
        throw "Could not resolve the active Azure subscription."
    }
    $SubscriptionId = $activeSubscription.Trim()
}
az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) {
    throw "Could not select the Azure subscription."
}

$phaseArguments = switch ($Phase) {
    "Prepare" { "--skip-tags observability_deploy -e zava_observability_phase=prepare" }
    "Stage" { "--tags observability_deploy -e zava_observability_phase=stage" }
    "Full" { "" }
}
$successMarker = if ($Phase -eq "Prepare") {
    "ZAVA_PRIVATE_OBSERVABILITY_PREPARED"
}
else {
    "ZAVA_PRIVATE_OBSERVABILITY_READY"
}

$temporaryFiles = @()
try {
    if ($Phase -ne "Stage") {
        $setupPath = [IO.Path]::GetTempFileName()
        $temporaryFiles += $setupPath
        Write-LfFile -Path $setupPath -Content @"
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv git
if [ ! -d '$RemoteRoot/.git' ]; then
  install -d -o estate -g estate '$RemoteRoot'
  sudo -u estate -H git clone --quiet '$RemoteRepository' '$RemoteRoot'
fi
sudo -u estate -H git -C '$RemoteRoot' fetch --quiet origin
sudo -u estate -H git -C '$RemoteRoot' checkout --quiet --detach '$commit'
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
    }

    $preparedCheck = ""
    $preparedMarker = ""
    if ($Phase -eq "Stage") {
        $preparedCheck = @"
if [ "`$(sudo -u estate -H git -C '$RemoteRoot' rev-parse HEAD)" != '$commit' ] ||
   [ ! -f generated/prepared-commit ] ||
   [ "`$(cat generated/prepared-commit)" != '$commit' ]; then
  echo 'Run the Prepare phase for this commit before the stage deployment.' >&2
  exit 1
fi
test -x .venv/bin/ansible-playbook
"@
    }
    elseif ($Phase -eq "Prepare") {
        $preparedMarker = "printf '%s\n' '$commit' > generated/prepared-commit"
    }

    $deployPath = [IO.Path]::GetTempFileName()
    $temporaryFiles += $deployPath
    Write-LfFile -Path $deployPath -Content @"
set -eu
cd '$RemoteRoot'
$preparedCheck
log=/home/estate/zava-observability-$($Phase.ToLowerInvariant()).log
if sudo -u estate -H .venv/bin/ansible-playbook \
    -i inventory/production/admin-hosts.yml \
    playbooks/private-estate.yml \
    $phaseArguments \
    -e @generated/azure.yml > "`$log" 2>&1; then
  tail -n 120 "`$log"
else
  rc=`$?
  tail -n 200 "`$log"
  exit "`$rc"
fi
$preparedMarker
echo $successMarker
"@
    Invoke-AzureRunCommandFile -Path $deployPath -SuccessMarker $successMarker
}
finally {
    foreach ($path in $temporaryFiles) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}
