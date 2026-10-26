param(
    [ValidateSet('install', 'uninstall')][string]$Action = 'install',
    [string]$Setup,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Use a normal desktop PowerShell, without elevation.'
}
$sid = $identity.User.Value
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$expected = [IO.Path]::GetFullPath((Join-Path $HOME 'dev\deskdeck'))
if ($source -ne $expected) { throw 'Use the reviewed checkout at ~/dev/deskdeck.' }
$prefix = Join-Path $HOME 'AppData\Local\DeskDeck'
function Assert-NoReparse([string]$Path) {
    $item = [IO.DirectoryInfo]::new($Path)
    while ($null -ne $item) {
        if ((Test-Path -LiteralPath $item.FullName) -and
            ((Get-Item -Force -LiteralPath $item.FullName).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Linked/reparse paths are not supported.'
        }
        $item = $item.Parent
    }
}
Assert-NoReparse $source
Assert-NoReparse $prefix
if ($Action -eq 'uninstall') {
    # Inspect exact prefix only; do not follow junctions or enumerate other apps.
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($prefix)
    while ($queue.Count -gt 0) {
        $path = $queue.Dequeue()
        $item = Get-Item -Force -LiteralPath $path
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked entries are refused.' }
        $acl = Get-Acl -LiteralPath $path
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) {
            throw 'An installation entry has a different owner.'
        }
        foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                $rule.IdentityReference.Value -notin @($sid, 'S-1-5-18')) {
                throw 'Installation permissions are no longer private; nothing removed.'
            }
        }
        if ($item.PSIsContainer) {
            foreach ($child in Get-ChildItem -Force -LiteralPath $path) { $queue.Enqueue($child.FullName) }
        }
    }
}
if ($Python) {
    if (-not [IO.Path]::IsPathRooted($Python)) { throw '-Python must be an explicit absolute executable path.' }
    $executable = [IO.Path]::GetFullPath($Python)
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'The selected Python executable is unavailable.' }
    $arguments = @('-B', '-m', 'deskdeck.installer')
} else {
    $launcher = Get-Command py -CommandType Application -ErrorAction Stop
    $executable = $launcher.Source
    $arguments = @('-3', '-B', '-m', 'deskdeck.installer')
}
$arguments += @($Action, '--source', $source, '--windows-owner-sid', $sid)
if ($Setup) {
    if ($Action -ne 'install') { throw 'Setup is only supported for installation.' }
    $arguments += @('--setup', [IO.Path]::GetFullPath($Setup))
}
Push-Location -LiteralPath $source
try {
    & $executable @arguments
    if ($LASTEXITCODE -ne 0) { throw 'DeskDeck installer stopped; inspect its private prefix/manifest.' }
} finally { Pop-Location }
