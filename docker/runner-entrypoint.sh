#!/bin/sh
set -eu
umask 077
mkdir -p "$XDG_RUNTIME_DIR/keyring"
dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --fork --nopidfile
gnome-keyring-daemon --start --components=secrets --control-directory="$GNOME_KEYRING_CONTROL" >/dev/null
exec sleep infinity
