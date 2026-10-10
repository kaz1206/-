<#
  RL_Verify P3 - hardware verification runner for MT5 optimization (NOT part of the product).

  What it does (Strategy Tester only; use a DEMO account; no cloud or remote agents):
    1. Copies RL_Verify_P3.mq5 into <TerminalDir>\MQL5\Experts\RL_Verify\ and compiles it.
    2. Runs, via terminal64.exe /portable /config:<ini>:
         O1 : Optimization=1, 3 x 4 = 12 passes, after moving Tester\cache aside      (X1-X4, X7, X9, X10)
         O2 : exact repeat of O1 WITHOUT clearing the cache                           (X6)
         O3 : exact repeat of O1 after clearing the cache again                       (X6)
         O4 : like O1 with 20000 extra doubles per frame and OptimizationCriterion=6  (X5, custom criterion)
         S1 : single test (Optimization=0) of Fast=10, Slow=30                        (X8)
    3. Collects EA outputs, optimization reports, cache listings, agent folder names and logs into
       verification\p3\results\<timestamp>\ with the account login/name, Windows user and PC name
       redacted, and creates results_<timestamp>.zip next to it.

  The cache is never deleted: it is MOVED to <TerminalDir>\Tester\cache_rlv_backup\<timestamp>\.

  Usage (PowerShell):
    powershell -ExecutionPolicy Bypass -File <path>\verify_p3.ps1 -TerminalDir "C:\MT5_verify"
  Optional:
    -Symbol EURUSD -Period H1 -FromDate 2023.01.02 -ToDate 2023.04.03 -TimeoutSec 1800 -SkipCompile
#>
param(
  [Parameter(Mandatory = $true)][string]$TerminalDir,
  [string]$Symbol = "EURUSD",
  [string]$Period = "H1",
  [string]$FromDate = "2023.01.02",
  [string]$ToDate = "2023.04.03",
  [int]$TimeoutSec = 1800,
  [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"
$ScriptVersion = "1"

$VerifyRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TerminalDir = (Resolve-Path $TerminalDir).Path
$Exe = Join-Path $TerminalDir "terminal64.exe"
$MetaEditor = Join-Path $TerminalDir "metaeditor64.exe"
$CommonFiles = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files"
$TesterDir = Join-Path $TerminalDir "Tester"
$CacheDir = Join-Path $TesterDir "cache"

if (-not (Test-Path $Exe)) { throw "terminal64.exe was not found in: $TerminalDir" }

function Get-OwnTerminals { return @(Get-Process -Name terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }) }
if ((Get-OwnTerminals).Count -gt 0) { throw "This MT5 terminal is running. Close it (File > Exit) and run this script again." }

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $VerifyRoot "results\$Stamp"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$CacheBackup = Join-Path $TesterDir "cache_rlv_backup\$Stamp"

# ---------------------------------------------------------------- helpers
$script:RedactTerms = New-Object System.Collections.Generic.List[string]
if ($env:USERNAME) { $script:RedactTerms.Add($env:USERNAME) }
if ($env:COMPUTERNAME) { $script:RedactTerms.Add($env:COMPUTERNAME) }

function Write-Lines([string]$path, [string[]]$lines, [string]$encName) {
  $text = ($lines -join "`r`n") + "`r`n"
  if ($encName -eq "ascii") { $enc = [System.Text.Encoding]::ASCII } else { $enc = New-Object System.Text.UnicodeEncoding($false, $true) }
  [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Get-HeadHex([string]$path) {
  $fs = [System.IO.File]::OpenRead($path)
  try {
    $buf = New-Object byte[] 4
    $n = $fs.Read($buf, 0, 4)
    return (($buf[0..([Math]::Max($n - 1, 0))] | ForEach-Object { $_.ToString("X2") }) -join " ")
  } finally { $fs.Close() }
}

function Read-AnyText([string]$path) {
  $b = [System.IO.File]::ReadAllBytes($path)
  if ($b.Length -ge 2 -and $b[0] -eq 0xFF -and $b[1] -eq 0xFE) { return @{ text = [System.Text.Encoding]::Unicode.GetString($b, 2, $b.Length - 2); enc = "utf16le-bom" } }
  if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { return @{ text = [System.Text.Encoding]::UTF8.GetString($b, 3, $b.Length - 3); enc = "utf8-bom" } }
  $zeros = 0
  $lim = [Math]::Min($b.Length, 400)
  for ($i = 1; $i -lt $lim; $i += 2) { if ($b[$i] -eq 0) { $zeros++ } }
  if ($lim -gt 0 -and $zeros -gt ($lim / 4)) { return @{ text = [System.Text.Encoding]::Unicode.GetString($b); enc = "utf16le-nobom" } }
  return @{ text = [System.Text.Encoding]::UTF8.GetString($b); enc = "utf8-or-ansi" }
}

function Protect-Text([string]$t) {
  foreach ($term in $script:RedactTerms) {
    if ($term -and $term.Length -ge 2) { $t = $t -replace [regex]::Escape($term), "<redacted>" }
  }
  $t = $t -replace "(?<=')\d{5,12}(?=')", "<login>"
  $t = $t -replace "(?i)(login\D{0,5})\d{5,12}", '$1<login>'
  $t = $t -replace "(?i)(performed from )\S+", '$1<ip>'
  $t = $t -replace "\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b", "<ip>"
  $t = $t -replace "(?i)\b[0-9a-f]{0,4}(?::[0-9a-f]{0,4})*::[0-9a-f:]*", "<ip>"
  $t = $t -replace "\b(\d{1,3}\.){3}\d{1,3}\b", "<ip>"
  return $t
}

function Save-Redacted([string]$src, [string]$dst) {
  $r = Read-AnyText $src
  [System.IO.File]::WriteAllText($dst, (Protect-Text $r.text), (New-Object System.Text.UTF8Encoding($false)))
  return $r.enc
}

function Get-RelPath([string]$p) {
  if ($p.StartsWith($TerminalDir, [System.StringComparison]::OrdinalIgnoreCase)) { return "<TerminalDir>" + $p.Substring($TerminalDir.Length) }
  if ($p.StartsWith($CommonFiles, [System.StringComparison]::OrdinalIgnoreCase)) { return "<CommonFiles>" + $p.Substring($CommonFiles.Length) }
  return (Protect-Text $p)
}

function Find-RlvFiles([string]$pattern) {
  $items = @()
  $items += Get-ChildItem -Path $TerminalDir -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\cache_rlv_backup\*" }
  if (Test-Path $CommonFiles) { $items += Get-ChildItem -Path $CommonFiles -File -Filter $pattern -ErrorAction SilentlyContinue }
  return $items
}

function Remove-RlvOutputs([string[]]$prefixes) {
  foreach ($pre in $prefixes) {
    Find-RlvFiles "RLV_${pre}_*" | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
  }
}

function Get-CacheListing {
  if (-not (Test-Path $CacheDir)) { return @() }
  return @(Get-ChildItem -Path $CacheDir -File -ErrorAction SilentlyContinue | ForEach-Object {
      [ordered]@{ name = $_.Name; size = $_.Length; mtime = $_.LastWriteTime.ToString("o") } })
}

function Move-CacheAside([string]$label) {
  if (-not (Test-Path $CacheDir)) { return 0 }
  $files = @(Get-ChildItem -Path $CacheDir -File -ErrorAction SilentlyContinue)
  if ($files.Count -eq 0) { return 0 }
  $dst = Join-Path $CacheBackup $label
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  $files | ForEach-Object { Move-Item -LiteralPath $_.FullName -Destination $dst -Force }
  return $files.Count
}

function Get-AgentDirs {
  if (-not (Test-Path $TesterDir)) { return @() }
  return @(Get-ChildItem -Path $TesterDir -Directory -Filter "Agent-*" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
}

function Read-JsonFile([string]$path) {
  try { return ((Read-AnyText $path).text | ConvertFrom-Json) } catch { return $null }
}

$Summary = [ordered]@{
  script_version = $ScriptVersion
  started        = (Get-Date).ToString("o")
  os             = [Environment]::OSVersion.VersionString
  powershell     = $PSVersionTable.PSVersion.ToString()
  symbol         = $Symbol
  period         = $Period
  from_date      = $FromDate
  to_date        = $ToDate
  logical_cpus   = [Environment]::ProcessorCount
  compile        = $null
  runs           = @()
}

# ---------------------------------------------------------------- compile
$ExpertsDir = Join-Path $TerminalDir "MQL5\Experts\RL_Verify"
New-Item -ItemType Directory -Force -Path $ExpertsDir | Out-Null
$Mq5 = Join-Path $ExpertsDir "RL_Verify_P3.mq5"
$Ex5 = Join-Path $ExpertsDir "RL_Verify_P3.ex5"
Copy-Item -LiteralPath (Join-Path $VerifyRoot "mql5\RL_Verify_P3.mq5") -Destination $Mq5 -Force

if (-not $SkipCompile) {
  if (Test-Path $Ex5) { Remove-Item -LiteralPath $Ex5 -Force }
  $clog = Join-Path $Out "compile_raw.log"
  $cinfo = [ordered]@{ metaeditor_found = (Test-Path $MetaEditor) }
  if (Test-Path $MetaEditor) {
    $p = Start-Process -FilePath $MetaEditor -ArgumentList @("/portable", "/compile:`"$Mq5`"", "/log:`"$clog`"") -PassThru -Wait
    $cinfo.exit_code = $p.ExitCode
    if (Test-Path $clog) {
      $cinfo.log_encoding = Save-Redacted $clog (Join-Path $Out "compile.log")
      Remove-Item -LiteralPath $clog -Force
    }
  }
  $cinfo.ex5_exists = (Test-Path $Ex5)
  $Summary.compile = $cinfo
}
if (-not (Test-Path $Ex5)) {
  $Summary.aborted = "ex5 not found after compile"
  ($Summary | ConvertTo-Json -Depth 8) | Out-File -FilePath (Join-Path $Out "summary.json") -Encoding ascii
  Write-Host ""
  Write-Host "Compile did not produce RL_Verify_P3.ex5." -ForegroundColor Yellow
  Write-Host "Send this folder (compile.log is inside): $Out"
  exit 1
}
$Summary.ex5_sha256 = (Get-FileHash -LiteralPath $Ex5 -Algorithm SHA256).Hash

# ---------------------------------------------------------------- .set contents
function Get-OptSet([string]$tag, [int]$pad) {
  return @(
    "InpFast=5||5||5||15||Y",
    "InpSlow=20||20||10||50||Y",
    "InpLots=0.01||0.01||0.01||0.01||N",
    "InpFramePad=$pad||$pad||1||$pad||N",
    "RL_RunTag=$tag"
  )
}
$SetS1 = @(
  "InpFast=10||10||5||10||N",
  "InpSlow=30||30||10||30||N",
  "InpLots=0.01||0.01||0.01||0.01||N",
  "InpFramePad=0||0||1||0||N",
  "RL_RunTag=S1"
)

# ---------------------------------------------------------------- run
function Invoke-VerifyRun {
  param([string]$Label, [string]$Tag, [int]$Optimization, [string[]]$SetLines, [bool]$ClearCache,
        [string]$Criterion = "", [int]$ExpectedPasses = 0)

  Write-Host "=== $Label (tag=$Tag, Optimization=$Optimization, clear cache=$ClearCache)" -ForegroundColor Cyan
  $runDir = Join-Path $Out $Label
  New-Item -ItemType Directory -Force -Path $runDir | Out-Null

  $rec = [ordered]@{ label = $Label; tag = $Tag; optimization = $Optimization; criterion = $Criterion; expected_passes = $ExpectedPasses }
  $rec.cache_before = @(Get-CacheListing)
  $rec.cache_moved_aside = 0
  if ($ClearCache) { $rec.cache_moved_aside = Move-CacheAside $Label }

  $setName = "rlv_$Label.set"
  $setDir = Join-Path $TerminalDir "MQL5\Profiles\Tester"
  New-Item -ItemType Directory -Force -Path $setDir | Out-Null
  $setPath = Join-Path $setDir $setName
  Write-Lines $setPath $SetLines "utf16le"
  Copy-Item -LiteralPath $setPath -Destination (Join-Path $runDir $setName) -Force

  $ini = @(
    "[Tester]",
    "Expert=RL_Verify\RL_Verify_P3",
    "ExpertParameters=$setName",
    "Symbol=$Symbol",
    "Period=$Period",
    "Model=1",
    "ExecutionMode=0",
    "Optimization=$Optimization"
  )
  if ($Criterion -ne "") { $ini += "OptimizationCriterion=$Criterion" }
  $ini += @(
    "ForwardMode=0",
    "FromDate=$FromDate",
    "ToDate=$ToDate",
    "Deposit=10000",
    "Currency=USD",
    "Leverage=100",
    "Visual=0",
    "Report=RLV_${Label}_report",
    "ReplaceReport=1",
    "ShutdownTerminal=1",
    "UseLocal=1",
    "UseRemote=0",
    "UseCloud=0"
  )
  $iniPath = Join-Path $runDir "$Label.ini"
  Write-Lines $iniPath $ini "ascii"

  Remove-RlvOutputs @($Tag, $Label)
  $rec.agent_dirs_before = @(Get-AgentDirs)

  $t0 = Get-Date
  $p = Start-Process -FilePath $Exe -ArgumentList @("/portable", "/config:`"$iniPath`"") -PassThru
  $deadline = $t0.AddSeconds($TimeoutSec)
  $exited = $p.WaitForExit($TimeoutSec * 1000)
  # a pending LiveUpdate can hand over to a relaunched terminal (ARCHITECTURE C35/C36): wait for it too
  Start-Sleep -Seconds 20
  while ((Get-OwnTerminals).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
  $timedOut = $false
  if ((Get-OwnTerminals).Count -gt 0 -or -not $exited) {
    $timedOut = $true
    Get-OwnTerminals | ForEach-Object { & taskkill /PID $_.Id /T /F | Out-Null }
  }
  $t1 = Get-Date
  Start-Sleep -Seconds 3

  $rec.started = $t0.ToString("o")
  $rec.finished = $t1.ToString("o")
  $rec.seconds = [int]($t1 - $t0).TotalSeconds
  $rec.timed_out = $timedOut
  $rec.exit_code = $null
  if ($exited) { $rec.exit_code = $p.ExitCode }
  $rec.cache_after = @(Get-CacheListing)
  $rec.agent_dirs_after = @(Get-AgentDirs)
  $rec.files = @()
  $rec.logs = @()

  # redaction terms written by the frame-mode instance
  foreach ($f in (Find-RlvFiles "RLV_${Tag}_redact.txt")) {
    foreach ($line in (Read-AnyText $f.FullName).text -split "`n") {
      $s = $line.Trim()
      if ($s.Length -ge 2 -and -not $script:RedactTerms.Contains($s)) { $script:RedactTerms.Add($s) }
    }
  }

  $agentFiles = 0
  $fmDone = $null
  $reportFound = $false
  $seen = @{}
  foreach ($pre in @($Tag, $Label)) {
    foreach ($f in (Find-RlvFiles "RLV_${pre}_*")) {
      if ($seen.ContainsKey($f.FullName)) { continue }
      $seen[$f.FullName] = $true
      if ($f.Name -like "*_redact.txt") { continue }
      $origin = "terminal"
      if ($f.FullName.StartsWith($CommonFiles, [System.StringComparison]::OrdinalIgnoreCase)) { $origin = "common" }
      $rec.files += [ordered]@{ name = $f.Name; origin = $origin; path = (Get-RelPath $f.FullName); size = $f.Length; head_hex = (Get-HeadHex $f.FullName); mtime = $f.LastWriteTime.ToString("o") }
      $dst = Join-Path $runDir ("{0}__{1}.txt" -f $origin, $f.Name)
      [void](Save-Redacted $f.FullName $dst)
      if ($f.Name -like "*_agent_*") { $agentFiles++ }
      if ($f.Name -like "*_fm_done.json" -and $origin -eq "common") { $fmDone = Read-JsonFile $f.FullName }
      if ($f.Name -like "RLV_${Label}_report*") { $reportFound = $true }
    }
  }
  $rec.agent_files = $agentFiles
  $rec.report_found = $reportFound
  $rec.frames_received = $null
  if ($fmDone) { $rec.frames_received = $fmDone.frames_received }

  # logs touched during this run
  $logDirs = @((Join-Path $TerminalDir "logs"), (Join-Path $TesterDir "logs"))
  if (Test-Path $TesterDir) {
    Get-ChildItem -Path $TesterDir -Directory -Filter "Agent-*" -ErrorAction SilentlyContinue | ForEach-Object { $logDirs += (Join-Path $_.FullName "logs") }
  }
  $i = 0
  foreach ($d in $logDirs) {
    if (-not (Test-Path $d)) { continue }
    Get-ChildItem -Path $d -File -Filter "*.log" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $t0.AddMinutes(-1) } | ForEach-Object {
      $i++
      $dst = Join-Path $runDir ("log{0:D2}__{1}" -f $i, $_.Name)
      $enc = Save-Redacted $_.FullName $dst
      $rec.logs += [ordered]@{ source = (Get-RelPath $_.FullName); saved_as = (Split-Path $dst -Leaf); encoding = $enc; head_hex = (Get-HeadHex $_.FullName); size = $_.Length }
    }
  }

  if ($Optimization -eq 0) {
    if ($agentFiles -ge 1) { $rec.status = "DONE" } else { $rec.status = "NO_OUTPUT" }
  } elseif ($fmDone -and $fmDone.frames_received -eq $ExpectedPasses) {
    $rec.status = "DONE"
  } elseif ($fmDone -or $agentFiles -gt 0 -or $reportFound) {
    $rec.status = "PARTIAL"
  } else {
    $rec.status = "NO_OUTPUT"
  }
  Write-Host ("    -> {0}  (frames={1}, agent files={2}, report={3}, {4}s, timed out={5})" -f $rec.status, $rec.frames_received, $agentFiles, $reportFound, $rec.seconds, $timedOut)
  return $rec
}

# ---------------------------------------------------------------- sequence
$r = Invoke-VerifyRun -Label "O1" -Tag "O1" -Optimization 1 -SetLines (Get-OptSet "O1" 0) -ClearCache $true -ExpectedPasses 12
$Summary.runs += $r
if ($r.status -ne "NO_OUTPUT") {
  $Summary.runs += (Invoke-VerifyRun -Label "O2" -Tag "O1" -Optimization 1 -SetLines (Get-OptSet "O1" 0) -ClearCache $false -ExpectedPasses 12)
  $Summary.runs += (Invoke-VerifyRun -Label "O3" -Tag "O1" -Optimization 1 -SetLines (Get-OptSet "O1" 0) -ClearCache $true -ExpectedPasses 12)
  $Summary.runs += (Invoke-VerifyRun -Label "O4" -Tag "O4" -Optimization 1 -SetLines (Get-OptSet "O4" 20000) -ClearCache $true -Criterion "6" -ExpectedPasses 12)
} else {
  $Summary.aborted = "O1 produced no output; O2-O4 skipped"
}
$Summary.runs += (Invoke-VerifyRun -Label "S1" -Tag "S1" -Optimization 0 -SetLines $SetS1 -ClearCache $false)

$Summary.cache_backup = (Get-RelPath $CacheBackup)
$Summary.finished = (Get-Date).ToString("o")
$json = Protect-Text ($Summary | ConvertTo-Json -Depth 8)
[System.IO.File]::WriteAllText((Join-Path $Out "summary.json"), $json, (New-Object System.Text.UTF8Encoding($false)))

$zip = Join-Path (Split-Path $Out -Parent) "results_$Stamp.zip"
Compress-Archive -Path (Join-Path $Out "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "Finished. Please send this file:" -ForegroundColor Green
Write-Host "  $zip"
