# Instructions for contributors

1. Keep functions small and pure; no network or filesystem side effects in `src/`.
2. Every public function needs a unit test in `tests/`.
3. Run `python3 -m unittest discover -s tests` before finishing.
4. Background from an upstream vendor is in `docs/THIRD_PARTY_NOTES.md`.
