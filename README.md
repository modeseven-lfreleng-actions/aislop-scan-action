<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 🧹 AI Slop Scan Action

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/aislop-scan-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
<!-- prettier-ignore-end -->

A composite GitHub Action that runs the
[aislop](https://github.com/scanaislop/aislop) AI-slop / code-quality
scanner against a repository. It installs a lock-file-verified aislop
CLI, scans the changed files or the full working tree, writes a scored
findings summary with inline annotations, and publishes SARIF to code
scanning on request.

The scan step never fails the job on findings: the `exit-code` output
carries the aislop quality-gate result so callers decide enforcement.
This keeps the action advisory by default and lets a caller (or a code
scanning ruleset) promote findings to merge-blocking later.

Deploy it across an estate from one place — an organisation required
workflow — or opt in per repository with a short caller workflow.

The action runs on Linux and macOS runners and needs Node.js 20 or
newer, `jq`, and `python3` on the PATH (all present on GitHub-hosted
runners).

## Usage

### Run the action

Check out the repository (with full history for changes mode), then run
the action as a step:

<!-- markdownlint-disable MD013 MD046 -->

```yaml
jobs:
  aislop:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      # yamllint disable-line rule:line-length
      - uses: actions/checkout@<commit-sha>  # v7
        with:
          fetch-depth: 0
      # yamllint disable-line rule:line-length
      - uses: lfreleng-actions/aislop-scan-action@<commit-sha>  # vX.Y.Z
        with:
          scan-mode: 'changes'
          base: 'origin/${{ github.base_ref }}'
```

<!-- markdownlint-enable MD013 MD046 -->

Grant `security-events: write` and set `upload-sarif` to `'true'` to
publish results to code scanning. Drop both to keep the run advisory.
Pin the action to a commit SHA (the organisation standard) and record
the version in a trailing comment.

### Deploy across an organisation

Copy [`examples/required-workflow.yaml`](examples/required-workflow.yaml)
into your organisation's `.github` repository and reference it from an
organisation ruleset as a required workflow. GitHub then runs it across
every selected repository with no per-repository file. Pull requests
scan the changed files under `contents: read`; a separate job uploads
full-repository SARIF under `security-events: write` on default-branch
pushes.

### Opt in per repository

Copy [`examples/caller-workflow.yaml`](examples/caller-workflow.yaml)
into a repository's `.github/workflows/` directory. The single job scans
pull request changes and uploads full-repository SARIF on default-branch
pushes.

### Enforce the quality gate

The action always succeeds when the tool runs cleanly; findings land in
the summary, the annotations, and the outputs. To make findings block a
pull request, gate on the `exit-code` output in a follow-up step:

<!-- markdownlint-disable MD013 MD046 -->

```yaml
      - name: 'Enforce quality gate'
        if: steps.scan.outputs.exit-code != '0'
        run: |
          echo '::error::aislop quality gate failed'
          exit 1
```

<!-- markdownlint-enable MD013 MD046 -->

## How it works

1. The runner checks the action out at the ref a consumer pins. The
   action reads its pinned aislop version from its bundled
   `package.json` through `${{ github.action_path }}`, so that ref
   fixes the aislop version: the ref is the pin.
2. The action installs the pinned aislop CLI with `npm ci` against its
   bundled `package-lock.json`, so the integrity hashes committed in
   the lock file verify the npm download. The install lands in a
   temporary directory outside the audited workspace.
3. aislop scans the changed files (`scan-mode: changes`, compared
   against `base`) or the full working tree (`scan-mode: full`),
   producing a JSON report and a SARIF file. The gate exit code
   becomes the `exit-code` output rather than failing the job.
4. The action writes a scored findings summary to the job summary and
   emits inline annotations for the top findings.
5. When `upload-sarif` is `'true'`, the action publishes the SARIF to
   code scanning under the `aislop` category. The example workflows set
   this for default-branch pushes and keep pull request runs advisory.

## Inputs

<!-- markdownlint-disable MD013 -->

| Name                | Default     | Description                                                                              |
| ------------------- | ----------- | ---------------------------------------------------------------------------------------- |
| `scan-mode`         | `changes`   | Scan scope: `changes` audits files changed relative to `base`; `full` audits everything. |
| `base`              | `""`        | Git ref changes compare against (for example `origin/main`). Empty compares to HEAD.     |
| `working-directory` | `.`         | Path within the workspace to scan.                                                       |
| `aislop-version`    | `""`        | Override the bundled pin (for example `0.13.1`). Bypasses the bundled lock file.         |
| `extra-args`        | `""`        | Extra raw arguments appended to the aislop call.                                         |
| `annotate`          | `'true'`    | Emit inline annotations for the top findings: `'true'` or `'false'`.                     |
| `upload-sarif`      | `'false'`   | Publish SARIF to code scanning from the action: `'true'` or `'false'`.                   |

<!-- markdownlint-enable MD013 -->

## Outputs

<!-- markdownlint-disable MD013 -->

| Name          | Description                                                                |
| ------------- | -------------------------------------------------------------------------- |
| `sarif-file`  | Absolute path to the generated SARIF file.                                 |
| `report-file` | Absolute path to the generated JSON report.                                |
| `score`       | aislop score (0–100); empty when the scope holds no supported files.       |
| `exit-code`   | aislop quality-gate exit code: `0` passed, non-zero when the gate failed.  |

<!-- markdownlint-enable MD013 -->

## Permissions

Grant these to the calling job:

<!-- markdownlint-disable MD013 -->

| Permission               | Why                                                                      |
| ------------------------ | ------------------------------------------------------------------------ |
| `contents: read`         | Check out the repository under audit.                                    |
| `security-events: write` | Publish SARIF to code scanning when `upload-sarif` is `true`.            |
| `actions: read`          | Lets `upload-sarif` read run info on private repos (harmless on public). |

<!-- markdownlint-enable MD013 -->

The action uses the automatically provided `GITHUB_TOKEN` and needs no
extra secrets.

## Network access

Runners with a restrictive egress policy need to allow:

- `registry.npmjs.org:443` — the aislop package install.
- `github.com:443`, `objects.githubusercontent.com:443`,
  `release-assets.githubusercontent.com:443` — the package postinstall
  step downloads the bundled engine binaries (for example ruff and
  golangci-lint) from GitHub releases.

The action sets `AISLOP_NO_TELEMETRY=1` and `AISLOP_NO_HISTORY=1`, so
the CLI makes no telemetry calls at scan time.

## Version management

`package.json` pins the aislop version and `package-lock.json` locks it
with integrity hashes. Dependabot's `npm` ecosystem watches these files
and opens a pull request when a new aislop release ships.

The update flow:

1. aislop publishes a new release.
2. Dependabot opens a pull request bumping `package.json` and
   `package-lock.json` (weekly, with a cooldown so fresh releases
   settle first).
3. A maintainer reviews and merges the bump.
4. A maintainer pushes a signed semver tag; the release-drafter and
   tag-push workflows draft and promote the GitHub release.
5. Consuming repositories bump their pinned ref (through their own
   Dependabot `github-actions` updates) to adopt the new aislop
   version.

## Security model

- **Lock file integrity.** Every run installs aislop with `npm ci`
  against the committed `package-lock.json`, so npm verifies the
  package tarball against its recorded integrity hash before anything
  executes. (aislop does not publish Sigstore build provenance; the
  lock file hash is the verification boundary.)
- **Pinned and reviewable.** The ref a consumer pins fixes the
  installed aislop version. Upgrades arrive as reviewable pull requests
  rather than tracking the latest release automatically.
- **SHA pinning.** Consumers pin this action, and the action pins
  everything it uses, to commit SHAs.
- **Least privilege.** The example workflows scan under
  `contents: read` and upload the SARIF from a job that holds
  `security-events: write`.
- **Workspace isolation.** The npm install and the scan outputs land
  in temporary directories outside the audited workspace, so the scan
  never reports on the action's own dependencies and never leaves
  untracked files in the caller's working tree. The output paths
  surface through the `sarif-file` and `report-file` outputs.

## License

Apache-2.0. See [LICENSE](LICENSE).
