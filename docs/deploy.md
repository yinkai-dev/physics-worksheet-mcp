# Deployment

Use Ubuntu 24.04, 64-bit Python 3.12, XeLaTeX with ctexart, Fandol fonts, SQLite FTS5 and Caddy. Keep existing host services intact. Verify host identity through a trusted channel and strict SSH host checking before any login.

Install TeX using `texlive-xetex texlive-lang-chinese texlive-latex-extra` and Python venv support. Install Caddy from its official apt repository. Build a wheel and export locked hashed runtime requirements. Verify the release archive SHA before extracting; install dependencies with `pip --require-hashes`, then the verified local wheel with `--no-deps` into a separate versioned venv.

Paths: code `/opt/pwmcp/releases/<version>`, venv `/opt/pwmcp/envs/<version>`, current symlink `/opt/pwmcp/current` (with `env` symlink), config `/etc/pwmcp/config.toml`, immutable banks `/srv/pwmcp/banks`, writable state/jobs `/var/lib/pwmcp`, backups `/var/backups/pwmcp`. Only the admin CLI can install, verify, activate or roll back releases. Grant the runtime account read access to banks and write access only to state/jobs.

Install the supplied systemd units, use one worker, keep MemoryMax aligned with host capacity, then enable the API, worker, backup and certificate timers. The worker has a private network namespace and runs each job in a supervised process group with CPU/address-space/wall limits. Shell escape is disabled. Keep access logs disabled or redact download credentials.

Set `PWMCP_SITE` and `PWMCP_ACME_CA` in a Caddy service environment drop-in. For an IP certificate use the supplied `profile shortlived` configuration, first with Let's Encrypt staging. After successful staging issuance, switch to the production directory. A trusted chain, correct IP subject alternative name and short certificate lifetime must be verified from outside the host. Short certificate renewal is automatic in Caddy, with the certificate check timer as a failure signal. Issuance does not prove future renewal.

`/healthz` checks process liveness; `/readyz` checks active releases, SQLite write access, worker heartbeat and XeLaTeX availability. TLS expiry is checked separately by the certificate timer. Backups use the SQLite backup API and integrity_check. Test a restore, worker interruption recovery and file SHA persistence before accepting a deployment. Keep previous code, venvs, bank releases and pointer history for rollback. Stop only the two pwmcp services to perform state recovery; preserve the pre-restore database.

The optional semantic embedder is an interface only in this version. Search builds a bound FTS5 trigram index on demand and reports semantic unavailable; no paid API calls occur. Human semantic reference labels, client UI tests and a multi-day certificate renewal observation remain separate acceptance work.
