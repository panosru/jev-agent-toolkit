# Key management

The toolkit needs one secret: an **OpenRouter API key** (`sk-or-…`). This project's promise is that it never
stores, prints or logs it. This page explains how it finds the key and how to store it well.

## Resolution order

| Order | Source | Stored where |
|---|---|---|
| 1 | `OPENROUTER_API_KEY` | your shell environment |
| 2 | `JEV_KEY_CMD` | a *command* in your shell environment |
| 3 | `~/.config/jev/key_cmd` (or `$JEV_CONFIG_DIR/key_cmd`) | a *command* in a file (mode 600) |

The first that yields a non-empty value wins. Sources 2 and 3 hold a command, not a secret, so it is safe to
keep that file in a backup or a dotfiles repository.

`jev-router doctor` reports whether a key was found and how many characters it has, and never shows it.

## The command is run without a shell

The command line is split with `shlex` and executed directly: no pipes, redirects, `;`, `&&`, globbing or
command substitution. `~` and `$VARS` are expanded per argument. This removes an injection surface (a writable
`key_cmd` cannot run arbitrary shell) and makes failures predictable. If you need a pipeline or environment
variables, put them in a tiny script and reference the script:

```bash
#!/usr/bin/env bash
# ~/.local/bin/jev-key   (chmod 700)
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
exec sops -d --extract '["openrouter"]["api_key"]' "$HOME/secrets/main.yaml"
```

```bash
./install.sh --key-cmd '~/.local/bin/jev-key'
```

The command has `JEV_KEY_CMD_TIMEOUT` seconds (default 2) to answer, because it runs on every message. Keychain,
`pass` and `sops` answer in well under a second. Tools that unlock interactively or over the network
(1Password, Bitwarden) can be slow; for those, export the key once in your shell instead.

## Recipes

| Store | Add the key | `--key-cmd` |
|---|---|---|
| macOS Keychain | Keychain Access → File → New Password Item, name `jev-openrouter` | `security find-generic-password -s jev-openrouter -w` |
| Linux libsecret | `secret-tool store --label='Jev OpenRouter' service jev-openrouter` (prompts) | `secret-tool lookup service jev-openrouter` |
| pass | `pass insert openrouter/key` (prompts) | `pass show openrouter/key` |
| sops + age | keep it in an encrypted YAML/JSON | a wrapper script as above |
| 1Password CLI | store it as an item | `op read op://Vault/Item/field` (slow: prefer exporting once) |
| Anything else | | any command that prints the key on stdout |

We ran the `JEV_KEY_CMD` mechanism live against the real API using a sops-backed command, and the environment-variable
and saved-command paths in the test suite. The Keychain, libsecret and pass recipes use each tool's standard read
command but were **not** tested by the authors: try one with a dummy value first, and
if macOS shows an access prompt the first time, approve it for the `security` tool.

## Do and don't

- **Do** create a key used only for this, with a small credit balance, so a leak costs cents.
- **Do** rotate it if you ever paste it somewhere by accident: <https://openrouter.ai/keys>.
- **Don't** put the key in `settings.json`, `hooks.json`, a committed `.env`, shell history, screenshots or issue
  reports. The repository's CI greps for key-shaped strings, but that is a safety net, not a plan.
- **Don't** print it while debugging. To test a key command, check the length instead:
  `your-command | wc -c`.

## GUI-launched sessions

Applications started from a launcher or a desktop app inherit no shell profile, so an `export` in `~/.zshrc`
is invisible to them. Use the `key_cmd` file for those (the installer's `--key-cmd` writes it).
