<#
.SYNOPSIS
    GitHub Copilot Customer Report Generator (PowerShell)

.DESCRIPTION
    Produces a single, clean CSV report with organization-level user metrics.
    Each row is one user, grouped by org, with org summary rows between groups.
    Zero empty columns - uses only verified-populated API fields.

.PARAMETER Token
    GitHub Personal Access Token. Falls back to GITHUB_TOKEN env var.

.PARAMETER Enterprise
    Enterprise slug - auto-discovers all orgs. Falls back to ENTERPRISE_SLUG env var.

.PARAMETER Orgs
    Comma-separated org slugs (overrides -Enterprise). Falls back to ORGS env var.

.PARAMETER OutputDir
    Directory for CSV output. Defaults to current directory.

.PARAMETER RawJson
    Also save raw API JSON responses.

.EXAMPLE
    .\copilot_customer_report.ps1 -Enterprise my-ent -Token ghp_xxx
    .\copilot_customer_report.ps1 -Orgs "org1,org2"
#>

[CmdletBinding()]
param(
    [string]$Token = $env:GITHUB_TOKEN,
    [string]$Enterprise = $env:ENTERPRISE_SLUG,
    [string]$Orgs = $env:ORGS,
    [string]$OutputDir = $(if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "." }),
    [switch]$RawJson
)

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

$script:GitHubApiBase = "https://api.github.com"
$script:ApiVersion = "2022-11-28"
$script:ReportDays = 28

$script:ReportColumns = @(
    "organization", "user_login", "status", "plan_type", "seat_assigned_date",
    "last_activity_date", "days_inactive", "editor", "copilot_model",
    "total_days_active", "utilization_pct", "total_interactions",
    "total_code_generations", "total_code_acceptances", "acceptance_rate_pct",
    "total_loc_suggested", "total_loc_added", "total_loc_deleted", "features_used"
)

# ---------------------------------------------------------------------------
# API Helpers
# ---------------------------------------------------------------------------

function Get-AuthHeaders {
    param([string]$TokenValue)
    return @{
        "Authorization"      = "Bearer $TokenValue"
        "Accept"             = "application/vnd.github+json"
        "X-GitHub-Api-Version" = $script:ApiVersion
    }
}

function Invoke-GitHubApi {
    param(
        [string]$Url,
        [hashtable]$Headers,
        [int]$TimeoutSec = 30
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -Headers $Headers -TimeoutSec $TimeoutSec -UseBasicParsing
        return @{ StatusCode = $response.StatusCode; Content = ($response.Content | ConvertFrom-Json) }
    }
    catch {
        $statusCode = 0
        $body = $null
        try {
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
                $stream = $_.Exception.Response.GetResponseStream()
                if ($stream -and $stream.CanRead) {
                    $reader = [System.IO.StreamReader]::new($stream)
                    $bodyText = $reader.ReadToEnd()
                    $reader.Close()
                    if ($bodyText) { $body = $bodyText | ConvertFrom-Json }
                }
            }
        } catch {}
        if ($statusCode -eq 0) { $statusCode = 999 }
        return @{ StatusCode = $statusCode; Content = $body; Error = $_.Exception.Message }
    }
}

function Write-ApiError {
    param($Response)
    $msg = Get-SafeProperty $Response.Content 'message'
    if ($msg) {
        Write-Host "    API: $msg" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Token Validation
# ---------------------------------------------------------------------------

function Test-Token {
    param([string]$TokenValue)
    $headers = Get-AuthHeaders -TokenValue $TokenValue
    $resp = Invoke-GitHubApi -Url "$script:GitHubApiBase/user" -Headers $headers
    if ($resp.StatusCode -ne 200) {
        Write-Error "Token invalid (HTTP $($resp.StatusCode))."
        exit 1
    }
    $login = $resp.Content.login
    Write-Host "`n`u{1F511} Authenticated as: $login" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Org Discovery
# ---------------------------------------------------------------------------

function Get-Organizations {
    param([string]$TokenValue, [string]$EntSlug)
    $headers = Get-AuthHeaders -TokenValue $TokenValue
    $orgList = [System.Collections.ArrayList]::new()

    # Try enterprise endpoint
    $resp = Invoke-GitHubApi -Url "$script:GitHubApiBase/enterprises/$EntSlug/organizations?per_page=100" -Headers $headers
    if ($resp.StatusCode -eq 200 -and $resp.Content -is [array] -and $resp.Content.Count -gt 0) {
        return @($resp.Content | ForEach-Object { $_.login } | Where-Object { $_ })
    }

    # Fallback: user orgs
    Write-Host "  `u{26A0} Enterprise endpoint unavailable. Using your org memberships." -ForegroundColor Yellow
    $page = 1
    while ($true) {
        $resp = Invoke-GitHubApi -Url "$script:GitHubApiBase/user/orgs?page=$page&per_page=100" -Headers $headers
        if ($resp.StatusCode -ne 200) { Write-ApiError $resp; break }
        $data = $resp.Content
        if (-not $data -or $data.Count -eq 0) { break }
        foreach ($o in $data) { if ($o.login) { [void]$orgList.Add($o.login) } }
        if ($data.Count -lt 100) { break }
        $page++
    }
    return $orgList
}

# ---------------------------------------------------------------------------
# Fetch Seats
# ---------------------------------------------------------------------------

function Get-OrgSeats {
    param([string]$TokenValue, [string]$Org)
    $headers = Get-AuthHeaders -TokenValue $TokenValue
    $allSeats = [System.Collections.ArrayList]::new()
    $page = 1

    while ($true) {
        $resp = Invoke-GitHubApi -Url "$script:GitHubApiBase/orgs/$Org/copilot/billing/seats?page=$page&per_page=100" -Headers $headers
        if ($resp.StatusCode -ne 200) {
            if ($resp.StatusCode -in @(403, 404)) { Write-ApiError $resp }
            break
        }
        $pageSeats = Get-SafeProperty $resp.Content 'seats'
        if (-not $pageSeats -or $pageSeats.Count -eq 0) { break }
        foreach ($s in $pageSeats) { [void]$allSeats.Add($s) }
        $totalSeats = Get-SafeProperty $resp.Content 'total_seats' $allSeats.Count
        if ($allSeats.Count -ge $totalSeats) { break }
        $page++
    }
    return @($allSeats)
}

# ---------------------------------------------------------------------------
# Fetch NDJSON User Metrics
# ---------------------------------------------------------------------------

function Get-NdjsonMetrics {
    param([string]$TokenValue, [string]$Org)
    $headers = Get-AuthHeaders -TokenValue $TokenValue
    $records = [System.Collections.ArrayList]::new()

    $resp = Invoke-GitHubApi -Url "$script:GitHubApiBase/orgs/$Org/copilot/metrics/reports/users-28-day/latest" -Headers $headers -TimeoutSec 60
    if ($resp.StatusCode -ne 200) { return @($records) }

    $links = Get-SafeProperty $resp.Content 'download_links'
    if (-not $links -or $links.Count -eq 0) { return @($records) }

    Write-Host "   Downloading NDJSON ($($links.Count) file(s)) ..." -NoNewline

    foreach ($link in $links) {
        $dlResp = $null
        try {
            $dlResp = Invoke-WebRequest -Uri $link -TimeoutSec 120 -UseBasicParsing
        }
        catch {
            try {
                $dlResp = Invoke-WebRequest -Uri $link -Headers $headers -TimeoutSec 120 -UseBasicParsing
            }
            catch {
                Write-Host " failed." -ForegroundColor Yellow
                continue
            }
        }

        $rawLines = $dlResp.Content -split "`n"
        foreach ($rawLine in $rawLines) {
            $rawLine = $rawLine.Trim()
            if ($rawLine) {
                try { [void]$records.Add(($rawLine | ConvertFrom-Json)) } catch {}
            }
        }
    }
    Write-Host " done."
    return @($records)
}

# ---------------------------------------------------------------------------
# Editor Parsing
# ---------------------------------------------------------------------------

function Parse-Editor {
    param([string]$Raw)
    if (-not $Raw) { return @("N/A", "N/A") }

    $parts = $Raw -split "/"
    $editorName = $parts[0]

    $copilotParts = @()
    for ($i = 0; $i -lt $parts.Count; $i++) {
        if ($parts[$i] -match "(?i)copilot|GitHubCopilot") {
            $copilotParts = $parts[$i..($parts.Count - 1)]
            break
        }
    }

    $copilotModel = if ($copilotParts.Count -gt 0) { $copilotParts -join "/" } else { "N/A" }
    return @($editorName, $copilotModel)
}

# ---------------------------------------------------------------------------
# Safe Property Access
# ---------------------------------------------------------------------------

function Get-SafeProperty {
    param($Obj, [string]$Name, $Default = $null)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [hashtable]) {
        if ($Obj.ContainsKey($Name)) { return $Obj[$Name] }
        return $Default
    }
    if ($Obj.PSObject.Properties[$Name]) { return $Obj.$Name }
    return $Default
}

# ---------------------------------------------------------------------------
# Aggregate NDJSON Per User
# ---------------------------------------------------------------------------

function Get-UserAggregates {
    param([array]$NdjsonRecords)
    $users = @{}

    foreach ($rec in $NdjsonRecords) {
        $login = $null
        if ($rec.PSObject.Properties['user_login']) { $login = $rec.user_login }
        if (-not $login) { continue }

        if (-not $users.ContainsKey($login)) {
            $users[$login] = @{
                days_active   = 0; interactions = 0; code_gen = 0; code_accept = 0
                loc_suggested = 0; loc_added = 0; loc_deleted = 0
                used_chat = $false; used_agent = $false; used_cli = $false; used_code_review = $false
            }
        }
        $u = $users[$login]
        $u.days_active   += 1
        $u.interactions  += [int](Get-SafeProperty $rec 'user_initiated_interaction_count' 0)
        $u.code_gen      += [int](Get-SafeProperty $rec 'code_generation_activity_count' 0)
        $u.code_accept   += [int](Get-SafeProperty $rec 'code_acceptance_activity_count' 0)
        $u.loc_suggested += [int](Get-SafeProperty $rec 'loc_suggested_to_add_sum' 0)
        $u.loc_added     += [int](Get-SafeProperty $rec 'loc_added_sum' 0)
        $u.loc_deleted   += [int](Get-SafeProperty $rec 'loc_deleted_sum' 0)
        if (Get-SafeProperty $rec 'used_chat' $false)   { $u.used_chat = $true }
        if (Get-SafeProperty $rec 'used_agent' $false)   { $u.used_agent = $true }
        if (Get-SafeProperty $rec 'used_cli' $false)     { $u.used_cli = $true }
        if ((Get-SafeProperty $rec 'used_copilot_code_review_active' $false) -or
            (Get-SafeProperty $rec 'used_copilot_code_review_passive' $false)) {
            $u.used_code_review = $true
        }
    }
    return $users
}

# ---------------------------------------------------------------------------
# Build Features String
# ---------------------------------------------------------------------------

function Get-FeaturesUsed {
    param([hashtable]$U)
    $features = @()
    if ($U.used_chat)        { $features += "chat" }
    if ($U.used_agent)       { $features += "agent" }
    if ($U.used_cli)         { $features += "cli" }
    if ($U.used_code_review) { $features += "code_review" }
    if ($features.Count -eq 0) { return "none" }
    return ($features -join ", ")
}

# ---------------------------------------------------------------------------
# Build Report Rows
# ---------------------------------------------------------------------------

function Build-OrgReport {
    param([string]$Org, [array]$Seats, [array]$NdjsonRecords)

    $now = [DateTimeOffset]::UtcNow
    $userAgg = Get-UserAggregates -NdjsonRecords $NdjsonRecords

    $userRows = [System.Collections.ArrayList]::new()
    $inactiveLogins = [System.Collections.ArrayList]::new()
    $editorCounts = @{}
    $totalInteractions = 0
    $totalCodeGen = 0
    $totalCodeAccept = 0
    $totalLocAdded = 0
    $totalLocDeleted = 0
    $utilizations = [System.Collections.ArrayList]::new()

    foreach ($seat in $Seats) {
        $assignee = Get-SafeProperty $seat 'assignee'
        if (-not $assignee) { continue }
        $login = Get-SafeProperty $assignee 'login'
        if (-not $login) { continue }

        # Last activity
        $lastActivity = Get-SafeProperty $seat 'last_activity_at'
        $daysInactive = "never"
        $lastDate = "N/A"
        if ($lastActivity) {
            try {
                $laDt = [DateTimeOffset]::Parse($lastActivity)
                $daysInactive = [math]::Floor(($now - $laDt).TotalDays)
                $lastDate = $laDt.ToString("yyyy-MM-dd")
            } catch {}
        }

        # Seat assigned date
        $seatCreated = Get-SafeProperty $seat 'created_at'
        $seatDate = "N/A"
        if ($seatCreated) {
            try { $seatDate = ([DateTimeOffset]::Parse($seatCreated)).ToString("yyyy-MM-dd") } catch {}
        }

        # Status
        $status = if ($lastActivity) { "active" } else { "inactive" }
        if ($status -eq "inactive") { [void]$inactiveLogins.Add($login) }

        # Editor parsing
        $rawEditor = Get-SafeProperty $seat 'last_activity_editor' ''
        $parsed = Parse-Editor -Raw $rawEditor
        $editorName = $parsed[0]
        $copilotModel = $parsed[1]
        if ($editorName -and $editorName -ne "N/A") {
            if (-not $editorCounts.ContainsKey($editorName)) { $editorCounts[$editorName] = 0 }
            $editorCounts[$editorName]++
        }

        # NDJSON aggregated data
        $u = if ($userAgg.ContainsKey($login)) { $userAgg[$login] } else { @{
            days_active = 0; interactions = 0; code_gen = 0; code_accept = 0
            loc_suggested = 0; loc_added = 0; loc_deleted = 0
            used_chat = $false; used_agent = $false; used_cli = $false; used_code_review = $false
        }}

        $daysActive = $u.days_active
        $utilization = [math]::Round($daysActive / $script:ReportDays * 100, 1)
        [void]$utilizations.Add($utilization)

        $interactions = $u.interactions
        $codeGen = $u.code_gen
        $codeAccept = $u.code_accept
        $acceptRate = if ($codeGen -gt 0) { [math]::Round($codeAccept / $codeGen * 100, 1) } else { 0 }

        $totalInteractions += $interactions
        $totalCodeGen += $codeGen
        $totalCodeAccept += $codeAccept
        $totalLocAdded += $u.loc_added
        $totalLocDeleted += $u.loc_deleted

        $features = Get-FeaturesUsed -U $u

        [void]$userRows.Add([PSCustomObject]@{
            organization         = $Org
            user_login           = $login
            status               = $status
            plan_type            = if (Get-SafeProperty $seat 'plan_type') { $seat.plan_type } else { "N/A" }
            seat_assigned_date   = $seatDate
            last_activity_date   = $lastDate
            days_inactive        = $daysInactive
            editor               = $editorName
            copilot_model        = $copilotModel
            total_days_active    = $daysActive
            utilization_pct      = $utilization
            total_interactions   = $interactions
            total_code_generations  = $codeGen
            total_code_acceptances  = $codeAccept
            acceptance_rate_pct  = $acceptRate
            total_loc_suggested  = $u.loc_suggested
            total_loc_added      = $u.loc_added
            total_loc_deleted    = $u.loc_deleted
            features_used        = $features
        })
    }

    # Sort: active first, then inactive
    $sorted = @($userRows | Sort-Object @{Expression = { if ($_.status -eq "active") { 0 } else { 1 } }}, user_login)

    # Org summary row
    $totalSeats = $Seats.Count
    $activeCount = $totalSeats - $inactiveLogins.Count
    $avgUtil = if ($utilizations.Count -gt 0) { [math]::Round(($utilizations | Measure-Object -Average).Average, 1) } else { 0 }
    $orgAcceptRate = if ($totalCodeGen -gt 0) { [math]::Round($totalCodeAccept / $totalCodeGen * 100, 1) } else { 0 }

    # Top 3 editors
    $topEditors = ($editorCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3 | ForEach-Object { $_.Key }) -join ", "

    # Inactive list
    $inactiveDisplay = if ($inactiveLogins.Count -le 20) { $inactiveLogins -join ", " } else {
        ($inactiveLogins[0..19] -join ", ") + " (+$($inactiveLogins.Count - 20) more)"
    }
    $inactiveField = if ($inactiveDisplay) { "Inactive: $inactiveDisplay" } else { "None inactive" }

    $summaryRow = [PSCustomObject]@{
        organization         = "-- $Org SUMMARY --"
        user_login           = "$totalSeats seats"
        status               = "$activeCount active / $($inactiveLogins.Count) inactive"
        plan_type            = ""
        seat_assigned_date   = ""
        last_activity_date   = ""
        days_inactive        = ""
        editor               = if ($topEditors) { "Top: $topEditors" } else { "N/A" }
        copilot_model        = ""
        total_days_active    = ""
        utilization_pct      = "avg $avgUtil%"
        total_interactions   = $totalInteractions
        total_code_generations  = $totalCodeGen
        total_code_acceptances  = $totalCodeAccept
        acceptance_rate_pct  = $orgAcceptRate
        total_loc_suggested  = ""
        total_loc_added      = $totalLocAdded
        total_loc_deleted    = $totalLocDeleted
        features_used        = $inactiveField
    }

    $result = [System.Collections.ArrayList]::new()
    foreach ($r in $sorted) { [void]$result.Add($r) }
    [void]$result.Add($summaryRow)
    return ,$result.ToArray()
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Load .env if present
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
    # Re-read params from env if not provided via args
    if (-not $Token) { $Token = $env:GITHUB_TOKEN }
    if (-not $Enterprise) { $Enterprise = $env:ENTERPRISE_SLUG }
    if (-not $Orgs) { $Orgs = $env:ORGS }
}

if (-not $Token) {
    Write-Error "No token. Use -Token or set GITHUB_TOKEN env var."
    exit 1
}

# Validate token
Test-Token -TokenValue $Token

# Discover orgs
$orgList = @()
if ($Orgs) {
    $orgList = @($Orgs -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
elseif ($Enterprise) {
    Write-Host "`n`u{1F3E2} Discovering orgs under: $Enterprise"
    $orgList = @(Get-Organizations -TokenValue $Token -EntSlug $Enterprise)
    if ($orgList.Count -eq 0) {
        Write-Error "No orgs found."
        exit 1
    }
    Write-Host "   Found $($orgList.Count) org(s)`n"
}
else {
    Write-Error "Use -Enterprise or -Orgs."
    exit 1
}

# Prepare output
$OutputDir = (Resolve-Path $OutputDir -ErrorAction SilentlyContinue).Path
if (-not $OutputDir) { $OutputDir = $PWD.Path }
if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }
$today = (Get-Date).ToString("yyyyMMdd")
$csvPath = Join-Path $OutputDir "copilot_report_$today.csv"
Write-Host "Output directory: $OutputDir"

$allRows = [System.Collections.ArrayList]::new()
$rawData = @{}

Write-Host "`n`u{1F4CA} Copilot Report `u{2014} $($script:ReportDays)-day window`n"

foreach ($org in $orgList) {
    Write-Host "`u{1F50D} $org"

    $seats = @(Get-OrgSeats -TokenValue $Token -Org $org)
    Write-Host "   Seats: $($seats.Count)"

    $ndjson = @(Get-NdjsonMetrics -TokenValue $Token -Org $org)
    Write-Host "   NDJSON records: $($ndjson.Count)"

    if ($seats.Count -eq 0 -and $ndjson.Count -eq 0) {
        Write-Host "   `u{26A0} No data `u{2014} skipping." -ForegroundColor Yellow
        continue
    }

    $rows = @(Build-OrgReport -Org $org -Seats $seats -NdjsonRecords $ndjson)
    Write-Host "   Rows generated: $($rows.Count)"
    foreach ($r in $rows) { [void]$allRows.Add($r) }

    if ($RawJson) {
        $rawData[$org] = @{ seats = $seats; ndjson = $ndjson }
    }
}

# Write CSV
Write-Host "`n`u{1F4DD} Writing report ... ($($allRows.Count) total rows)"
if ($allRows.Count -eq 0) {
    Write-Host "  `u{26A0} No data to write. Check API access." -ForegroundColor Yellow
} else {
    $allRows | Select-Object $script:ReportColumns | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
}

$userCount = @($allRows | Where-Object { -not $_.organization.StartsWith("--") }).Count
$summaryCount = $allRows.Count - $userCount
Write-Host "  `u{2705} $userCount users + $summaryCount org summaries `u{2192} $csvPath"

# Raw JSON
if ($RawJson -and $rawData.Count -gt 0) {
    $jsonPath = Join-Path $OutputDir "copilot_raw_$today.json"
    $rawData | ConvertTo-Json -Depth 10 | Set-Content -Path $jsonPath -Encoding UTF8
    Write-Host "  `u{2705} Raw JSON `u{2192} $jsonPath"
}

if (Test-Path $csvPath) {
    Write-Host "`n`u{2705} Done! Report saved to $csvPath`n" -ForegroundColor Green
} else {
    Write-Host "`n`u{26A0} Report file not found at $csvPath - check permissions.`n" -ForegroundColor Yellow
}
