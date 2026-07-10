#!/usr/bin/env bash
set -euo pipefail

PROFILES=(
  feedback_vanilla_1080p
  lookdev_vanilla_unbound_1080p
  feedback_legendary_rt_1080p
  feedback_legendary_rt_unbound_1080p
  lookdev_legendary_rt_bliss_1080p
  preview_legendary_rt_solas_1080p
  lookdev_legendary_rt_unbound_seuspbr_1080p
  lookdev_legendary_rt_solas_seuspbr_1080p
  feedback_modernarch_1080p
  lookdev_modernarch_unbound_1080p
  lookdev_optimum_1080p
  preview_optimum_unbound_1080p
  lookdev_patrix_full_1080p
  lookdev_patrix_full_unbound_1080p
  lookdev_stylista_1080p
  preview_stylista_unbound_1080p
  lookdev_prettyrealistic_1080p
  lookdev_prettyrealistic_unbound_1080p
  lookdev_style_vanilla_1080p
  lookdev_style_stylista_1080p
  lookdev_style_reimagined_1080p
  lookdev_style_ashen_1080p
  lookdev_style_simplified_1080p
  lookdev_style_quadral_1080p
  lookdev_style_bare_bones_pbr_1080p
  lookdev_style_natural_1080p
)

PROFILE_SOURCE_KIND="builtin"
PROFILE_SOURCE_PATH=""
PROFILE_SOURCE_SHA256=""
if [[ -n "${BATCH_PROFILES_FILE+x}" ]]; then
  if [[ -z "$BATCH_PROFILES_FILE" ]]; then
    echo "BATCH_PROFILES_FILE must not be empty when set" >&2
    exit 2
  fi
  if [[ ! -f "$BATCH_PROFILES_FILE" || ! -r "$BATCH_PROFILES_FILE" ]]; then
    echo "BATCH_PROFILES_FILE must be a readable regular file: $BATCH_PROFILES_FILE" >&2
    exit 2
  fi

  PROFILE_SOURCE_KIND="file"
  PROFILE_SOURCE_PATH="$(realpath "$BATCH_PROFILES_FILE")"
  PROFILE_SOURCE_SHA256="$(sha256sum "$PROFILE_SOURCE_PATH" | awk '{print $1}')"
  PROFILES=()
  declare -A seen_profiles=()
  line_number=0
  while IFS= read -r profile || [[ -n "$profile" ]]; do
    ((line_number += 1))
    [[ -z "$profile" ]] && continue
    if [[ ! "$profile" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
      echo "invalid profile name at $PROFILE_SOURCE_PATH:$line_number: $profile" >&2
      exit 2
    fi
    if [[ -n "${seen_profiles[$profile]+x}" ]]; then
      echo "duplicate profile at $PROFILE_SOURCE_PATH:$line_number: $profile" >&2
      exit 2
    fi
    seen_profiles["$profile"]=1
    PROFILES+=("$profile")
  done < "$PROFILE_SOURCE_PATH"
  if [[ ${#PROFILES[@]} -eq 0 ]]; then
    echo "BATCH_PROFILES_FILE does not contain any profiles: $PROFILE_SOURCE_PATH" >&2
    exit 2
  fi
  if [[ "$(sha256sum "$PROFILE_SOURCE_PATH" | awk '{print $1}')" != "$PROFILE_SOURCE_SHA256" ]]; then
    echo "BATCH_PROFILES_FILE changed while it was being read: $PROFILE_SOURCE_PATH" >&2
    exit 2
  fi
fi

if [[ "${1:-}" == "--print-profiles" ]]; then
  printf '%s\n' "${PROFILES[@]}"
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "usage: scripts/lookdev_render_batch.sh [--print-profiles]" >&2
  exit 2
fi

: "${EXPECTED_SYNC:?Set EXPECTED_SYNC to the full synced commit hash}"
: "${BATCH_ID:?Set BATCH_ID to a unique filesystem-safe identifier}"

ROOT_DIR="${MCDATA_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MCDATA_TMP_ROOT="${MCDATA_TMP_ROOT:-/root/nas/bigdata1/tmp/mcdata}"
DISPLAY_NUM="${DISPLAY_NUM:-:77}"
SERVER_PORT="${SERVER_PORT:-25800}"
STRATEGY="${STRATEGY:-lookdev_showcase_60s}"
GAME_VERSION="26.2"
DURATION="60"
QA_FRAMES="12"
LANE="$BATCH_ID"
RUN_ROOT="$MCDATA_TMP_ROOT/runs/$BATCH_ID"
BATCH_DIR="$RUN_ROOT/_batch"
GLOBAL_LOG="$MCDATA_TMP_ROOT/logs/${BATCH_ID}.log"
PYTHON="${PYTHON:-python3}"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ ! "$EXPECTED_SYNC" =~ ^[0-9a-f]{40}$ ]]; then
  echo "EXPECTED_SYNC must be a full 40-character lowercase commit hash" >&2
  exit 2
fi
if [[ ! "$BATCH_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "BATCH_ID must contain only letters, digits, underscores, and hyphens" >&2
  exit 2
fi
if [[ ! "$DISPLAY_NUM" =~ ^:[0-9]+$ ]]; then
  echo "DISPLAY_NUM must look like :77" >&2
  exit 2
fi
if [[ ! "$SERVER_PORT" =~ ^[0-9]+$ ]] || (( SERVER_PORT < 1024 || SERVER_PORT > 65535 )); then
  echo "SERVER_PORT must be an unprivileged TCP port" >&2
  exit 2
fi
if [[ ${#PROFILES[@]} -eq 0 ]]; then
  echo "internal error: selected profile list is empty" >&2
  exit 2
fi

mkdir -p "$MCDATA_TMP_ROOT/runs" "$MCDATA_TMP_ROOT/logs"
if ! mkdir "$RUN_ROOT"; then
  echo "Refusing to reuse batch output root: $RUN_ROOT" >&2
  exit 2
fi
mkdir -p \
  "$BATCH_DIR/bootstrap_before" \
  "$BATCH_DIR/config_snapshot" \
  "$BATCH_DIR/logs" \
  "$BATCH_DIR/markers" \
  "$BATCH_DIR/records"
: > "$GLOBAL_LOG"
exec > >(tee -a "$GLOBAL_LOG") 2>&1

preflight_fail() {
  local message="$1"
  echo "PREFLIGHT_FAIL:$message"
  BATCH_ERROR="$message" BATCH_COMPLETION="$BATCH_DIR/completion.json" \
    BATCH_STARTED_AT="$STARTED_AT" BATCH_EXPECTED_SYNC="$EXPECTED_SYNC" \
    BATCH_ID_VALUE="$BATCH_ID" BATCH_LANE="$LANE" BATCH_PORT="$SERVER_PORT" \
    "$PYTHON" - <<'PY'
import json
import os
from pathlib import Path

record = {
    "status": "preflight_failed",
    "passed": False,
    "error": os.environ["BATCH_ERROR"],
    "started_at": os.environ["BATCH_STARTED_AT"],
    "batch_id": os.environ["BATCH_ID_VALUE"],
    "lane": os.environ["BATCH_LANE"],
    "server_port": int(os.environ["BATCH_PORT"]),
    "expected_sync": os.environ["BATCH_EXPECTED_SYNC"],
}
Path(os.environ["BATCH_COMPLETION"]).write_text(
    json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
  exit 2
}

check_hash_file() {
  local hashes="$1"
  local output="$2"
  sha256sum -c "$hashes" > "$output" 2>&1
}

port_is_free() {
  "$PYTHON" - "$SERVER_PORT" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
PY
}

cd "$ROOT_DIR"
export MCDATA_TMP_ROOT
source scripts/mcdata_env.sh >/dev/null
export MCDATA_OUTPUT_DIR="$RUN_ROOT"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

[[ -f .sync_commit ]] || preflight_fail ".sync_commit is missing"
ACTUAL_SYNC="$(tr -d '[:space:]' < .sync_commit)"
[[ "$ACTUAL_SYNC" == "$EXPECTED_SYNC" ]] || \
  preflight_fail "sync mismatch: expected $EXPECTED_SYNC, found $ACTUAL_SYNC"

if [[ "$(printf '%s\n' "${PROFILES[@]}" | sort -u | wc -l)" -ne ${#PROFILES[@]} ]]; then
  preflight_fail "selected profile list contains duplicates"
fi
if pgrep -af '[m]cdata.cli bootstrap' > "$BATCH_DIR/bootstrap_processes.txt"; then
  preflight_fail "a bootstrap process is still running"
fi
if pgrep -af '[m]cdata.cli run-matrix|[m]cdata.cli run ' > "$BATCH_DIR/render_processes.txt"; then
  preflight_fail "another mcdata render process is active"
fi
if pgrep -af '[f]fmpeg.*x11grab|[n]et.minecraft.client.main.Main' \
  > "$BATCH_DIR/display_processes.txt"; then
  preflight_fail "the render display already has a capture or Minecraft client"
fi
command -v glxinfo >/dev/null || preflight_fail "glxinfo is unavailable"
DISPLAY="$DISPLAY_NUM" glxinfo -B > "$BATCH_DIR/glxinfo.txt" 2>&1 || \
  preflight_fail "display $DISPLAY_NUM is unavailable"
command -v nvidia-smi >/dev/null || preflight_fail "nvidia-smi is unavailable"
nvidia-smi -L > "$BATCH_DIR/nvidia-smi.txt" 2>&1 || \
  preflight_fail "NVIDIA GPU is unavailable"
port_is_free || preflight_fail "fixed server port $SERVER_PORT is already in use"

PROFILES_FILE="$BATCH_DIR/profiles.txt"
printf '%s\n' "${PROFILES[@]}" > "$PROFILES_FILE"

if ! "$PYTHON" - "$ROOT_DIR/configs" "$MCDATA_WORK_DIR" "$PROFILES_FILE" \
  > "$BATCH_DIR/instance_preflight.json" <<'PY'
import json
import sys
from pathlib import Path

from mcdata.config import load_profile

config_dir = Path(sys.argv[1])
work_dir = Path(sys.argv[2])
profiles = [line.strip() for line in Path(sys.argv[3]).read_text().splitlines() if line.strip()]
records = []
for profile_name in profiles:
    profile = load_profile(config_dir, profile_name)
    instance = work_dir / profile_name
    manifest_path = instance / "mcdata_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing completed bootstrap manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("profile") != profile_name:
        raise SystemExit(f"bootstrap manifest profile mismatch: {profile_name}")
    if manifest.get("minecraft_version") != "26.2":
        raise SystemExit(f"bootstrap manifest version mismatch: {profile_name}")
    if manifest.get("asset_set") != profile.get("asset_set"):
        raise SystemExit(f"bootstrap manifest asset-set mismatch: {profile_name}")
    required = [instance / "options.txt"]
    required.extend(instance / "mods" / name for name in manifest.get("mods", []))
    required.extend(
        instance / "resourcepacks" / name for name in manifest.get("resourcepacks", [])
    )
    shaderpack = manifest.get("shaderpack")
    if shaderpack:
        required.append(instance / "shaderpacks" / shaderpack)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"incomplete instance {profile_name}: {missing}")
    records.append(
        {
            "profile": profile_name,
            "manifest": str(manifest_path),
            "asset_set": manifest.get("asset_set"),
            "resourcepack_count": len(manifest.get("resourcepacks", [])),
            "shaderpack": shaderpack,
        }
    )
print(json.dumps(records, indent=2, sort_keys=True))
PY
then
  preflight_fail "one or more accepted profiles are not completely bootstrapped"
fi

CONFIG_HASHES="$BATCH_DIR/config_inputs.sha256"
sha256sum \
  "$ROOT_DIR/.sync_commit" \
  "$ROOT_DIR/configs/actions.yml" \
  "$ROOT_DIR/configs/asset_sets.yml" \
  "$ROOT_DIR/configs/profiles.yml" \
  "$ROOT_DIR/configs/scene.yml" \
  > "$CONFIG_HASHES"
cp \
  "$ROOT_DIR/.sync_commit" \
  "$ROOT_DIR/configs/actions.yml" \
  "$ROOT_DIR/configs/asset_sets.yml" \
  "$ROOT_DIR/configs/profiles.yml" \
  "$ROOT_DIR/configs/scene.yml" \
  "$BATCH_DIR/config_snapshot/"

BOOTSTRAP_HASHES="$BATCH_DIR/bootstrap_manifests.sha256"
: > "$BOOTSTRAP_HASHES"
for profile in "${PROFILES[@]}"; do
  manifest="$MCDATA_WORK_DIR/$profile/mcdata_manifest.json"
  sha256sum "$manifest" >> "$BOOTSTRAP_HASHES"
  sha256sum "$manifest" > "$BATCH_DIR/bootstrap_before/$profile.sha256"
done

check_hash_file "$CONFIG_HASHES" "$BATCH_DIR/config_preflight.txt" || \
  preflight_fail "configuration changed during preflight"
check_hash_file "$BOOTSTRAP_HASHES" "$BATCH_DIR/bootstrap_preflight.txt" || \
  preflight_fail "an instance manifest changed during preflight"

SHARED_TRAJECTORY="$RUN_ROOT/trajectories/${STRATEGY}_matrix_${LANE}.json"
REGENERATED_TRAJECTORY="$BATCH_DIR/trajectory.regenerated.json"
FROZEN_TRAJECTORY="$BATCH_DIR/trajectory.frozen.json"
mkdir -p "$(dirname "$SHARED_TRAJECTORY")"
"$PYTHON" -m mcdata.cli make-trajectory "$STRATEGY" \
  --root "$ROOT_DIR" --out "$SHARED_TRAJECTORY" || \
  preflight_fail "failed to generate the shared trajectory"
"$PYTHON" -m mcdata.cli make-trajectory "$STRATEGY" \
  --root "$ROOT_DIR" --out "$REGENERATED_TRAJECTORY" || \
  preflight_fail "failed to regenerate the trajectory determinism probe"
cmp -s "$SHARED_TRAJECTORY" "$REGENERATED_TRAJECTORY" || \
  preflight_fail "trajectory generation is not deterministic"
cp "$SHARED_TRAJECTORY" "$FROZEN_TRAJECTORY"
EXPECTED_TRAJECTORY_SHA256="$(sha256sum "$FROZEN_TRAJECTORY" | awk '{print $1}')"

if ! "$PYTHON" - "$FROZEN_TRAJECTORY" > "$BATCH_DIR/trajectory_preflight.json" <<'PY'
import json
import sys
from pathlib import Path

trajectory = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
duration = float(trajectory.get("duration_sec") or 0.0)
if trajectory.get("type") != "astar_walk":
    raise SystemExit("look-dev trajectory must be astar_walk")
if not 58.5 <= duration <= 60.5:
    raise SystemExit(f"look-dev trajectory duration is {duration}, expected about 60 seconds")
if not trajectory.get("route") or not trajectory.get("events"):
    raise SystemExit("look-dev trajectory has no route or events")
print(json.dumps({
    "type": trajectory["type"],
    "duration_sec": duration,
    "event_count": len(trajectory["events"]),
    "route_point_count": len(trajectory["route"]),
}, indent=2, sort_keys=True))
PY
then
  preflight_fail "the shared trajectory does not satisfy the 60-second contract"
fi

BATCH_MANIFEST="$BATCH_DIR/batch_manifest.json"
BATCH_MANIFEST="$BATCH_MANIFEST" PROFILES_FILE="$PROFILES_FILE" \
  BATCH_STARTED_AT="$STARTED_AT" BATCH_ID_VALUE="$BATCH_ID" \
  BATCH_LANE="$LANE" BATCH_PORT="$SERVER_PORT" BATCH_DISPLAY="$DISPLAY_NUM" \
  BATCH_STRATEGY="$STRATEGY" BATCH_EXPECTED_SYNC="$EXPECTED_SYNC" \
  BATCH_TRAJECTORY_SHA="$EXPECTED_TRAJECTORY_SHA256" \
  BATCH_PROFILE_SOURCE_KIND="$PROFILE_SOURCE_KIND" \
  BATCH_PROFILE_SOURCE_PATH="$PROFILE_SOURCE_PATH" \
  BATCH_PROFILE_SOURCE_SHA256="$PROFILE_SOURCE_SHA256" \
  BATCH_PROFILE_COUNT="${#PROFILES[@]}" "$PYTHON" - <<'PY'
import json
import os
from pathlib import Path

profiles = [
    line.strip()
    for line in Path(os.environ["PROFILES_FILE"]).read_text().splitlines()
    if line.strip()
]
profile_count = int(os.environ["BATCH_PROFILE_COUNT"])
if len(profiles) != profile_count:
    raise SystemExit(
        f"profile count drifted while writing batch manifest: {len(profiles)} != {profile_count}"
    )
profile_source = {"kind": os.environ["BATCH_PROFILE_SOURCE_KIND"]}
if profile_source["kind"] == "file":
    profile_source.update(
        {
            "path": os.environ["BATCH_PROFILE_SOURCE_PATH"],
            "sha256": os.environ["BATCH_PROFILE_SOURCE_SHA256"],
        }
    )
manifest = {
    "started_at": os.environ["BATCH_STARTED_AT"],
    "batch_id": os.environ["BATCH_ID_VALUE"],
    "lane": os.environ["BATCH_LANE"],
    "server_port": int(os.environ["BATCH_PORT"]),
    "display": os.environ["BATCH_DISPLAY"],
    "duration_sec": 60,
    "strategy": os.environ["BATCH_STRATEGY"],
    "expected_sync": os.environ["BATCH_EXPECTED_SYNC"],
    "expected_trajectory_sha256": os.environ["BATCH_TRAJECTORY_SHA"],
    "profile_count": profile_count,
    "profile_source": profile_source,
    "profiles": profiles,
}
Path(os.environ["BATCH_MANIFEST"]).write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

RESULTS="$BATCH_DIR/results.jsonl"
: > "$RESULTS"
integrity_drift=false

for index in "${!PROFILES[@]}"; do
  profile="${PROFILES[$index]}"
  ordinal="$(printf '%02d' "$((index + 1))")"
  prefix="${ordinal}_${profile}"
  marker="$BATCH_DIR/markers/$prefix"
  run_log="$BATCH_DIR/logs/$prefix.run.log"
  qa_log="$BATCH_DIR/logs/$prefix.qa.log"
  validation_log="$BATCH_DIR/logs/$prefix.validation.log"
  candidates_file="$BATCH_DIR/records/$prefix.candidates.txt"
  record="$BATCH_DIR/records/$prefix.json"

  if ! check_hash_file "$CONFIG_HASHES" "$BATCH_DIR/logs/$prefix.config.before.txt"; then
    echo "INTEGRITY_DRIFT:$profile:config_before"
    integrity_drift=true
    break
  fi
  if ! check_hash_file \
    "$BOOTSTRAP_HASHES" "$BATCH_DIR/logs/$prefix.bootstrap.before.txt"; then
    echo "INTEGRITY_DRIFT:$profile:bootstrap_before"
    integrity_drift=true
    break
  fi

  touch "$marker"
  echo "START:$ordinal:${#PROFILES[@]}:$profile:port=$SERVER_PORT:lane=$LANE"
  set +e
  "$PYTHON" -m mcdata.cli run-matrix \
    --root "$ROOT_DIR" \
    --profiles "$profile" \
    --strategy "$STRATEGY" \
    --duration "$DURATION" \
    --capture \
    --with-server \
    --replay-actions \
    --display "$DISPLAY_NUM" \
    --server-port "$SERVER_PORT" \
    --lane "$LANE" \
    --game-version "$GAME_VERSION" \
    --no-bootstrap \
    2>&1 | tee "$run_log"
  render_rc=${PIPESTATUS[0]}
  set -e

  find "$RUN_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name "*_${profile}__${LANE}" -newer "$marker" -print \
    | sort > "$candidates_file"
  mapfile -t candidates < "$candidates_file"
  run_dir=""
  if [[ ${#candidates[@]} -eq 1 ]]; then
    run_dir="${candidates[0]}"
  fi

  qa_rc=127
  if [[ -n "$run_dir" && -s "$run_dir/capture.mp4" ]]; then
    set +e
    "$PYTHON" -m mcdata.cli qa-run "$run_dir" --frames "$QA_FRAMES" \
      2>&1 | tee "$qa_log"
    qa_rc=${PIPESTATUS[0]}
    set -e
  else
    echo "QA_SKIPPED:$profile:no non-empty uniquely located capture" | tee "$qa_log"
  fi

  config_unchanged=true
  bootstrap_set_unchanged=true
  if ! check_hash_file "$CONFIG_HASHES" "$BATCH_DIR/logs/$prefix.config.after.txt"; then
    config_unchanged=false
  fi
  if ! check_hash_file \
    "$BOOTSTRAP_HASHES" "$BATCH_DIR/logs/$prefix.bootstrap.after.txt"; then
    bootstrap_set_unchanged=false
  fi

  bootstrap_sha="$(awk '{print $1}' "$BATCH_DIR/bootstrap_before/$profile.sha256")"
  set +e
  "$PYTHON" -m mcdata.qa.lookdev_batch validate-run \
    --profile "$profile" \
    --run-dir "$run_dir" \
    --render-rc "$render_rc" \
    --qa-rc "$qa_rc" \
    --unique-run-count "${#candidates[@]}" \
    --expected-trajectory-sha256 "$EXPECTED_TRAJECTORY_SHA256" \
    --expected-sync "$EXPECTED_SYNC" \
    --shared-trajectory "$SHARED_TRAJECTORY" \
    --instance-manifest "$MCDATA_WORK_DIR/$profile/mcdata_manifest.json" \
    --bootstrap-manifest-sha256 "$bootstrap_sha" \
    --lane "$LANE" \
    --strategy "$STRATEGY" \
    --server-port "$SERVER_PORT" \
    --display "$DISPLAY_NUM" \
    --config-unchanged "$config_unchanged" \
    --bootstrap-set-unchanged "$bootstrap_set_unchanged" \
    --record "$record" \
    --results "$RESULTS" \
    2>&1 | tee "$validation_log"
  validation_rc=${PIPESTATUS[0]}
  set -e
  echo "DONE:$profile:render_rc=$render_rc:qa_rc=$qa_rc:validation_rc=$validation_rc"

  if [[ "$config_unchanged" != true || "$bootstrap_set_unchanged" != true ]]; then
    echo "INTEGRITY_DRIFT:$profile:after_run"
    integrity_drift=true
    break
  fi
done

config_final=true
bootstrap_final=true
if ! check_hash_file "$CONFIG_HASHES" "$BATCH_DIR/config_final.txt"; then
  config_final=false
fi
if ! check_hash_file "$BOOTSTRAP_HASHES" "$BATCH_DIR/bootstrap_final.txt"; then
  bootstrap_final=false
fi
if [[ "$integrity_drift" == true ]]; then
  config_final=false
  bootstrap_final=false
fi

set +e
"$PYTHON" -m mcdata.qa.lookdev_batch complete-batch \
  --results "$RESULTS" \
  --expected-profiles "$PROFILES_FILE" \
  --output "$BATCH_DIR/completion.json" \
  --config-unchanged "$config_final" \
  --bootstrap-unchanged "$bootstrap_final" \
  --batch-id "$BATCH_ID" \
  --lane "$LANE" \
  --server-port "$SERVER_PORT" \
  --expected-sync "$EXPECTED_SYNC"
completion_rc=$?
set -e
exit "$completion_rc"
