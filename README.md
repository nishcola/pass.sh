# pass.sh

A lightweight, local, encrypted CLI password manager. Entries are stored in a
single encrypted vault file on your machine — nothing is ever sent over the
network, and the master password (or any key derived from it) is never
written to disk.

## Features

- **AES-256-GCM** authenticated encryption for the vault contents.
- **Argon2id** key derivation from your master password (64 MiB memory cost,
  3 iterations, 4 lanes) — tuned to be slow against offline password
  guessing.
- **Tamper detection**: the KDF parameters are bound into the ciphertext as
  authenticated data, so even editing the plaintext header (e.g. downgrading
  the cost parameters) is detected and rejected.
- **Clipboard copy with auto-clear**: `get`/`generate` copy secrets to the
  clipboard by default and wipe them again after a delay, without blocking
  your terminal.
- **Session auto-lock**: unlock once, and nearby commands within 5 minutes
  reuse that session instead of re-prompting; a background agent wipes the
  cached key after 5 minutes of inactivity (or immediately via `pm lock`).
- **Rate-limited unlock attempts** with exponential backoff on repeated wrong
  master passwords.
- **Atomic writes**: the vault is always replaced via write-temp-then-rename,
  so a crash mid-save can't corrupt or partially-write it.

## Requirements

- Python 3.10 or later
- macOS, Linux, or another platform with a Unix-domain-socket-capable OS
  (session auto-lock is skipped on Windows; everything else still works)

## Installation

Clone the repository, then install it (editable installs are recommended
during development so code changes take effect immediately):

```sh
git clone <this-repo-url>
cd pass.sh
pip install -e .
```

This installs the `pm` command on your `PATH`, along with its dependencies
(`cryptography`, `pyperclip`, `click`) as declared in `pyproject.toml`.

If you'd rather manage dependencies yourself (e.g. in an existing virtualenv)
without installing the package:

```sh
pip install -r requirements.txt
```

To also run the test suite, install the `dev` extra:

```sh
pip install -e ".[dev]"
```

### Clipboard support on Linux

`pyperclip` needs a system clipboard tool on Linux. If clipboard copying
fails, install one of:

```sh
sudo apt install xclip      # or
sudo apt install xsel
```

(macOS and Windows use their built-in clipboard APIs — no extra setup
needed.)

## Setup

Create your vault before doing anything else:

```sh
$ pm init
Master password:
Confirm password:
Vault created at /home/you/.passsh/vault.json
```

By default the vault lives at `~/.passsh/vault.json`. Every command accepts
`--vault PATH` if you want a different location (e.g. to keep multiple
vaults, or store it in a synced folder):

```sh
$ pm init --vault ~/work-vault.json
```

The master password is never stored anywhere — only a value derived from it
via Argon2id is used, and only in memory for the duration of a command (or a
cached session; see [Session auto-lock](#session-auto-lock) below).

## Usage

All commands below assume the default vault (`~/.passsh/vault.json`); add
`--vault PATH` to target a different one.

### Add an entry

```sh
$ pm add github --username alice
Master password:
Password for 'github':
Confirm password:
Added 'github'.
```

- `--username TEXT` — optional username/account identifier to store alongside the entry.
- `--notes TEXT` — optional freeform notes.
- `--force` — overwrite an entry that already exists under that name.

### Retrieve an entry

```sh
$ pm get github
Master password:
Name:     github
Username: alice
Password copied to clipboard (clears in 15s).
```

By default the password is copied to the clipboard (not printed), and
auto-clears after 15 seconds — but only if you haven't copied something else
in the meantime.

- `--no-copy` — print the password instead of copying it.
- `--clear-delay SECONDS` — change the auto-clear delay (default: 15).

### List entries

```sh
$ pm list
Master password:
github  (alice)
gitlab  (bob)
```

Only names and usernames are listed — passwords are never shown by `list`.

### Update an entry

```sh
$ pm update github --username alice2
Master password:
Updated 'github'.
```

- `--username TEXT` — set a new username.
- `--notes TEXT` — set new notes.
- `--password` — prompt for and set a new password.

At least one of the above must be given.

### Delete an entry

```sh
$ pm delete github
Master password:
Delete 'github'? [y/N]: y
Deleted 'github'.
```

- `--yes` — skip the confirmation prompt.

### Generate a password

```sh
$ pm generate
Generated password copied to clipboard (clears in 15s).

$ pm generate --no-copy --length 32
K9f#pQ2!zXvR7m@Lc4WsYtB1Nh8Jd0Ea
```

- `--length N` — password length (default: 20).
- `--no-symbols` — letters and digits only.
- `--exclude-ambiguous` — drop visually ambiguous characters (`il1Lo0O`).
- `--copy` / `--no-copy` — copy to clipboard (default) or print instead.
- `--clear-delay SECONDS` — auto-clear delay when copying (default: 15).

Combine with `add`/`update` when you want a fresh random password:

```sh
$ pm generate --no-copy --length 24
<paste the output in when prompted by `pm add`/`pm update --password`>
```

### Lock the session immediately

```sh
$ pm lock
Session locked.
```

Ends the cached session right away instead of waiting for the 5-minute idle
timeout — useful before stepping away from your machine.

## Session auto-lock

Unlocking a vault (deriving the key from your master password via Argon2id)
is deliberately slow, so `pass.sh` avoids repeating it on every command. The
first command against a vault that prompts for and verifies your master
password starts a small background "agent" process (similar in spirit to
`ssh-agent`) that holds the derived key in memory — never on disk — and
serves it to later commands over a local, permission-restricted Unix domain
socket.

- The agent auto-expires and wipes its key after **5 minutes** of
  inactivity; the next command will prompt for your master password again.
- Run `pm lock` to end the session immediately instead of waiting.
- The socket is created in a directory readable only by your user account
  (mode `0700`, socket mode `0600`), so other local user accounts can't read
  the cached key. Other *processes running as you* still could — the same
  trust boundary `ssh-agent`/`gpg-agent` accept.

## Rate-limited unlock attempts

Failed master-password attempts are tracked (as a small non-secret counter,
never the password itself) and trigger exponential backoff — 1s, 2s, 4s...
capped at 60s — before another attempt is accepted, even if that next
attempt uses the correct password. A successful unlock resets the counter.

## Running the tests

```sh
pip install -e ".[dev]"
pytest
```

The test suite covers encryption round-trips and tamper detection at the
crypto/storage layer, session/rate-limit behavior, clipboard auto-clear
(without touching your real clipboard in most cases), and full CLI command
behavior end-to-end, including tampered-vault detection through the CLI
itself.

## Known limitations

- The derived key is held in the agent's RAM and served over local IPC to
  other processes running as your user — this protects against other user
  accounts on the machine, not against another process running as you.
- Python doesn't guarantee zeroing memory or preventing swap; a sufficiently
  privileged attacker with access to a memory dump or swap file could in
  theory recover secrets that were in memory.
- Rate limiting protects the `pm` unlock path specifically, not the vault
  file itself — someone who copies the vault file elsewhere can still
  brute-force it offline (Argon2id's cost is the actual defense there).
- There's no tamper-evident version/rollback protection: restoring an older,
  still-validly-encrypted copy of the vault file isn't detected.
