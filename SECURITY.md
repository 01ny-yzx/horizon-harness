# Security Policy

[简体中文](SECURITY.zh-CN.md)

## Supported versions

Security fixes are provided for the latest code on `main`. Older snapshots and personal forks are not actively supported.

## Reporting a vulnerability

Please do not disclose exploitable security issues in a public issue, discussion, pull request, or chat log.

Use the repository's **Security → Report a vulnerability** flow to submit a private report. If private vulnerability reporting is unavailable, open a minimal public issue requesting a private contact channel, without including exploit details, credentials, or sensitive logs.

Include when possible:

- affected commit or version;
- impact and attack prerequisites;
- minimal reproduction steps;
- expected and actual safety behavior;
- sanitized logs or proof of concept;
- suggested mitigation, if known.

Relevant reports include authentication bypasses, credential exposure, path or sandbox escapes, access-boundary bypasses, unsafe browser/network access, MCP permission bypasses, and unintended tool side effects.

The maintainer will make a best effort to acknowledge a complete report within seven days. Validation and remediation timing depends on severity and reproducibility.

## Handling secrets

- Never include real API keys, tokens, passwords, private keys, or personal data in a report.
- Replace sensitive values with clear placeholders.
- If a credential may have been exposed, revoke or rotate it before reporting.

Reports about normal `full_access` behavior without a safety-boundary bypass, unsupported third-party services, or social-engineering-only scenarios may be handled as regular issues.
