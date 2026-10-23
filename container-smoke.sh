#!/usr/bin/env bash
set -euo pipefail
image="$1"
platform="$2"
docker run --rm --platform "$platform" --entrypoint python "$image" -m unittest installer.test_installation installer.test_umbrel.UmbrelTests -q
state=$(mktemp -d)
sudo chown 1000:1000 "$state"
export UMBREL_BOOTSTRAP_PASSWORD
UMBREL_BOOTSTRAP_PASSWORD=$(openssl rand -hex 32)
cleanup() { docker rm -f preview-web preview-worker >/dev/null 2>&1 || true; sudo rm -rf "$state"; }
trap cleanup EXIT
docker run -d --name preview-web --platform "$platform" --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --env UMBREL_BOOTSTRAP_PASSWORD --env PUBLIC_URL=http://localhost:18099 -p 127.0.0.1:18099:8099 -v "$state:/state" "$image" >/dev/null
ready() { for attempt in $(seq 1 60); do if curl -fsS http://127.0.0.1:18099/health >/dev/null; then return 0; fi; sleep 2; done; return 1; }
ready
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18099/setup)" = 401
test "$(curl -s -o /dev/null -w '%{http_code}' -u "owner:$UMBREL_BOOTSTRAP_PASSWORD" http://127.0.0.1:18099/setup)" = 200
before=$(sudo sha256sum "$state/config.json")
docker restart preview-web >/dev/null
ready
test "$before" = "$(sudo sha256sum "$state/config.json")"
docker run -d --name preview-worker --platform "$platform" --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --env UMBREL_BOOTSTRAP_PASSWORD -v "$state:/state" "$image" worker >/dev/null
sleep 5
test "$(docker inspect -f '{{.State.Running}}' preview-worker)" = true
echo "Container tests and web/worker restart smoke passed for $platform"
