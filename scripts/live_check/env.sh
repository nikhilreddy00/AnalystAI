# Production mode against the scratch Postgres; HS256 tokens with a throwaway local secret (the
# frontend's Supabase is the mock on :54321). Sourced by up.sh; real keys come from .env.
export APP_ENV=production
export DATABASE_URL=postgresql+asyncpg://postgres@localhost:54329/analystai_live
export SUPABASE_URL=http://127.0.0.1:54321
export SUPABASE_JWT_SECRET=live-check-secret-not-a-real-one-0123456789
# one Fernet key per checkout, kept with the data it encrypts (.pgdata/ survives restarts)
[ -f .live/enc.key ] || uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > .live/enc.key
export ENCRYPTION_KEY=$(cat .live/enc.key)
export FRONTEND_URL=http://127.0.0.1:3100
# macOS: SQL Server's ODBC driver loads OpenSSL 3 (scripts/test_dbs.sh says why). macOS drops
# DYLD_* whenever a system binary starts (bash, nohup, env), so uv sets it: `uv run --env-file`
if [ -d /opt/homebrew/opt/openssl@3/lib ]; then
  echo "DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib" > .live/odbc.env
else
  : > .live/odbc.env
fi
