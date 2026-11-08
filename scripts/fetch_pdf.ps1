param([Parameter(Mandatory)][string]$Url,[Parameter(Mandatory)][string]$Sha256,[Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
& curl.exe --noproxy '*' -fL --max-time 120 -o $Out $Url
if ($LASTEXITCODE -ne 0) { throw 'Download failed' }
$actual = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash
if ($actual -ne $Sha256) { throw 'SHA256 mismatch' }
Write-Output "Verified: $Out"
