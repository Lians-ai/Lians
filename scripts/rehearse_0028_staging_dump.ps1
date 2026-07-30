[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DumpPath,

    [Parameter(Mandatory = $true)]
    [switch]$ConfirmSanitized,

    [string]$PostgresImage = "pgvector/pgvector:pg17",

    [switch]$KeepContainer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE."
    }
}

if (-not $ConfirmSanitized) {
    throw "Pass -ConfirmSanitized only after confirming the dump contains no restricted production data."
}

$resolvedDump = (Resolve-Path -LiteralPath $DumpPath).Path
if (-not (Test-Path -LiteralPath $resolvedDump -PathType Leaf)) {
    throw "DumpPath must point to a pg_dump custom-format file."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentmemRoot = Join-Path $repoRoot "agentmem"
$containerSuffix = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$containerName = "lians-migration-rehearsal-$containerSuffix"
$databaseName = "lians_rehearsal"
$databasePassword = [Guid]::NewGuid().ToString("N")
$migrationUser = "lians_migrator"
$migrationPassword = [Guid]::NewGuid().ToString("N")
$containerCreated = $false
$hadDatabaseUrl = Test-Path Env:DATABASE_URL
$priorDatabaseUrl = $env:DATABASE_URL

$integritySql = @'
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM decision_records
        WHERE envelope_id IS NULL
    ) THEN
        RAISE EXCEPTION 'decision_records contains rows without an envelope';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM decision_records AS record
        LEFT JOIN decision_envelopes AS envelope
          ON envelope.id = record.envelope_id
        WHERE envelope.id IS NULL
    ) THEN
        RAISE EXCEPTION 'decision_records contains orphaned envelope references';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM decision_evidence_links AS evidence
        LEFT JOIN decision_envelopes AS envelope
          ON envelope.id = evidence.envelope_id
        WHERE envelope.id IS NULL
    ) THEN
        RAISE EXCEPTION 'decision_evidence_links contains orphaned envelope references';
    END IF;
END
$$;

SELECT
    (SELECT count(*) FROM decision_records) AS decision_records,
    (SELECT count(*) FROM decision_envelopes) AS decision_envelopes,
    (SELECT count(*) FROM decision_evidence_links) AS evidence_links;
'@

$migrationRoleSql = @"
CREATE ROLE $migrationUser LOGIN PASSWORD '$migrationPassword';
ALTER DATABASE $databaseName OWNER TO $migrationUser;
ALTER SCHEMA public OWNER TO $migrationUser;

DO `$`$
DECLARE
    owned_object record;
BEGIN
    FOR owned_object IN
        SELECT
            namespace.nspname AS schema_name,
            class.relname AS object_name,
            class.relkind AS object_kind
        FROM pg_class AS class
        JOIN pg_namespace AS namespace
          ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'public'
          AND class.relkind IN ('r', 'p', 'S', 'v', 'm')
    LOOP
        IF owned_object.object_kind = 'S' THEN
            EXECUTE format(
                'ALTER SEQUENCE %I.%I OWNER TO $migrationUser',
                owned_object.schema_name,
                owned_object.object_name
            );
        ELSIF owned_object.object_kind = 'v' THEN
            EXECUTE format(
                'ALTER VIEW %I.%I OWNER TO $migrationUser',
                owned_object.schema_name,
                owned_object.object_name
            );
        ELSIF owned_object.object_kind = 'm' THEN
            EXECUTE format(
                'ALTER MATERIALIZED VIEW %I.%I OWNER TO $migrationUser',
                owned_object.schema_name,
                owned_object.object_name
            );
        ELSE
            EXECUTE format(
                'ALTER TABLE %I.%I OWNER TO $migrationUser',
                owned_object.schema_name,
                owned_object.object_name
            );
        END IF;
    END LOOP;
END
`$`$;

GRANT ALL ON ALL TABLES IN SCHEMA public TO $migrationUser;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $migrationUser;
"@

try {
    & docker info --format "{{.ServerVersion}}" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not available."
    }

    Invoke-Checked -FilePath "docker" -Arguments @(
        "run",
        "--detach",
        "--name", $containerName,
        "--publish", "127.0.0.1::5432",
        "--env", "POSTGRES_PASSWORD=$databasePassword",
        "--env", "POSTGRES_DB=$databaseName",
        $PostgresImage
    )
    $containerCreated = $true

    $databaseReady = $false
    $consecutiveReadyChecks = 0
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $priorErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & docker exec $containerName psql `
                --username postgres `
                --dbname $databaseName `
                --tuples-only `
                --no-align `
                --command "SELECT 1" *> $null
            $readyExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $priorErrorActionPreference
        }
        if ($readyExitCode -eq 0) {
            $consecutiveReadyChecks++
            if ($consecutiveReadyChecks -ge 3) {
                $databaseReady = $true
                break
            }
        }
        else {
            $consecutiveReadyChecks = 0
        }
        Start-Sleep -Seconds 1
    }
    if (-not $databaseReady) {
        throw "Disposable PostgreSQL did not become ready within 60 seconds."
    }

    Invoke-Checked -FilePath "docker" -Arguments @(
        "cp",
        $resolvedDump,
        "${containerName}:/tmp/staging.dump"
    )
    & docker exec $containerName pg_restore --list /tmp/staging.dump *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "The supplied file is not a readable pg_dump custom-format archive."
    }
    Invoke-Checked -FilePath "docker" -Arguments @(
        "exec",
        $containerName,
        "pg_restore",
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--username", "postgres",
        "--dbname", $databaseName,
        "/tmp/staging.dump"
    )
    Invoke-Checked -FilePath "docker" -Arguments @(
        "exec",
        $containerName,
        "psql",
        "--username", "postgres",
        "--dbname", $databaseName,
        "--set", "ON_ERROR_STOP=1",
        "--command", $migrationRoleSql
    )

    $inspectOutput = & docker inspect $containerName
    $portExitCode = $LASTEXITCODE
    if ($portExitCode -ne 0) {
        throw "Could not inspect the disposable PostgreSQL container."
    }
    $inspectData = $inspectOutput | ConvertFrom-Json
    $portBindings = $inspectData[0].NetworkSettings.Ports."5432/tcp"
    $portOutput = [string]$portBindings[0].HostPort
    if ($portOutput -notmatch "^\d+$") {
        throw "Could not determine the disposable PostgreSQL host port."
    }
    $databasePort = $portOutput
    $env:DATABASE_URL = (
        "postgresql+asyncpg://{0}:{1}@127.0.0.1:{2}/{3}" -f
        $migrationUser,
        $migrationPassword,
        $databasePort,
        $databaseName
    )

    Push-Location $agentmemRoot
    try {
        Write-Output "Restored revision:"
        $restoredRevisionOutput = & python -m alembic current
        $restoredRevisionExitCode = $LASTEXITCODE
        $restoredRevisionOutput | Write-Output
        if ($restoredRevisionExitCode -ne 0) {
            throw "Could not read the restored Alembic revision."
        }
        $restoredRevision = $restoredRevisionOutput -join [Environment]::NewLine
        if ($restoredRevision -notmatch "(?m)^0027_agent_experiences\s*$") {
            throw (
                "Expected a pre-migration dump at 0027_agent_experiences. " +
                "Refusing to rehearse a downgrade against a different revision."
            )
        }

        Write-Output "Upgrade to head:"
        Invoke-Checked -FilePath "python" -Arguments @("-m", "alembic", "upgrade", "head")
        Invoke-Checked -FilePath "docker" -Arguments @(
            "exec",
            $containerName,
            "psql",
            "--username", "postgres",
            "--dbname", $databaseName,
            "--set", "ON_ERROR_STOP=1",
            "--command", $integritySql
        )

        Write-Output "Downgrade 0028, then upgrade it again:"
        Invoke-Checked -FilePath "python" -Arguments @(
            "-m", "alembic", "downgrade", "0027_agent_experiences"
        )
        Invoke-Checked -FilePath "python" -Arguments @("-m", "alembic", "upgrade", "head")
        Invoke-Checked -FilePath "docker" -Arguments @(
            "exec",
            $containerName,
            "psql",
            "--username", "postgres",
            "--dbname", $databaseName,
            "--set", "ON_ERROR_STOP=1",
            "--command", $integritySql
        )

        Write-Output "Final revision:"
        Invoke-Checked -FilePath "python" -Arguments @("-m", "alembic", "current")
    }
    finally {
        Pop-Location
    }

    Write-Output "Staging-data rehearsal passed in disposable container $containerName."
}
finally {
    if ($hadDatabaseUrl) {
        $env:DATABASE_URL = $priorDatabaseUrl
    }
    else {
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }

    if ($containerCreated -and -not $KeepContainer) {
        if ($containerName -notlike "lians-migration-rehearsal-*") {
            throw "Refusing to remove an unexpected container name."
        }
        & docker rm --force $containerName *> $null
    }
    elseif ($containerCreated) {
        Write-Output "Kept disposable container $containerName for inspection."
    }
}
