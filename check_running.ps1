# Detect whether Daniya (main.py) is already running.
# exit 1 = running; exit 0 = not running
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'main\.py' -and $_.CommandLine -match 'Denia' }
if ($procs) {
    Write-Output ("Running instance(s): " + @($procs).Count)
    exit 1
} else {
    Write-Output "No running instance"
    exit 0
}
