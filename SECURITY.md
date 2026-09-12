# Security Policy

## Supported Versions

This is experimental software. No versions are guaranteed secure.

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it via a GitHub issue on this repository.

Please do not include working exploits, PoC code, or sensitive payloads in the issue body — describe the impact and point to the affected code path. We can coordinate disclosure before any public follow-up.

## Safe Usage Guidelines

- Run in isolated environments (Docker / VM)
- Do not expose to the public internet
- Do not provide production credentials
- Restrict filesystem and network access
- Assume all generated code may be unsafe

## Scope

This project is not designed for:
- production deployment
- handling sensitive data
- autonomous external system control without supervision
