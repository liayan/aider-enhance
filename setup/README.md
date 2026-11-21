# Environment setup

Run these on the Linux demo host, from the repository root, in this order.
Use a disposable host or VM: no SSH keys, cloud credentials or real repos.

| Step | Command | Needed for |
|---|---|---|
| 1. Check the host | `setup/preflight.sh` | Shows which backends this host can run |
| 2. Fetch aider | `git submodule update --init` | `--agent` runs |
| 3. Container image | `setup/build-container-image.sh` (`WITH_AIDER=1` for `--agent`) | `rootless-container`, aider-local container tests |
| 4. Firecracker guest | put a guest kernel at `src/firecracker/assets/vmlinux`, then `sudo setup/build-firecracker-guest.sh` (`sudo env WITH_AIDER=1 ...` for `--agent`) | `firecracker`, aider-local microVM tests |
| 5. aider-local | `setup/install-aider-local.sh` | The `aider-local` and `aider-test` commands |
| 6. Baseline only | `sudo mkdir -p /demo/outside && sudo chown -R $USER /demo` | Lets the unsandboxed baseline place its canary and outside-write target |

Rebuild the Firecracker rootfs after any change to `src/firecracker/guest-init.sh`;
the launcher refuses a stale one. Details: [src/firecracker/README.md](../src/firecracker/README.md).

Then run `setup/preflight.sh` again; it should show YES for every backend you
plan to run.
