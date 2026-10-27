# Run the backend test suite on Windows (H11 groundwork, GO lane-qualification 4). The Windows counterpart of
# scripts/linux-container-test.sh: a development check, never a qualification run (packaging/windows/README.md).
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\run-backend-tests.ps1 -Out C:\hooke-tests [-Jobs 4]
#
# It uses only a pinned uv (downloaded and checked by SHA-256 unless uv is already on PATH), uv's own Python 3.13 and
# services/uv.lock. It installs nothing system-wide and writes only under -Out and the repository's services\.venv.
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$Jobs = 4,
    [string]$PytestArgs = ""
)
# Continue, not Stop: native tools (uv, git, python) write progress to stderr, which Stop would turn into a failure;
# every native step is checked by its exit code instead.
$ErrorActionPreference = "Continue"
$UvVersion = "0.7.19"
$UvSha256 = "3098b38638b271d8b14b81247d6d69bb105807ec9fc5eb85f8cc8b6de5867c86"   # uv-x86_64-pc-windows-msvc.zip
$Root = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw "run this from inside the repository" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Out = (Resolve-Path $Out).Path

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $zip = Join-Path $Out "uv.zip"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -ErrorAction Stop -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
    $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($got -ne $UvSha256) { throw "uv download hash $got does not match the pinned $UvSha256" }
    Expand-Archive -Force $zip (Join-Path $Out "uv")
    $uv = Join-Path $Out "uv\uv.exe"
}
& $uv --version | Out-File -Encoding utf8 (Join-Path $Out "uv.txt")

$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:MICROMIND_HOME = Join-Path $Out "mm-home"
$env:MICROMIND_KEYCHAIN = "off"
$env:CAMERA_NO_PHONE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
New-Item -ItemType Directory -Force -Path $env:MICROMIND_HOME | Out-Null

$rc = 1   # until pytest runs, a failure is a failure
Push-Location (Join-Path $Root "services")
try {
    git rev-parse HEAD | Out-File -Encoding utf8 (Join-Path $Out "commit.txt")
    & $uv sync --locked --python 3.13 --all-groups 2>&1 | Tee-Object -FilePath (Join-Path $Out "sync.txt")
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (see sync.txt)" }
    $py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    & $py -c "import sys, platform; print(sys.version.split()[0], platform.platform(), platform.machine())" |
        Out-File -Encoding utf8 (Join-Path $Out "platform.txt")
    & $py ..\packaging\windows\import_probe.py --out (Join-Path $Out "import-probe.json") 2>&1 |
        Out-File -Encoding utf8 (Join-Path $Out "import-probe.txt")
    $pyArgs = @("-m", "pytest", "-q", "-m", "not timing", "-p", "no:cacheprovider", "-n", "$Jobs", "-rfEs",
              "--junitxml=$(Join-Path $Out 'junit.xml')")
    if ($PytestArgs) { $pyArgs += $PytestArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries) }
    & $py @pyArgs 2>&1 | Out-File -Encoding utf8 (Join-Path $Out "pytest.txt")
    $rc = $LASTEXITCODE
    "pytest exit $rc" | Out-File -Append -Encoding utf8 (Join-Path $Out "pytest.txt")
    Get-Content (Join-Path $Out "pytest.txt") -Tail 3
} finally {
    Pop-Location
}
exit $rc
