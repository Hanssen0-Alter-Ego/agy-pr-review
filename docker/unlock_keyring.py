"""Owner-only interactive keyring unlock. Never accepts secrets in arguments."""
import getpass
import subprocess
password = getpass.getpass('Container keyring password (hidden): ')
result = subprocess.run(['gnome-keyring-daemon', '--unlock'], input=password.encode(),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
raise SystemExit(result.returncode)
