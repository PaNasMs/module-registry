# PaNasMs module registry

Modules for **Pavlo's NAS Management System**.

Official catalog: **https://panasms.github.io/module-registry/**

This repository publishes a signed catalog and versioned ARM64 module packages. It does not contain the PaNasMs core source or private signing keys. PaNasMs core 0.2.1 and later lists this catalog in the Modules panel and supports installation and updates with one click. Installation from a downloaded `.panasms` archive is also available.

## Available modules

- **Files** — file manager, folder trees, removable devices and file operations.
- **Terminal** — interactive system-user terminal with administrator permission checks.

Files and Terminal require the PaNasMs namespace, core `>=0.2.0,<0.3.0`, module API 1 and ARM64. OS package requirements are included in each signed manifest. The previous project namespace is not compatible with these module globals, service paths or signing-key identifiers.

**Cloud Sync (prototype)** — multiple Google Drive and Dropbox connections, local authorization helper, per-user SQLite state, pause/retry and task history. Requires core `>=0.2.1,<0.3.0`, ARM64, Python 3 and rclone (installed from OS packages). Upload/download modes preserve deletions and keep replaced destination versions; initial two-way sync requires one empty folder and retains rclone safety limits. Inotify and cloud change cursors avoid recurring local scans while idle. Actual provider access requires user authorization. OneDrive and Synology Drive are not included yet.

## Install

Open **Modules** in PaNasMs core 0.2.1 or later and click the install or update icon. Alternatively, download the appropriate release archive from the catalog and upload it in **Modules**. The core checks module signatures, payload hashes, API/core compatibility, architecture and dependencies. Never install packages from an untrusted signer.

## Catalog protocol

- `catalog.json`: deterministic UTF-8 JSON with a trailing newline; `schemaVersion: 1`.
- `catalog.sig`: Ed25519 envelope with `algorithm`, `signer` and base64 `signature`. The signature covers the exact bytes of `catalog.json`.
- `modules[].releases[]`: signed module manifest, base64 manifest signature, immutable archive URL, SHA256, size and channel. All published versions remain available; releases are sorted newest first.
- Manifest signatures cover compact, sorted-key UTF-8 JSON **without** a trailing newline, matching the core package format.
- Clients must use a separately trusted, pinned public key; downloading a key from this site does not establish trust. PaNasMs pins the `panasms-local` and `panasms-ci` key identifiers in the core package.
- A client must verify catalog signature, compatibility, archive length and SHA256, manifest signature, and every payload hash before installation. Registry metadata alone must never authorize installation or execution. Freshness and rollback policy must be handled by the future client.

## Source repositories and automated releases

- [Files](https://github.com/PaNasMs/module-files)
- [Terminal](https://github.com/PaNasMs/module-terminal)
- [Cloud Sync](https://github.com/PaNasMs/module-cloud-sync)
- [Shared SDK](https://github.com/PaNasMs/module-sdk)

Update a module's manifest/package version and push `vX.Y.Z`. Its ARM64 CI tests
and builds the source and publishes an immutable release with an unsigned payload.
The **Import module releases** workflow checks these repositories every 15 minutes
(GitHub scheduling can be delayed) or on manual dispatch. It verifies the source
release and payload hashes, signs installable archives with `panasms-ci`, publishes
immutable registry releases, verifies all public catalog assets, then creates and
merges a catalog PR through the required `validate` status and dispatches Pages.
No source payload code is executed by the signing job.
The `CI_PUBLISHING_ENABLED` repository variable gates publication until client key
rollout is complete; set it to `true` only after trusted-key deployment.

The new signing key is a secret available only in this repository. The existing
local signing key remains offline. Clients must trust the new public key through
a core update before automated catalogs are published. Downloading a catalog does
not establish key trust. Existing local-key releases remain valid.

Publication is serialized and retryable. Published archives are verified and reused,
never overwritten. Existing entries are retained. Concurrent manual changes to the
catalog cause a safe retry. CI uses pinned action commits and lockfiles.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/registry.py verify --online
gh workflow run import.yml --repo PaNasMs/module-registry
```

## License

Original registry software, catalog metadata and modules use **PolyForm Noncommercial 1.0.0**. This is a source-available noncommercial license, not an OSI-approved open-source license. Third-party components retain their licenses; module archives include LICENSE and NOTICE. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
