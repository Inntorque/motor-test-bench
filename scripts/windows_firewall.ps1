$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
if (-not (Test-Path $python)) { throw 'Bundled Python not found' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $proc = Start-Process -FilePath "$PSHOME\powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'))
    exit $proc.ExitCode
}
$name = 'Inntorque-TorqueWorkbench-LAN'
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $name -Enabled True -Profile Private -Direction Inbound -Action Allow | Out-Null
    Get-NetFirewallRule -Name $name | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $python | Out-Null
    Get-NetFirewallRule -Name $name | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort 5173 | Out-Null
    Get-NetFirewallRule -Name $name | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet | Out-Null
} else {
    New-NetFirewallRule -Name $name -DisplayName 'Inntorque Torque Workbench (Private LAN)' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5173 -Profile Private `
        -RemoteAddress LocalSubnet -Program $python | Out-Null
}
Write-Host 'Private LAN firewall rule is ready: TCP 5173, LocalSubnet only.'
