#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# deploy.sh — one-shot local setup + free-tier cloud deployment
#
# Usage:
#   ./deploy/deploy.sh local              # docker-compose up, seed, smoke-test
#   ./deploy/deploy.sh local down         # tear down local stack
#   ./deploy/deploy.sh cloud              # provision Neon+Upstash+Render, deploy
#   ./deploy/deploy.sh cloud verify       # re-check health of an existing deploy
#   ./deploy/deploy.sh all                # local, then cloud
#
# Requires: docker, docker compose, curl, jq
#
# Credentials: copy deploy/.env.deploy.example -> deploy/.env.deploy and fill
# it in (see that file for what to get, where, and in what order). This
# script NEVER contains secrets itself -- it only reads deploy/.env.deploy at
# run time, which is git-ignored.
#
# Idempotency: every cloud phase checks deploy/.env.deploy for an ID/URL it
# would have written on a previous successful run, and skips re-creating that
# resource if found. This means re-running after a partial failure (e.g. Neon
# succeeded but Render failed) does NOT create duplicate Neon projects -- it
# picks up from where it left off. To force a clean re-provision, blank out
# the relevant *_ID / *_URL fields in .env.deploy.
#
# Honesty note: this script is written against each provider's documented
# API as of this writing. Provider APIs occasionally change field names or
# behavior. Every cloud call below checks the HTTP status and prints the raw
# response body on failure specifically so you can compare it against the
# provider's current API reference (linked in each phase) rather than
# guessing. If a phase fails, the manual dashboard steps in README.md are
# always the fallback.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.deploy"

# ── colors for readability ───────────────────────────────────────────────
c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[31m%s\033[0m\n' "$1"; }
step() { echo; c_yellow "═══ $1 ═══"; }

# ── prerequisite checks ──────────────────────────────────────────────────
check_prereqs() {
    local missing=()
    for cmd in curl jq docker; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        c_red "Missing required tools: ${missing[*]}"
        echo "Install them, then re-run. (On macOS: brew install ${missing[*]})"
        exit 1
    fi
}

# ── env file helpers (read + idempotent write-back) ──────────────────────
load_env() {
    if [ ! -f "$ENV_FILE" ]; then
        c_red "Missing $ENV_FILE"
        echo "Copy deploy/.env.deploy.example to deploy/.env.deploy and fill it in first."
        echo "See that file's header comment for exactly what to get and in what order."
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
}

# Updates (or inserts) a KEY=value line in .env.deploy so re-runs are idempotent.
write_env_var() {
    local key="$1" value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        # portable in-place edit (works on both GNU and BSD sed)
        sed -i.bak "s|^${key}=.*|${key}=\"${value}\"|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "${key}=\"${value}\"" >> "$ENV_FILE"
    fi
    export "${key}=${value}"
}

require_var() {
    local key="$1"
    if [ -z "${!key:-}" ]; then
        c_red "Required variable $key is not set in $ENV_FILE"
        exit 1
    fi
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: local
# ═══════════════════════════════════════════════════════════════════════════
phase_local_up() {
    step "Local: docker compose up"
    cd "$REPO_ROOT"
    docker compose up -d --build
    echo "Waiting for services to become healthy..."
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            c_green "App is healthy."
            break
        fi
        sleep 2
        if [ "$i" -eq 30 ]; then
            c_red "App did not become healthy in time. Logs:"
            docker compose logs --tail=50 app
            exit 1
        fi
    done

    step "Local: seeding sample data + smoke tests"
    if [ -d "$REPO_ROOT/venv" ]; then
        # shellcheck disable=SC1091
        source "$REPO_ROOT/venv/bin/activate"
    fi
    python3 -m pip show httpx >/dev/null 2>&1 || pip3 install --quiet httpx
    BASE_URL=http://localhost:8000 python3 "$REPO_ROOT/tests/test_smoke.py"

    c_green "Local stack is up at http://localhost:8000 (docs: /docs)"
    c_green "Visual search console: http://localhost:8000/ui"
    c_green "Database browser (Adminer): http://localhost:8080  (System: PostgreSQL, Server: postgres, User/Pass/DB: docsearch)"
}

phase_local_down() {
    step "Local: tearing down"
    cd "$REPO_ROOT"
    docker compose down
    c_green "Local stack stopped."
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: Neon (Postgres)
# Docs: https://api-docs.neon.tech/reference/createproject
# ═══════════════════════════════════════════════════════════════════════════
phase_neon() {
    step "Cloud: provisioning Postgres on Neon"
    require_var NEON_API_KEY

    if [ -n "${NEON_PROJECT_ID:-}" ] && [ -n "${NEON_CONNECTION_URI:-}" ]; then
        c_green "Neon project already provisioned (project_id=$NEON_PROJECT_ID). Skipping."
        return
    fi

    local resp
    resp=$(curl -sS -w '\n%{http_code}' -X POST 'https://console.neon.tech/api/v2/projects' \
        -H "Authorization: Bearer ${NEON_API_KEY}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json' \
        --data "{\"project\": {\"name\": \"${NEON_PROJECT_NAME}\", \"region_id\": \"${NEON_REGION}\", \"pg_version\": ${NEON_PG_VERSION}}}")

    local http_code body
    http_code=$(echo "$resp" | tail -n1)
    body=$(echo "$resp" | sed '$d')

    if [ "$http_code" -ge 300 ]; then
        c_red "Neon project creation failed (HTTP $http_code):"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        echo "Reference: https://api-docs.neon.tech/reference/createproject"
        exit 1
    fi

    local project_id connection_uri
    project_id=$(echo "$body" | jq -r '.project.id')
    connection_uri=$(echo "$body" | jq -r '.connection_uris[0].connection_uri')

    write_env_var "NEON_PROJECT_ID" "$project_id"
    write_env_var "NEON_CONNECTION_URI" "$connection_uri"
    c_green "Neon project created: $project_id"
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: Upstash (Redis)
# Docs: https://upstash.com/docs/devops/developer-api/redis/create_database_global
# ═══════════════════════════════════════════════════════════════════════════
phase_upstash() {
    step "Cloud: provisioning Redis on Upstash"
    require_var UPSTASH_EMAIL
    require_var UPSTASH_API_KEY

    if [ -n "${UPSTASH_DATABASE_ID:-}" ] && [ -n "${UPSTASH_REDIS_URL:-}" ]; then
        c_green "Upstash database already provisioned (id=$UPSTASH_DATABASE_ID). Skipping."
        return
    fi

    local auth resp http_code body
    auth=$(printf '%s:%s' "$UPSTASH_EMAIL" "$UPSTASH_API_KEY" | base64 | tr -d '\n')

    resp=$(curl -sS -w '\n%{http_code}' -X POST 'https://api.upstash.com/v2/redis/database' \
        -H "Authorization: Basic ${auth}" \
        -H 'Content-Type: application/json' \
        --data "{\"name\": \"${UPSTASH_DB_NAME}\", \"region\": \"${UPSTASH_REGION}\", \"tls\": true}")

    http_code=$(echo "$resp" | tail -n1)
    body=$(echo "$resp" | sed '$d')

    if [ "$http_code" -ge 300 ]; then
        c_red "Upstash database creation failed (HTTP $http_code):"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        echo "Reference: https://upstash.com/docs/devops/developer-api/redis/create_database_global"
        exit 1
    fi

    local db_id endpoint port password redis_url
    db_id=$(echo "$body" | jq -r '.database_id')
    endpoint=$(echo "$body" | jq -r '.endpoint')
    port=$(echo "$body" | jq -r '.port')
    password=$(echo "$body" | jq -r '.password')
    # rediss:// (TLS) since we requested "tls": true above -- matches this
    # app's redis.asyncio client, which accepts a standard redis:// / rediss:// URL.
    redis_url="rediss://default:${password}@${endpoint}:${port}"

    write_env_var "UPSTASH_DATABASE_ID" "$db_id"
    write_env_var "UPSTASH_REDIS_URL" "$redis_url"
    c_green "Upstash Redis database created: $db_id"
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: build + push image to GHCR
# ═══════════════════════════════════════════════════════════════════════════
phase_build_and_push_image() {
    step "Cloud: building and pushing image to GHCR"
    require_var GITHUB_USERNAME
    require_var GITHUB_TOKEN

    local image="ghcr.io/${GITHUB_USERNAME}/${GHCR_IMAGE_NAME}:latest"

    echo "$GITHUB_TOKEN" | docker login ghcr.io -u "$GITHUB_USERNAME" --password-stdin

    cd "$REPO_ROOT"
    docker build -t "$image" .
    docker push "$image"

    write_env_var "GHCR_IMAGE_FULL" "$image"
    c_green "Image pushed: $image"
    c_yellow "Note: GHCR images are private by default. The next phase creates a"
    c_yellow "Render registry credential using the same token so Render can pull it"
    c_yellow "without you needing to change the package's visibility manually."
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: Render registry credential (so Render can pull the private GHCR image)
# Docs: https://api-docs.render.com/reference/create-registry-credential
# ═══════════════════════════════════════════════════════════════════════════
phase_render_registry_credential() {
    step "Cloud: creating Render registry credential for GHCR"
    require_var RENDER_API_KEY
    require_var RENDER_OWNER_ID
    require_var GITHUB_USERNAME
    require_var GITHUB_TOKEN

    if [ -n "${RENDER_REGISTRY_CREDENTIAL_ID:-}" ]; then
        c_green "Registry credential already exists (id=$RENDER_REGISTRY_CREDENTIAL_ID). Skipping."
        return
    fi

    local resp http_code body
    resp=$(curl -sS -w '\n%{http_code}' -X POST 'https://api.render.com/v1/registrycredentials' \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H 'Content-Type: application/json' \
        --data "{\"name\": \"ghcr-${GHCR_IMAGE_NAME}\", \"ownerId\": \"${RENDER_OWNER_ID}\", \"registry\": \"GITHUB\", \"username\": \"${GITHUB_USERNAME}\", \"authToken\": \"${GITHUB_TOKEN}\"}")

    http_code=$(echo "$resp" | tail -n1)
    body=$(echo "$resp" | sed '$d')

    if [ "$http_code" -ge 300 ]; then
        c_red "Render registry credential creation failed (HTTP $http_code):"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        echo "Reference: https://api-docs.render.com/reference/create-registry-credential"
        echo "Fallback: create it manually in the Render dashboard under Account Settings -> Credentials,"
        echo "then paste its ID into RENDER_REGISTRY_CREDENTIAL_ID in .env.deploy and re-run."
        exit 1
    fi

    local cred_id
    cred_id=$(echo "$body" | jq -r '.id')
    write_env_var "RENDER_REGISTRY_CREDENTIAL_ID" "$cred_id"
    c_green "Registry credential created: $cred_id"
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: create the Render web service
# Docs: https://api-docs.render.com/reference/create-service
# ═══════════════════════════════════════════════════════════════════════════
phase_render_service() {
    step "Cloud: creating Render web service"
    require_var RENDER_API_KEY
    require_var RENDER_OWNER_ID
    require_var NEON_CONNECTION_URI
    require_var UPSTASH_REDIS_URL
    require_var GHCR_IMAGE_FULL

    if [ -n "${RENDER_SERVICE_ID:-}" ]; then
        c_green "Render service already exists (id=$RENDER_SERVICE_ID). Triggering a fresh deploy instead."
        curl -sS -X POST "https://api.render.com/v1/services/${RENDER_SERVICE_ID}/deploys" \
            -H "Authorization: Bearer ${RENDER_API_KEY}" >/dev/null
        return
    fi

    local payload
    payload=$(jq -n \
        --arg name "$RENDER_SERVICE_NAME" \
        --arg ownerId "$RENDER_OWNER_ID" \
        --arg image "$GHCR_IMAGE_FULL" \
        --arg cred "$RENDER_REGISTRY_CREDENTIAL_ID" \
        --arg region "$RENDER_REGION" \
        --arg dbUrl "$NEON_CONNECTION_URI" \
        --arg redisUrl "$UPSTASH_REDIS_URL" \
        '{
            type: "web_service",
            name: $name,
            ownerId: $ownerId,
            image: { ownerId: $ownerId, imagePath: $image, registryCredentialId: $cred },
            envVars: [
                { key: "DATABASE_URL", value: $dbUrl },
                { key: "REDIS_URL", value: $redisUrl },
                { key: "ENVIRONMENT", value: "production" }
            ],
            serviceDetails: {
                runtime: "image",
                plan: "free",
                region: $region,
                envSpecificDetails: {},
                healthCheckPath: "/health"
            }
        }')

    local resp http_code body
    resp=$(curl -sS -w '\n%{http_code}' -X POST 'https://api.render.com/v1/services' \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H 'Content-Type: application/json' \
        --data "$payload")

    http_code=$(echo "$resp" | tail -n1)
    body=$(echo "$resp" | sed '$d')

    if [ "$http_code" -ge 300 ]; then
        c_red "Render service creation failed (HTTP $http_code):"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        echo "Reference: https://api-docs.render.com/reference/create-service"
        echo "Fallback: use the render.yaml blueprint via Render dashboard -> New -> Blueprint"
        echo "(paste DATABASE_URL / REDIS_URL as secrets when prompted)."
        exit 1
    fi

    local service_id service_url
    service_id=$(echo "$body" | jq -r '.service.id // .id')
    service_url="https://$(echo "$body" | jq -r '.service.slug // .slug').onrender.com"

    write_env_var "RENDER_SERVICE_ID" "$service_id"
    write_env_var "RENDER_SERVICE_URL" "$service_url"
    c_green "Render service created: $service_id"
    c_green "URL (may take a few minutes for the first deploy to finish): $service_url"
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE: wait for deploy + verify
# ═══════════════════════════════════════════════════════════════════════════
phase_verify() {
    step "Cloud: verifying deployment"
    require_var RENDER_SERVICE_URL

    echo "Polling ${RENDER_SERVICE_URL}/health (free-tier cold start can take 30-60s)..."
    for i in $(seq 1 40); do
        if curl -sf "${RENDER_SERVICE_URL}/health" 2>/dev/null | tee /tmp/health_check.json | grep -q '"status"'; then
            c_green "Deployed service is responding:"
            jq . /tmp/health_check.json 2>/dev/null || cat /tmp/health_check.json
            return
        fi
        sleep 5
    done
    c_red "Service did not respond after ~3 minutes."
    echo "Check the deploy logs in the Render dashboard: https://dashboard.render.com/web/${RENDER_SERVICE_ID:-}"
    exit 1
}

# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════
main() {
    check_prereqs
    local mode="${1:-}"
    local sub="${2:-}"

    case "$mode" in
        local)
            if [ "$sub" = "down" ]; then
                phase_local_down
            else
                phase_local_up
            fi
            ;;
        cloud)
            load_env
            if [ "$sub" = "verify" ]; then
                phase_verify
            else
                phase_neon
                phase_upstash
                phase_build_and_push_image
                phase_render_registry_credential
                phase_render_service
                echo
                c_yellow "Waiting 20s before first health poll (Render needs a moment to start the deploy)..."
                sleep 20
                phase_verify
                echo
                c_green "═══ Deployment complete ═══"
                c_green "Live URL: ${RENDER_SERVICE_URL}"
                c_green "Health:   ${RENDER_SERVICE_URL}/health"
                c_green "Docs:     ${RENDER_SERVICE_URL}/docs"
                echo
                c_yellow "To validate visually: open ${RENDER_SERVICE_URL}/ui directly"
                c_yellow "(the console is served by the API itself, same as locally)"
                c_yellow "(Adminer is intentionally NOT deployed to the cloud -- exposing DB"
                c_yellow "credentials on a public free-tier URL is a real risk. Use Neon's own"
                c_yellow "SQL editor in its dashboard instead to browse the deployed database.)"
            fi
            ;;
        all)
            phase_local_up
            load_env
            phase_neon
            phase_upstash
            phase_build_and_push_image
            phase_render_registry_credential
            phase_render_service
            sleep 20
            phase_verify
            ;;
        *)
            echo "Usage: $0 {local [down] | cloud [verify] | all}"
            echo "See the header comment in this script for details."
            exit 1
            ;;
    esac
}

main "$@"
