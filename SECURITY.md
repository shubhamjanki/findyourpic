# Security policy

## Supported version

Security fixes are accepted for the latest source on the default branch. There are currently no published release versions with a separate support schedule.

## Reporting a vulnerability

Please do not report vulnerabilities in a public issue. Use GitHub's **Report a vulnerability** feature in the repository's Security tab when it is enabled. If private vulnerability reporting is unavailable, contact the repository owner privately before disclosing details publicly.

Include the affected version or commit, relevant configuration, impact, and reproduction steps. Do not attach real photos, face embeddings, database files, credentials, or other sensitive personal data; use synthetic examples.

## Deployment and data cautions

- The current browser server is intended for local use. It has no user authentication or per-user authorization and must not be exposed directly to the public internet.
- Indexed images, face embeddings, and labels are sensitive personal data. Restrict filesystem access, protect backups, and define a deletion/retention policy.
- Do not commit image collections, generated match sets, model weights, SQLite indexes, secrets, or user data.
- Use only photos you are authorized to organize. Face matching and automatic grouping are fallible; manually review results and do not use this application for surveillance or consequential identity decisions.

See [architecture.md](architecture.md) before planning a hosted deployment.
