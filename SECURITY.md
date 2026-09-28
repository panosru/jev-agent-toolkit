# Security policy

## Reporting a vulnerability

Please use **GitHub private vulnerability reporting**: open the *Security* tab of this repository and choose
*Report a vulnerability*. Do not open a public issue for anything that could expose a credential or let
one process run commands as another. You will get a reply as soon as I can manage, and credit if you want it.

## What is in scope

- Anything that could cause the OpenRouter API key to be written to disk, logged, printed or sent anywhere
  other than OpenRouter.
- Anything that lets untrusted text (a prompt, a pasted document, a skill description) run commands or
  change routing beyond what the documented tags and rules allow.
- Installer bugs that could damage a user's existing Claude Code / Codex configuration.

## Design facts that matter for a review

- **No secrets are stored by this project.** The key is read at call time from `OPENROUTER_API_KEY`,
  `JEV_KEY_CMD`, or a command saved in `~/.config/jev/key_cmd`. A saved command is not a secret.
- The key command is split with `shlex` and executed **without a shell**.
- Logs contain sizes, scores, timings and costs only: never message text (covered by a test).
- Every hook **fails open**: any error means your message goes through untouched.
- While the router or skill picker is ON, each message you type is sent to OpenRouter and TypeSafe (the
  makers of Jev). That is the tool's purpose, so it is OFF until you switch it on.

## If a key leaks

Revoke it at https://openrouter.ai/keys, create a new one, and update whatever your key command reads.
Nothing in this repository needs to change.
