<#
  RL_Verify P1 - hardware verification runner (NOT part of the P1 product).

  What it does (no trading on any live account; use a DEMO account):
    1. Copies RL_Verify_P1.mq5 into <TerminalDir>\MQL5\Experts\RL_Verify\ and compiles it.
    2. Runs a few single backtests via:  terminal64.exe /portable /config:<ini>
         R1 : Model=4, .set = UTF-16LE with BOM, "value||start||step||stop||N" form
              (fallbacks R1b: Expert path with .ex5, R1c: ini in UTF-16LE, only if R1 fails)
         R2 : exact repeat of R1 after deleting R1 outputs (cache / reproducibility)
         R3 : .set = UTF-8 without BOM, plain "name=value" form
         R4 : Model=1 (to confirm the Model value mapping)
    3. Collects EA outputs, MT5 report, and logs into verification\p1\results\<timestamp>\
       with the account login, account name, Windows user name and PC name redacted.
    4. Creates results_<timestamp>.zip next to that folder.

  Usage (PowerShell):
    powershell -ExecutionPolicy Bypass -File <path>\verify_p1.ps1 -TerminalDir "C:\MT5_verify"
  Optional:
    -Symbol EURUSD -Period H1 -FromDate 2023.01.09 -ToDate 2023.01.13 -TimeoutSec 900 -SkipCompile
#>
param(
  [Parameter(Mandatory = $true)][string]$TerminalDir,
  [string]$Symbol = "EURUSD",
  [string]$Period = "H1",
  [string]$FromDate = "2023.01.09",
  [string]$ToDate = "2023.01.13",
  [int]$TimeoutSec = 900,
  [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"
$ScriptVersion = "1"

$VerifyRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TerminalDir = (Resolve-Path $TerminalDir).Path
$Exe = Join-Path $TerminalDir "terminal64.exe"
$MetaEditor = Join-Path $TerminalDir "metaeditor64.exe"
$CommonFiles = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files"

if (-not (Test-Path $Exe)) { throw "terminal64.exe was not found in: $TerminalDir" }
$running = Get-Process -Name terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }
if ($running) { throw "This MT5 terminal is running. Close it (File > Exit) and run this script again." }

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $VerifyRoot "results\$Stamp"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# ---------------------------------------------------------------- helpers
$script:RedactTerms = New-Object System.Collections.Generic.List[string]
if ($env:USERNAME) { $script:RedactTerms.Add($env:USERNAME) }
if ($env:COMPUTERNAME) { $script:RedactTerms.Add($env:COMPUTERNAME) }

function Get-Enc([string]$name) {
  switch ($name) {
    "ascii"     { return [System.Text.Encoding]::ASCII }
    "utf16le"   { return (New-Object System.Text.UnicodeEncoding($false, $true)) }
    "utf8nobom" { return (New-Object System.Text.UTF8Encoding($false)) }
    default     { throw "unknown encoding $name" }
  }
}

function Write-Lines([string]$path, [string[]]$lines, [string]$encName) {
  $text = ($lines -join "`r`n") + "`r`n"
  [System.IO.File]::WriteAllText($path, $text, (Get-Enc $encName))
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
  $items += Get-ChildItem -Path $TerminalDir -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue
  if (Test-Path $CommonFiles) { $items += Get-ChildItem -Path $CommonFiles -File -Filter $pattern -ErrorAction SilentlyContinue }
  return $items
}

function Remove-RlvOutputs([string]$tag) {
  foreach ($pat in @("RLV_${tag}_*", "RLV_none_*")) {
    Find-RlvFiles $pat | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
  }
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
  common_files   = (Protect-Text $CommonFiles)
  compile        = $null
  runs           = @()
}

# ---------------------------------------------------------------- compile
$ExpertsDir = Join-Path $TerminalDir "MQL5\Experts\RL_Verify"
New-Item -ItemType Directory -Force -Path $ExpertsDir | Out-Null
$Mq5 = Join-Path $ExpertsDir "RL_Verify_P1.mq5"
$Ex5 = Join-Path $ExpertsDir "RL_Verify_P1.ex5"
Copy-Item -LiteralPath (Join-Path $VerifyRoot "mql5\RL_Verify_P1.mq5") -Destination $Mq5 -Force

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
  Write-Host "Compile did not produce RL_Verify_P1.ex5." -ForegroundColor Yellow
  Write-Host "Open MetaEditor, open MQL5\Experts\RL_Verify\RL_Verify_P1.mq5, press F7, then run this script again with -SkipCompile."
  Write-Host "If F7 shows errors, send the folder: $Out"
  exit 1
}
$Summary.ex5_sha256 = (Get-FileHash -LiteralPath $Ex5 -Algorithm SHA256).Hash

# ---------------------------------------------------------------- .set contents
$jp = -join ([char[]](0x65E5, 0x672C, 0x8A9E))   # three CJK characters, to test non-ASCII strings
$SetFull = @(
  "InpInt=42||42||1||42||N",
  "InpDouble=1.23456789012345||1.23456789012345||0.1||1.23456789012345||N",
  "InpString=${jp}_abc",
  "InpBool=true||true||0||true||N",
  "InpEnum=15||15||0||15||N",
  "InpDate=1672531200||1672531200||0||1672531200||N",
  "RL_RunTag=R1"
)
$SetPlainR3 = @(
  "InpInt=7",
  "InpDouble=2.5",
  "InpString=${jp}_utf8",
  "InpBool=true",
  "InpEnum=16385",
  "InpDate=1672531200",
  "RL_RunTag=R3"
)
$SetFullR4 = $SetFull | ForEach-Object { $_ -replace "^RL_RunTag=R1$", "RL_RunTag=R4" }

# ---------------------------------------------------------------- run
function Invoke-VerifyRun {
  param([string]$Label, [string]$Tag, [int]$Model, [string[]]$SetLines, [string]$SetEnc, [string]$Expert, [string]$IniEnc)

  Write-Host "=== $Label (tag=$Tag, Model=$Model, set=$SetEnc, ini=$IniEnc, Expert=$Expert)" -ForegroundColor Cyan
  $runDir = Join-Path $Out $Label
  New-Item -ItemType Directory -Force -Path $runDir | Out-Null

  $setName = "rlv_$Tag.set"
  $setDir = Join-Path $TerminalDir "MQL5\Profiles\Tester"
  New-Item -ItemType Directory -Force -Path $setDir | Out-Null
  $setPath = Join-Path $setDir $setName
  Write-Lines $setPath $SetLines $SetEnc
  Copy-Item -LiteralPath $setPath -Destination (Join-Path $runDir $setName) -Force

  $ini = @(
    "[Tester]",
    "Expert=$Expert",
    "ExpertParameters=$setName",
    "Symbol=$Symbol",
    "Period=$Period",
    "Model=$Model",
    "ExecutionMode=0",
    "Optimization=0",
    "ForwardMode=0",
    "FromDate=$FromDate",
    "ToDate=$ToDate",
    "Deposit=10000",
    "Currency=USD",
    "Leverage=100",
    "Visual=0",
    "Report=RLV_${Tag}_report",
    "ReplaceReport=1",
    "ShutdownTerminal=1",
    "UseLocal=1",
    "UseRemote=0",
    "UseCloud=0"
  )
  $iniPath = Join-Path $runDir "$Label.ini"
  Write-Lines $iniPath $ini $IniEnc

  Remove-RlvOutputs $Tag

  $t0 = Get-Date
  $p = Start-Process -FilePath $Exe -ArgumentList @("/portable", "/config:`"$iniPath`"") -PassThru
  $exited = $p.WaitForExit($TimeoutSec * 1000)
  $exitCode = $null
  if ($exited) { $exitCode = $p.ExitCode } else { & taskkill /PID $p.Id /T /F | Out-Null }
  $t1 = Get-Date
  Start-Sleep -Seconds 3

  $rec = [ordered]@{
    label = $Label; tag = $Tag; model = $Model; set_encoding = $SetEnc; ini_encoding = $IniEnc; expert = $Expert
    started = $t0.ToString("o"); finished = $t1.ToString("o"); seconds = [int]($t1 - $t0).TotalSeconds
    exited_by_itself = $exited; exit_code = $exitCode
    set_head_hex = (Get-HeadHex $setPath)
    files = @(); logs = @()
    done_marker_found = $false; none_marker_found = $false
  }

  # EA outputs and report (search everywhere relevant)
  $redactFile = $null
  foreach ($f in (Find-RlvFiles "RLV_*")) {
    if (-not ($f.Name -like "RLV_${Tag}_*" -or $f.Name -like "RLV_none_*")) { continue }
    if ($f.Name -like "*_redact.txt") { $redactFile = $f.FullName; continue }
    $origin = "terminal"
    if ($f.FullName.StartsWith($CommonFiles, [System.StringComparison]::OrdinalIgnoreCase)) { $origin = "common" }
    $rec.files += [ordered]@{ name = $f.Name; origin = $origin; path = (Get-RelPath $f.FullName); size = $f.Length; head_hex = (Get-HeadHex $f.FullName); mtime = $f.LastWriteTime.ToString("o") }
    if ($f.Name -like "*_done.json") { if ($f.Name -like "RLV_none_*") { $rec.none_marker_found = $true } else { $rec.done_marker_found = $true } }
  }
  if ($redactFile) {
    foreach ($line in (Read-AnyText $redactFile).text -split "`n") {
      $s = $line.Trim()
      if ($s.Length -ge 2 -and -not $script:RedactTerms.Contains($s)) { $script:RedactTerms.Add($s) }
    }
  }
  foreach ($f in (Find-RlvFiles "RLV_*")) {
    if (-not ($f.Name -like "RLV_${Tag}_*" -or $f.Name -like "RLV_none_*")) { continue }
    if ($f.Name -like "*_redact.txt") { continue }
    $origin = "terminal"
    if ($f.FullName.StartsWith($CommonFiles, [System.StringComparison]::OrdinalIgnoreCase)) { $origin = "common" }
    $dst = Join-Path $runDir ("{0}__{1}.txt" -f $origin, $f.Name)
    [void](Save-Redacted $f.FullName $dst)
  }

  # logs touched during this run
  $logDirs = @((Join-Path $TerminalDir "logs"), (Join-Path $TerminalDir "Tester\logs"))
  $testerDir = Join-Path $TerminalDir "Tester"
  if (Test-Path $testerDir) {
    Get-ChildItem -Path $testerDir -Directory -Filter "Agent-*" -ErrorAction SilentlyContinue | ForEach-Object { $logDirs += (Join-Path $_.FullName "logs") }
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

  $status = "NO_OUTPUT"
  if ($rec.done_marker_found) { $status = "DONE" } elseif ($rec.none_marker_found) { $status = "RAN_BUT_SET_NOT_APPLIED" }
  $rec.status = $status
  Write-Host "    -> $status  (exit=$exitCode, $($rec.seconds)s)"
  return $rec
}

# ---------------------------------------------------------------- sequence
$expert = "RL_Verify\RL_Verify_P1"
$iniEnc = "ascii"

$r = Invoke-VerifyRun -Label "R1" -Tag "R1" -Model 4 -SetLines $SetFull -SetEnc "utf16le" -Expert $expert -IniEnc $iniEnc
$Summary.runs += $r
if ($r.status -eq "NO_OUTPUT") {
  $r = Invoke-VerifyRun -Label "R1b" -Tag "R1" -Model 4 -SetLines $SetFull -SetEnc "utf16le" -Expert "$expert.ex5" -IniEnc $iniEnc
  $Summary.runs += $r
  if ($r.status -ne "NO_OUTPUT") { $expert = "$expert.ex5" }
}
if ($r.status -eq "NO_OUTPUT") {
  $r = Invoke-VerifyRun -Label "R1c" -Tag "R1" -Model 4 -SetLines $SetFull -SetEnc "utf16le" -Expert $expert -IniEnc "utf16le"
  $Summary.runs += $r
  if ($r.status -ne "NO_OUTPUT") { $iniEnc = "utf16le" }
}

if ($r.status -ne "NO_OUTPUT") {
  $Summary.runs += (Invoke-VerifyRun -Label "R2" -Tag "R1" -Model 4 -SetLines $SetFull -SetEnc "utf16le" -Expert $expert -IniEnc $iniEnc)
  $Summary.runs += (Invoke-VerifyRun -Label "R3" -Tag "R3" -Model 4 -SetLines $SetPlainR3 -SetEnc "utf8nobom" -Expert $expert -IniEnc $iniEnc)
  $Summary.runs += (Invoke-VerifyRun -Label "R4" -Tag "R4" -Model 1 -SetLines $SetFullR4 -SetEnc "utf16le" -Expert $expert -IniEnc $iniEnc)
} else {
  $Summary.aborted = "R1, R1b and R1c produced no EA output; later runs skipped"
}

$Summary.finished = (Get-Date).ToString("o")
$json = Protect-Text ($Summary | ConvertTo-Json -Depth 8)
[System.IO.File]::WriteAllText((Join-Path $Out "summary.json"), $json, (New-Object System.Text.UTF8Encoding($false)))

$zip = Join-Path (Split-Path $Out -Parent) "results_$Stamp.zip"
Compress-Archive -Path (Join-Path $Out "*") -DestinationPath $zip -Force

Write-Host ""
Write-Host "Finished. Please send this file:" -ForegroundColor Green
Write-Host "  $zip"
