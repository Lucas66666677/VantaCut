param(
  [string]$GeneratorImage = $env:OPENAPI_GENERATOR_IMAGE,
  [string]$OutputRoot = "sdk"
)

$ErrorActionPreference = "Stop"
if (-not $GeneratorImage) { $GeneratorImage = "openapitools/openapi-generator-cli" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is required to run the pinned OpenAPI Generator image." }

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$spec = Join-Path $root "$OutputRoot\openapi.json"
$pythonOutput = Join-Path $root "$OutputRoot\python-generated"
$nodeOutput = Join-Path $root "$OutputRoot\node-generated"
New-Item -ItemType Directory -Force (Split-Path $spec), $pythonOutput, $nodeOutput | Out-Null

# Export without starting Uvicorn; FastAPI owns the OpenAPI contract.
Push-Location (Join-Path $root "backend")
try { python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" | Set-Content -NoNewline -Encoding utf8 $spec }
finally { Pop-Location }

$mount = "$root`:/workspace"
docker run --rm -v $mount $GeneratorImage generate -i "/workspace/$OutputRoot/openapi.json" -g python -o "/workspace/$OutputRoot/python-generated" --additional-properties packageName=aivideo_platform_client
docker run --rm -v $mount $GeneratorImage generate -i "/workspace/$OutputRoot/openapi.json" -g typescript-fetch -o "/workspace/$OutputRoot/node-generated" --additional-properties npmName=@aivideo/platform-client,supportsES6=true

Copy-Item (Join-Path $root "$OutputRoot\templates\python\headless.py") (Join-Path $pythonOutput "headless.py") -Force
Copy-Item (Join-Path $root "$OutputRoot\templates\node\headless.ts") (Join-Path $nodeOutput "headless.ts") -Force
Write-Host "Generated SDKs from $spec. Publish generated packages only after contract tests pass."
