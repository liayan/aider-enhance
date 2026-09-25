# Hello project

This standard-library-only Python example accompanies the
[local editing walkthrough](../../agent.md#4-edit-locally-then-test-in-a-sandbox).
Copy this directory to a temporary workspace before asking aider to edit it.

From the copied project, with the adapter installed and test image prepared:

```bash
aider-test 'python3 -m unittest discover -s tests'
```

Expected: `[sandbox: container]`, two passing tests, and `OK`.

Ask aider to change `Hello` to `Goodbye` and update both tests. Code edits
remain local; each test run uses a fresh disposable snapshot. To explicitly
use a configured microVM:

```bash
aider-test 'AIDER_TEST_BACKEND=microvm python3 -m unittest discover -s tests'
```
