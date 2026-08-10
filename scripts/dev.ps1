param([ValidateSet("up", "down", "logs", "migrate")][string]$Command = "up")

switch ($Command) {
  "up"      { docker compose up -d --build }
  "down"    { docker compose down }
  "logs"    { docker compose logs -f backend worker frontend }
  "migrate" { docker compose run --rm backend alembic upgrade head }
}

