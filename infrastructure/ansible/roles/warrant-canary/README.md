# warrant-canary

Runs the Warrant canary on Arc mainnet on the VM `mnemonic-fabric`, one action per
run. The `warrant-canary` workflow (`.github/workflows/warrant-canary.yml`) drives it
through `playbooks/warrant-canary.yml`. The play runs as `op` with `become: false`.
Task-level `become` is used only for:

- the tmpfs mount and unmount,
- the secrets directory,
- the Caddy steps,
- the seller container start, the secret-file check and the MCP handshake, which read the root-owned `seller.env`,
- the root-only setup: apt, and creating `/opt/warrant-canary`.

This role is the only code that puts mainnet key material on the VM.

## Actions

`warrant_canary_action` selects one action.

| Action | What it does |
|---|---|
| `preflight` | Read-only checks: `sudo -n true`, `df`, `free`, `docker ps`, `caddy validate` of the live config, and `wget` of `127.0.0.1:2020/metrics` inside `vaultwarden-caddy-1`. Writes `health-before.json`. |
| `configure` | Installs the pinned toolchain and clones or updates `policy-execution` at `warrant_canary_src_commit` (never forced, never deleted). Builds `warrant-host` (release, guest in Docker) and `warrant-solver` (debug). **Asserts the image ID before writing anything else.** Then writes `/opt/warrant-canary/env` and the seller secrets. |
| `rehearse` | Refuses when the Anvil port is busy, starts loopback Anvil, then stages all four keys. Runs the explicit D7 chain with `ShowcaseToken`, then the D19 refund scenario. Writes `rehearsal.md` and `rehearsal.json`. Stops Anvil and unstages in `always`. |
| `stage-keys` | Stops on low disk or memory (stop rule), then stages keystores and passwords for `warrant_canary_roles` into the tmpfs. |
| `unstage-keys` | Unmounts only when the active mainnet order is Paid or Refunded, or with `force_unstage` and a reason. |
| `deploy-solver` | Runs the checks in this order: green rehearsal, `settle_window`, existing escrow, then `check-arc-solver.py before`. Only then does it stage keys and run `init`, `check` and `deploy`, plus `after` when broadcasting. |
| `deploy-task` | Checks for a green rehearsal and refuses when `task-escrow.json` exists. Runs `forge script DeployMock` and, after a broadcast, writes the `checks` object. |
| `deploy-seller` | Writes the registry key, renders the manifest and runs `init.py`. Strips the buyer acceptance key and builds the seller image. Starts the hardened container, validates and installs the Caddy snippet, reloads Caddy and runs the HTTPS handshake check. |

The workflow owns step 8: artifacts and the deployment PR. The role fetches its records
for that step to `warrant_canary_output_dir`:

- **preflight:** `health-before.json`.
- **configure, stage-keys, unstage-keys:** the health files, plus `force-unstage.txt` when present.
- **rehearse:** `rehearsal.md`, `rehearsal.json`, `prove.time`, `wrap-stats.log`, both probe reports, `anvil.log`, `run.json` (`{"run_dir": "local/<ts>"}`) and the health files.
- **deploy-solver:** `state.json`, `deployment.json`, `arc-before.json`, `arc-after.json`, `run.json` (`{"run_dir": "mainnet/<ts>"}`, written right after `init`, so it is fetched on a later failure too; a simulate run names its `-simulate` directory) and the health files.
- **deploy-task:** `task-escrow.json` and the health files.
- **deploy-seller:** `mainnet-public_keys.yaml`, `seller-public-manifest.json` and the health files.

The role never fetches from `secrets/`, `.keys/` or the tmpfs.

## Variables (D1 contract)

The workflow passes exactly these keys as extra-vars. In `defaults/main.yml` they sit
between `# --- BEGIN workflow inputs (D1) ---` and `# --- END workflow inputs (D1) ---`.
The workflow's unit test compares its keys with that block.

| Variable | Type | Default |
|---|---|---|
| `warrant_canary_action` | string, one of the eight actions | `""` |
| `warrant_canary_deploy_contracts` | bool | `false` |
| `warrant_canary_execute` | bool | `false` |
| `warrant_canary_render_only` | bool | `false` |
| `warrant_canary_roles` | list from `buyer`, `seller`, `relayer`, `deployer`; a string is refused | `[buyer, seller, relayer]` |
| `warrant_canary_run_dir` | string, empty or `mainnet/<YYYYMMDDTHHMMSSZ>` | `""` |
| `warrant_canary_settle_window` | string of digits | `""` |
| `warrant_canary_force_unstage` | bool | `false` |
| `warrant_canary_force_unstage_reason` | string | `""` |
| `warrant_canary_expected_image_id_override` | string; negative dispatches only, refused with execute | `""` |
| `warrant_canary_rpc_override` | string; negative dispatches only, refused with execute | `""` |
| `warrant_canary_output_dir` | controller path; empty skips the fetch | `""` |

Booleans are always coerced with `| bool`. A broadcast needs both
`warrant_canary_deploy_contracts` and `warrant_canary_execute`.

Some values are not workflow inputs:

- **From sops:** `warrant_canary_src_commit` and `warrant_canary_expected_image_id`.
- **From `playbooks/vars/warrant-canary.yml`:** `warrant_canary_max_cost`.
- **Internal lists set by each action:** `warrant_canary_keystore_roles` and `warrant_canary_password_roles`.

Every script call gets `WARRANT_CANARY_RPC`.

## Test flags

- `warrant_canary_render_only`: `configure` and `deploy-seller` render into a temp dir with the production modes and start nothing. `deploy-seller` validates the snippet in a throwaway `caddy:2.9.1`. The temp dir holds secret values, so the role removes it.
- `warrant_canary_skip_toolchain`: skips the Rust, RISC Zero, Foundry and Node installs, `npm ci`, the venv and the cargo builds.
- `warrant_canary_test_*`: molecule-only overrides for the stop-rule measurements, the health targets, the Caddy container name, a broken snippet and keeping the render dir. The playbook refuses them, and `warrant_canary_skip_toolchain`, on the VM.

## Stage and unstage contract

- **Stop rule.** Staging first checks free disk on `/` and `MemAvailable`. Under 10 GB of disk or 6 GB of memory, the action fails before anything is staged.
- **Record.** It then appends one line to `/opt/warrant-canary/runs/stage.log`: time, action and role names, no secrets.
- **tmpfs.** `/run/warrant-canary` is mounted as an ephemeral tmpfs with no fstab entry. Options: `noswap`, `0700`, owned by `op`. `noswap` is asserted with `findmnt`.
- **Bind mount.** `keystores/` is bind-mounted on `/home/op/.foundry/keystores`.
- **Keystores** are written as `warrant-canary-<role>` (0600). Each keystore's `address` must equal `warrant_canary_address_<role>`.
- **Password files** go to `/run/warrant-canary/pw/<role>` (0600), for the password roles only. `deploy-solver` and `deploy-task` stage the deployer password only.
- **Failure.** If staging fails, the tmpfs is removed again.
- **Existing staging.** Every staging action refuses while `/run/warrant-canary` already exists, so an operator's live staging is never unstaged by mistake.
- **`always` cleanup.** `rehearse`, `deploy-solver` and `deploy-task` unstage in `always`, with no state gate. Only `stage-keys` leaves the tmpfs in place.
- **State gate.** The standalone `unstage-keys` runs `files/state-gate.py <run dir> <rpc>` against the newest non-simulate `mainnet/<ts>` run that has an escrow and `quote.task`. The gate passes only for Paid (3) or Refunded (4).
- **Forced unstage.** `force_unstage=true` overrides the gate only with a non-empty reason. The reason goes to `force-unstage.txt`.
- **Proof of cleanup.** Every cleanup ends with `test ! -e /run/warrant-canary`.

## Refusal rules

- **Overrides.** `expected_image_id_override` is accepted only by `configure` and must be 64 hex digits. `rpc_override` is accepted only by `deploy-solver` and must be an https URL. Both are refused with execute. The other actions would persist them in the env file, `seller.env` or the manifest.
- **Other inputs.** `force_unstage_reason` must be 1 to 200 plain characters with no newline or braces. `output_dir` must be absolute and must not contain `..`.
- **sops values.** Addresses and the expected image ID must stay `0x` strings. A value that YAML 1.1 turned into an integer is refused, so load sops as JSON.
- **Pinned checkout.** `warrant_canary_src_commit` must be a full 40-hex sha. `rehearse`, the green-rehearsal gate and `deploy-seller` refuse unless the checkout HEAD equals the pin and `git status --porcelain` is empty. `rehearse.py` records the real HEAD.
- An unknown action, `roles` given as a string, or a `run_dir` outside `^mainnet/[0-9]{8}T[0-9]{6}Z$` is refused before anything else.
- `rehearse` and `deploy-solver` refuse an empty or non-positive `warrant_canary_max_cost`.
- The green-rehearsal gate reads the newest `canary/local/*/rehearsal.json`. `commit`, `image_id` and `instance_hash` must match, and `real_proof` and `refund_ok` must be true.
- `settle_window` must match `\A[0-9]+\Z` and satisfy `max(7200, 1800 + 4 * prove_seconds) <= x <= 86400`. `prove_seconds` is rounded up. `--accept-window 1800` is hard-wired.
- `deploy-solver` refuses when a deployed run exists, meaning a `state.json` with `escrow` and no `abandoned` marker. The exception is `run_dir` naming exactly that run. After a broadcast retry, the old run gets an `abandoned` marker.
- `deploy-task` refuses when `/opt/warrant-canary/deploy/task-escrow.json` exists.
- A simulate `deploy-solver` deletes its `-simulate` directory at the end.

## Health rule

- **Unreachable before the action.** `health-before.json` fails the action when a service that is not recorded as stopped answers nothing (status 000), so a wrong URL cannot hide a change. `unstage-keys` is the exception: it never refuses on reachability, unmounts first, and the health rule then reports `HEALTH RULE FAILED` for the dead service. Task 9 confirms the five default targets with a manual `curl` from the VM before the first `rehearse`.
- **Records.** Each action writes `health-before.json` and `health-after.json` into `/opt/warrant-canary/runs/<UTC ts>-<action>/`. Schema: `{"services": {"<name>": {"status": <int>}}, "stopped": [...]}`. The services are `mcp`, `kaneo`, `vaultwarden`, `caddy` and `ollama`. An unreachable service has status `0`.
- **Operator stops.** The operator records stops in `/opt/warrant-canary/stopped-services.json` as `{"stopped": [...], "stopped_at": ..., "restarted_at": null}`. While `restarted_at` is null, the listed services are excluded and reported as stopped, and they appear in both files' `stopped` field. Once `restarted_at` is set, they are compared again.
- **Failure.** The action fails if any other service's status code differs between the two records.

## Operator notes for the seller service

- **RPC down at start.** The seller refuses to start when the RPC does not answer `eth_chainId`, and exits 1. Under `restart: unless-stopped` it loops. Check the RPC first.
- **Blocked deal slot.** Three things block new deals:
  - a session left in `provisioning` after a crash or restart;
  - an unreadable or unknown file in `/opt/warrant-canary/state/sessions/`;
  - a `failed` session with `"deployment_may_exist": true` before its `expires_at`.

  The refusal names the task or file. To clear it:
  1. Confirm in the io.net console that no deployment for that task still runs or bills, and destroy any that does.
  2. In `sessions/<task_id>.json`, either set `"status": "failed"` and remove `deployment_may_exist`, or set `expires_at` to a past Unix time. Edit as UID 10001 and keep the JSON valid.
  3. Move an unreadable file out of `sessions/`.

  No restart is needed.
- **`task_id` on chain 5042.** It is `0x` plus 64 hex digits, the TaskEscrow id that `propose_deployment` returns.

## Molecule

Pinned versions, installed on 2026-10-15:

```
pipx install molecule==26.10.0
pipx inject molecule 'molecule-plugins[docker]==26.10.12' docker==7.2.0 'jsonschema>=4.23'
```

That gives molecule 26.10.0, molecule-plugins 26.10.12, docker 7.2.0, jsonschema 4.26.0 and
ansible-core 2.21.5. jsonschema 4.17 breaks molecule's schema check on Python 3.14. Molecule
calls the first `ansible-playbook` on `PATH`.

Run it from this directory with Docker running locally. The scenario pulls `ubuntu:24.04`
and `caddy:2.9.1`.

```
molecule test
```

The scenario uses a privileged Ubuntu 24.04 container with its own Docker daemon, and
`prepare.yml` sets it up:

- the `op` user with passwordless sudo, so `become` behaves as on the VM;
- fake `cast`, `forge`, `anvil`, `warrant-host` and `warrant-solver` that log to `/tmp/wc-calls.log`;
- a fixture `policy-execution` repository with stub scripts that accept exactly the D18 flags;
- a health stub;
- throwaway Caddy containers running the new vaultwarden `Caddyfile.j2` and the old pre-Decision-20 shape.

`converge.yml` runs `configure` with `skip_toolchain`, and the idempotence step requires
0 changed. `verify.yml` runs every TDD Anchor case from task 7 through `include_role` with
`block`/`rescue`, plus the Caddy reload smoke. The cases are grouped in `molecule/default/cases/`.
The shared runner `tasks/run.yml` asserts the expected outcome and message, and the other
helpers sit in `molecule/default/tasks/`.

## Measurements

- **`build_seconds`.** It comes from the configure build stamp (`stamps/build-<commit>` holds the cargo build wall time). Without a stamp it falls back to the warm `warrant-host solver-image-id` call, and `rehearsal.md` names both figures.
- **Wrap memory.** `wrap-stats.log` holds `docker stats` samples every 2 s, so the peak is a sampled lower bound.

## Installers

- **Node.** It comes from the NodeSource apt repository, signed by the key whose fingerprint is `warrant_canary_nodesource_key_fpr`. Configure refuses any other key and a key file that holds more than one key, and Task 9 confirms the fingerprint on the first run.
- **`npm ci`.** It runs with `--ignore-scripts`.
- **rustup, rzup and foundryup.** They use their upstream `curl | sh` installers as `op`, then install the pinned versions. This is an accepted trade-off, the same as `prover-vm.sh`.
- **PATH order.** The venv and the build outputs come before the system directories, so the scripts get the venv `python3`. Everything on that path is owned by `op`, who is already root-equivalent through the docker group.
