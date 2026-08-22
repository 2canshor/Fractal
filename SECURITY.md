# Security Policy

## Supported Versions

Fractal is in an initial development stage. No released version currently receives a long-term security-support commitment.

## Reporting a Vulnerability

Use the repository's private security-advisory feature. Do not place credentials, personal records, private workspace content, or exploit details in a public issue.

## Boundary Rules

- Store secret references, never secret values, in canonical state.
- Keep private workspace state and runtime evidence outside the public repository.
- Treat automated secret scanning as a guardrail, not a complete security review.
- Verify generated exports before publishing them.
