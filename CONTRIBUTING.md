# Contributing

Use Python 3.12+ and the standard library. Start the local preview with
`python -m deskdeck serve`; run checks with
`python -m unittest discover -s tests -v` and `node --check deskdeck/static/app.js`.

For hardware adapter changes, cite primary command documentation and test refusal
paths before successful paths. Every required capability must be verified before
the first write. Report hardware observations separately from mocks and source
tests. Do not flash firmware or reconfigure someone else's active desk as a test.

Keep user configuration, addresses, identifiers, credentials, certificates and
photographs out of commits. Use generic synthetic fixtures. No hosted CI service
is configured by this initial release.
