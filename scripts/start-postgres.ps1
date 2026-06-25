# Start PostgreSQL only (for local Python dev on Windows/Linux/macOS)
docker compose up -d postgres

Write-Host "PostgreSQL is starting on localhost:5432"
Write-Host "DATABASE_URL=postgresql+psycopg2://interview_eval:interview_eval@localhost:5432/interview_eval"
Write-Host ""
Write-Host "After adding DATABASE_URL to .env, run:"
Write-Host "  pip install -r requirements.txt"
Write-Host "  python scripts/backfill_postgres.py"
