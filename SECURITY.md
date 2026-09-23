# Security reporting

For a suspected vulnerability, please avoid posting private media, personal project data, credentials or reproduction files containing secrets in a public issue. If GitHub offers private vulnerability reporting for this repository, use that route. Otherwise, open a minimal issue asking for a private reporting channel without disclosing exploit details or personal data.

The intended deployment is a local IPv4 loopback server. Hosting it on a network interface, reverse proxy or tunnel is outside the supported configuration. Review mode does not write editor projects. Opt in editing accepts requests only from the local Madison page and saves edits as a new Tesseract project version. For source video preview, the review bundle and selected project folder are allowed by default; each `--media-root` adds another explicitly chosen local folder. Treat any served review or source media as accessible to other local processes while the server runs.

Report the operating system, Python version, command used, expected behavior and actual behavior. Prefer a synthetic minimal review bundle. Third party editor or codec vulnerabilities should also be reported to their maintainers.
