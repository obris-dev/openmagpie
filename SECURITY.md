# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately via GitHub's [**Report a vulnerability**](https://github.com/obris-dev/openmagpie/security/advisories/new)
button (the repo's Security tab, then Advisories). If you can't use that, email
**security@openmagpie.ai** instead.

We aim to acknowledge within 3 business days and will keep you updated as we
investigate. Once a fix ships we're glad to credit you, unless you'd rather stay
anonymous.

## Scope

OpenMagpie is self-hosted and BYO-LLM: relevance judgements run against a model
you operate. The areas we care most about:

- The connector fetch path (untrusted source URLs) and its SSRF containment.
- Authentication and account-scoping in `apps/core` (`auth_api`, `accounts`).
- The webhook delivery path and any handling of secrets.

## Supported versions

Pre-1.0: fixes land on `main`. Pin a commit if you need stability.
