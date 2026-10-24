# Checking the first commit before anything is published

A repository is published by pushing it. Whatever is in the history at that moment is public, and a secret that was ever committed stays recoverable even after it is deleted in a later commit. So check the **first commit** (and every commit that will be pushed) before the first push, and rotate any secret that slips through instead of trying to hide it.

Nothing in this checklist pushes or publishes anything. Run it from the repository folder (PowerShell; the same commands work in Git Bash).

**State on 25 Oct 2026:** a local repository exists with one baseline commit, no remote and nothing pushed. That commit was checked with the commands below and held no `.env`, no `data/`, no `*.db`, no logs, no generated test data and no token or key patterns. Run the checklist again on whatever you actually push.

## 1. Nothing is pushed yet

```powershell
git remote -v                       # expect: nothing printed (no remote)
git status -sb                      # expect: "## main" with no "...origin/main"
```

## 2. The files that must never be tracked

```powershell
git ls-files .env                   # expect: nothing
git ls-files | Select-String -Pattern '(^|/)\.env(\..*)?$' | Where-Object { $_ -notmatch '\.env\.example$' }   # expect: nothing
git ls-files | Select-String -Pattern '\.(db|db-wal|db-shm|db-journal|sqlite|sqlite3|lock)$|\.before-(schema|restore)|\.restore-tmp'   # expect: nothing
git ls-files | Select-String -Pattern '^data/|\.log$|^\.venv/|__pycache__|\.pytest_cache'                       # expect: nothing
git ls-files | Select-String -Pattern 'manifest\.jsonl|days_state\.json|last_run\.log'                          # generated test data: expect nothing
git ls-files | Select-String -Pattern '^ui-design/'                                                              # design exports of unknown origin: expect nothing until their licence is decided
git check-ignore -v .env data/erp_agents.db tools/testdata/manifest.jsonl     # each must print the rule that ignores it
```

## 3. Secrets and personal data inside tracked files

```powershell
# the same scan the CI runs (a token or key looks committed)
git grep -nIE '(EAA[A-Za-z0-9]{40,}|[0-9]{8,10}:[A-Za-z0-9_-]{35}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' -- . ':!docs/*.md'
# provider API keys, bearer tokens, passwords written as values
git grep -nIE '(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|(api[_-]?key|secret|password|token)\s*[:=]\s*["'']?[A-Za-z0-9+/_-]{16,})' -- . ':!tests' ':!docs'
# personal addresses, private network addresses, machine paths
git grep -nIE '(gmail\.com|outlook\.com|yahoo\.com|192\.168\.[0-9]+\.[0-9]+|C:\\Users\\)' -- . ':!LICENSE' ':!tests/test_assistant.py' ':!tests/test_hardening.py' ':!tests/test_agents.py'
# the settings files must refer to secrets as ${NAME}, never hold them
python -m erp_agents doctor         # "no secret is written into the settings file" (the check reads the settings file you point it at)
```

Every match needs a look. Expected and fine: example numbers such as `919800000000` in tests and the example settings, `demo-owner-token` (the documented demo key, accepted only on the server machine), RFC 1918 addresses used as test inputs in the three test files excluded above.

## 4. What is in the commit, by size and by folder

```powershell
git ls-files | Measure-Object -Line                         # the baseline was 152 files
git ls-files -z | ForEach-Object { $_ -split "`0" } | Where-Object { $_ } | ForEach-Object { Get-Item $_ } | Sort-Object Length -Descending | Select-Object -First 10 FullName, Length
git ls-files | ForEach-Object { ($_ -split '/')[0] } | Group-Object | Sort-Object Count -Descending | Format-Table Name, Count
```

Anything over a few hundred KB that is not source or a document needs an explanation. Folders expected at the top level: `.github`, `config`, `docs`, `src`, `tests`, `tools`, plus the top-level files `README.md`, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `pyproject.toml`, `.gitignore`, `.env.example`.

## 5. Decisions only the owner can make

- **`config/settings.erpnext.yaml`** is the owner's live configuration (the company name, no secrets). Keep it, or replace it with an example.
- **`ui-design/`** is ignored because the origin and licence of those design exports are not recorded. Remove the line from `.gitignore` only when that is settled.
- **Author name and e-mail** are written into every commit. `git log --format='%an <%ae>' | sort -u` shows what would be public; use a no-reply address if the personal one should not be. (`git config user.email` sets it for new commits; commits already made keep theirs.)
- **`REPLACE-WITH-OWNER`** in `.github/ISSUE_TEMPLATE/config.yml` and the missing authors and URLs in `pyproject.toml` need the owner's name, contact and repository URL.
- **Licence:** `LICENSE` is the GPL-3.0 text and `pyproject.toml` says `GPL-3.0-or-later`; keep the two in step.

## 6. History

```powershell
git log --all --stat --format='%h %an %s' | Select-String -Pattern '\.env$|\.db$|data/'      # expect: nothing
git log --all --oneline | Measure-Object -Line                                               # how many commits will be public
```

If a secret is found in any commit that has **not** been pushed, rewrite the unpushed history (for a single baseline commit, delete the repository's `.git` folder and start over after fixing the files; do that only on a repository that has never been pushed). If it was ever pushed, treat the secret as public: rotate it first (ERPNext API key and secret, WhatsApp token and app secret, Telegram bot token, any hosted-model key, `ERP_AGENTS_OWNER_TOKEN`), then clean the history.

## 7. After the first push

Turn on GitHub secret scanning and push protection for the repository, protect `main` with the CI checks (tests, `bandit`, `pip-audit`, the secret scan), and run the first CI on all five Python versions before announcing anything.
