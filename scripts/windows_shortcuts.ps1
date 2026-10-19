param([Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($entry in @(@('Torque Workbench', 'start'), @('Stop Torque Workbench', 'stop'))) {
    $shortcut = $shell.CreateShortcut((Join-Path $desktop ($entry[0] + '.lnk')))
    $shortcut.TargetPath = Join-Path $InstallDir 'runtime\python\python.exe'
    $shortcut.Arguments = '-X utf8 "' + (Join-Path $InstallDir 'scripts\workbench.py') + '" ' + $entry[1]
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Save()
}
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Workbench LAN Access.lnk'))
$shortcut.TargetPath = Join-Path $InstallDir 'LAN-ACCESS.txt'
$shortcut.Save()
