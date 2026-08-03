# Example third-party verifier

A real, independently pip-installable package demonstrating that a new
verifier can be added to verimcp with **zero changes to verimcp's own
source** — just a package that depends on `verimcp` and declares an entry
point.

```bash
pip install -e .
python -c "from verimcp.verifiers import registry; print([type(v).__name__ for v in registry.load_verifiers()])"
# ReverseStringVerifier is in the list, alongside every built-in verifier
```

See `../../docs/writing-a-verifier.md` for the full guide, and
`../../tests/test_third_party_verifier_plugin.py` for the automated proof
this actually works.
