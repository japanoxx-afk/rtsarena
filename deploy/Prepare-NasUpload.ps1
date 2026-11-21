# Builds deploy\upload\rtsarena\ : exactly the files to upload to File Station game-servers/rtsarena/.
# The upload folder never contains data/ - uploading it can never overwrite match records or replays on the NAS.
# Usage (PowerShell, anywhere):  .\deploy\Prepare-NasUpload.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$out = Join-Path $here 'upload\rtsarena'

if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force (Join-Path $out 'app') | Out-Null

# app/arena/ : the web server package (no __pycache__)
Copy-Item -Recurse (Join-Path $repo 'arena') (Join-Path $out 'app\arena')
Get-ChildItem -Recurse -Directory -Filter '__pycache__' (Join-Path $out 'app') | Remove-Item -Recurse -Force
Copy-Item (Join-Path $here 'compose.yaml') (Join-Path $out 'compose.yaml')
Copy-Item (Join-Path $here 'NAS_DEPLOY.md') (Join-Path $out 'NAS_DEPLOY.md')

$version = (Select-String -Path (Join-Path $repo 'arena\__init__.py') -Pattern '^__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$commit = (git -C $repo rev-parse --short HEAD) 2>$null
Set-Content -Path (Join-Path $out 'app\DEPLOYED_VERSION.txt') -Encoding ascii -Value @(
    "arena_version=$version",
    "git_commit=$commit",
    "prepared=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
)

Write-Host "Upload folder ready: $out" -ForegroundColor Green
Write-Host "arena $version (commit $commit)"
Get-ChildItem -Recurse $out | Where-Object { -not $_.PSIsContainer } |
    ForEach-Object { '  ' + $_.FullName.Substring($out.Length + 1) + "  ($($_.Length) bytes)" }
