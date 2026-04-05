#!/usr/bin/env bash
# bootstrap_env.sh — detect and configure the build environment for kmp-repair-pipeline
#
# What this script does:
#   1. Validates Java 21 (Temurin) is available — hard requirement for Kotlin 2.x
#   2. Detects Android SDK and writes local.properties with sdk.dir for each KMP project
#   3. Checks Xcode availability (macOS only) — needed for iOS target compilation
#   4. Checks GOOGLE_APPLICATION_CREDENTIALS for Vertex AI LLM provider
#   5. Reports gaps as WARNINGs (not errors) — pipeline degrades gracefully
#
# Usage:
#   source scripts/bootstrap_env.sh       # load env vars into current shell
#   bash scripts/bootstrap_env.sh         # dry-run, just report status

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[ENV]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 1. Java 21 (Temurin) ─────────────────────────────────────────────────────
# The Kotlin 2.x compiler crashes on Java 25 with:
#   IllegalArgumentException: 25.0.1
#   at org.jetbrains.kotlin.com.intellij.util.lang.JavaVersion.parse(...)
# Java 21 LTS (Temurin) is the required baseline.
TEMURIN_21="/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home"
TEMURIN_21_LINUX="/usr/lib/jvm/temurin-21"

if [[ -d "$TEMURIN_21" ]]; then
    export JAVA_HOME="$TEMURIN_21"
    info "Java 21 (Temurin) found at $JAVA_HOME"
elif [[ -d "$TEMURIN_21_LINUX" ]]; then
    export JAVA_HOME="$TEMURIN_21_LINUX"
    info "Java 21 (Temurin) found at $JAVA_HOME"
else
    # Check if current JAVA_HOME is already Java 21
    CURRENT_JAVA="${JAVA_HOME:-}"
    if [[ -n "$CURRENT_JAVA" ]]; then
        JAVA_VER=$("$CURRENT_JAVA/bin/java" -version 2>&1 | head -1 || true)
        if echo "$JAVA_VER" | grep -q '"21'; then
            info "Java 21 already set via JAVA_HOME=$CURRENT_JAVA"
        else
            warn "JAVA_HOME=$CURRENT_JAVA but Java 21 required. Current: $JAVA_VER"
            warn "Install Temurin 21: brew install --cask temurin21"
            warn "Or: sdk install java 21-tem (if using SDKMAN)"
        fi
    else
        warn "Java not found. Install Temurin 21:"
        warn "  macOS:  brew install --cask temurin21"
        warn "  Ubuntu: sudo apt install temurin-21-jdk"
        warn "  SDKMAN: sdk install java 21-tem"
    fi
fi

# Add java to PATH if JAVA_HOME is set
if [[ -n "${JAVA_HOME:-}" && -d "$JAVA_HOME/bin" ]]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

# ── 2. Android SDK ────────────────────────────────────────────────────────────
# Android compilation requires ANDROID_HOME (or ANDROID_SDK_ROOT).
# The pipeline also writes local.properties to each project workspace when
# this variable is set — Gradle reads sdk.dir from there as fallback.
ANDROID_SDK=""

# Priority order: env var > common macOS path > Linux path
if [[ -n "${ANDROID_HOME:-}" && -d "${ANDROID_HOME:-}" ]]; then
    ANDROID_SDK="$ANDROID_HOME"
elif [[ -n "${ANDROID_SDK_ROOT:-}" && -d "${ANDROID_SDK_ROOT:-}" ]]; then
    ANDROID_SDK="$ANDROID_SDK_ROOT"
    export ANDROID_HOME="$ANDROID_SDK"
elif [[ -d "$HOME/Library/Android/sdk" ]]; then
    ANDROID_SDK="$HOME/Library/Android/sdk"
    export ANDROID_HOME="$ANDROID_SDK"
    export ANDROID_SDK_ROOT="$ANDROID_SDK"
elif [[ -d "$HOME/Android/Sdk" ]]; then
    ANDROID_SDK="$HOME/Android/Sdk"
    export ANDROID_HOME="$ANDROID_SDK"
    export ANDROID_SDK_ROOT="$ANDROID_SDK"
fi

if [[ -n "$ANDROID_SDK" ]]; then
    info "Android SDK found: $ANDROID_SDK"
    # Find highest build-tools version
    BT_DIR="$ANDROID_SDK/build-tools"
    if [[ -d "$BT_DIR" ]]; then
        BT_VER=$(ls -v "$BT_DIR" 2>/dev/null | tail -1 || true)
        [[ -n "$BT_VER" ]] && info "Build-tools: $BT_VER"
    fi
else
    warn "Android SDK not found — Android targets will be marked NOT_RUN_ENVIRONMENT_UNAVAILABLE"
    warn "Install Android Studio or set ANDROID_HOME to the SDK path"
    warn "  macOS default: ~/Library/Android/sdk"
fi

# ── 3. Xcode (macOS only) ─────────────────────────────────────────────────────
if [[ "$(uname -s)" == "Darwin" ]]; then
    if command -v xcodebuild &>/dev/null; then
        XCODE_VER=$(xcodebuild -version 2>/dev/null | head -1 || true)
        info "Xcode available: $XCODE_VER"
        # Developer dir for command-line tools
        DEVELOPER_DIR=$(xcode-select -p 2>/dev/null || true)
        [[ -n "$DEVELOPER_DIR" ]] && export DEVELOPER_DIR
    else
        warn "Xcode not found — iOS targets will be marked NOT_RUN_ENVIRONMENT_UNAVAILABLE"
        warn "Install: xcode-select --install  (command-line tools only)"
        warn "Or: download Xcode from the App Store for full iOS builds"
    fi
fi

# ── 4. Vertex AI / GCP credentials ───────────────────────────────────────────
CREDS="${GOOGLE_APPLICATION_CREDENTIALS:-}"
if [[ -n "$CREDS" && -f "$CREDS" ]]; then
    info "GCP credentials: $CREDS"
elif [[ -f "./service-account.json" ]]; then
    export GOOGLE_APPLICATION_CREDENTIALS="./service-account.json"
    info "GCP credentials auto-detected: ./service-account.json"
else
    warn "GOOGLE_APPLICATION_CREDENTIALS not set or file missing"
    warn "LLM calls via Vertex AI will fail. Set:"
    warn "  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json"
fi

# ── 5. KMP_DATABASE_URL ────────────────────────────────────────────────────────
if [[ -z "${KMP_DATABASE_URL:-}" ]]; then
    DEFAULT_DB="postgresql+psycopg2://kmp_repair:kmp_repair_dev@localhost:5432/kmp_repair"
    export KMP_DATABASE_URL="$DEFAULT_DB"
    warn "KMP_DATABASE_URL not set — using default: $DEFAULT_DB"
else
    info "KMP_DATABASE_URL: ${KMP_DATABASE_URL}"
fi

# ── 6. Load .env if present ────────────────────────────────────────────────────
if [[ -f ".env" ]]; then
    # Export non-comment, non-empty lines
    set -o allexport
    # shellcheck disable=SC1091
    source <(grep -v '^#' .env | grep -v '^[[:space:]]*$') 2>/dev/null || true
    set +o allexport
    info ".env loaded"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo " kmp-repair-pipeline environment summary"
echo "────────────────────────────────────────────"
echo " JAVA_HOME            : ${JAVA_HOME:-NOT SET}"
echo " ANDROID_HOME         : ${ANDROID_HOME:-NOT SET}"
echo " GOOGLE_APPLICATION_CREDENTIALS: ${GOOGLE_APPLICATION_CREDENTIALS:-NOT SET}"
echo " KMP_DATABASE_URL     : ${KMP_DATABASE_URL:-NOT SET}"
echo " KMP_LLM_PROVIDER     : ${KMP_LLM_PROVIDER:-NOT SET}"
echo " KMP_LLM_MODEL        : ${KMP_LLM_MODEL:-NOT SET}"
echo "────────────────────────────────────────────"
