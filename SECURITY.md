# Security and privacy policy

## Do not commit local deployment data

Keep these items out of Git and out of issues, pull requests, screenshots, and logs:

- Robot, NAS, camera, or controller addresses
- RTSP URLs, usernames, passwords, tokens, certificates, or private keys
- `settings.json`, `.env*`, calibration values, workspace measurements, tool/TCP details, or payload data
- Camera captures, recordings, robot poses, session logs, and production safety configuration
- Local assistant artifacts and session data

Use `settings.example.json` as a template. Store actual values only in ignored local files.

## Reporting a vulnerability

Please use GitHub's private vulnerability-reporting feature for this repository when available. Do not publish exploitable details, secrets, or real deployment information in a public issue.

## Safety scope

This project is an academic prototype and is not a certified safety system. Security or software fixes do not replace controller safeguards, a physical emergency stop, or a site-specific robot risk assessment.
