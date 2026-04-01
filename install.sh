#!/bin/sh
set -eu

APP_NAME="ml-compose"
SRC_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

INSTALL_DIR="/opt/ml-compose"
LOCK_DIR="$INSTALL_DIR/lock"
BIN_PATH="/usr/local/bin/ml-compose"

echo "Installing ${APP_NAME} from: ${SRC_DIR}"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run this script as root"
  exit 1
fi

mkdir -p "$INSTALL_DIR"
mkdir -p "$LOCK_DIR/state"
mkdir -p "$LOCK_DIR/guard"

install -o root -g root -m 0755 "$SRC_DIR/ml-compose.py" "$INSTALL_DIR/ml-compose.py"
install -o root -g root -m 0644 "$SRC_DIR/common.py" "$INSTALL_DIR/common.py"
install -o root -g root -m 0644 "$SRC_DIR/compose_cli.py" "$INSTALL_DIR/compose_cli.py"
install -o root -g root -m 0644 "$SRC_DIR/compose_runtime.py" "$INSTALL_DIR/compose_runtime.py"
install -o root -g root -m 0644 "$SRC_DIR/gpu_backend.py" "$INSTALL_DIR/gpu_backend.py"
install -o root -g root -m 0644 "$SRC_DIR/gpu_locks.py" "$INSTALL_DIR/gpu_locks.py"
install -o root -g root -m 0644 "$SRC_DIR/policy.py" "$INSTALL_DIR/policy.py"
install -o root -g root -m 0644 "$SRC_DIR/compose-policy.yml" "$INSTALL_DIR/compose-policy.yml"
install -o root -g root -m 0644 "$SRC_DIR/README.md" "$INSTALL_DIR/README.md"
install -o root -g root -m 0644 "$SRC_DIR/LICENSE" "$INSTALL_DIR/LICENSE"

cat > "$BIN_PATH" <<'EOF'
#!/bin/sh
exec /usr/bin/env python3 /opt/ml-compose/ml-compose.py "$@"
EOF

chown root:root "$BIN_PATH"
chmod 0755 "$BIN_PATH"

chown -R root:root "$INSTALL_DIR"
chmod 0755 "$INSTALL_DIR"
chmod 0755 "$LOCK_DIR"
chmod 0755 "$LOCK_DIR/state"
chmod 0755 "$LOCK_DIR/guard"

echo
echo "Installed:"
echo "  app:  $INSTALL_DIR"
echo "  lock: $LOCK_DIR"
echo "  bin:  $BIN_PATH"
echo
echo "Next steps:"
echo "  1. Add a sudoers rule for $BIN_PATH"
echo "  2. Do not add users to the docker group"
echo "  3. Run: sudo ml-compose gpu-status"
