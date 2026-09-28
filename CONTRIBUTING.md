# Contributing

Bug reports, questions and pull requests are welcome.

## Ground rules

- **Never commit a secret or a personal path.** CI greps for key-shaped strings, and the tests check that
  logs never contain message text. If you need to show an example key, use an obviously fake value.
- **Hooks must fail open and stay fast.** A hook that raises, hangs, or prints garbage can break someone's
  session. New code paths need a test that proves they fall back to "do nothing".
- **Keep the two tools in step.** `claude/` and `codex/` share a design; a change to routing logic normally
  belongs in both routers (and in both pickers for picker changes).

## Development

```bash
python3 -m unittest discover -s tests -t . -v     # offline: no network, no key, no real config
shellcheck -x install.sh
```

The tests run every script against a temporary `$HOME`, so they never touch your real `~/.claude` or
`~/.codex`. Python 3.9+ is supported (CI runs 3.9 and 3.13).

## Pull requests

Describe the behaviour change and how you tested it. Small, focused PRs are reviewed fastest.
