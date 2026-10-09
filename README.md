# PaNasMs module registry

This repository publishes the signed module catalog for PaNasMs (Pavlo's NAS Management System),
together with the ARM64 and AMD64 module packages. The PaNasMs core lists this catalog on its
Modules page and installs and updates modules from it. The core source is in the
[main repository](https://github.com/PaNasMs/panasms), and the [project website](https://panasms.github.io/)
has installation and setup guides. This repository does not contain the core source or any
private signing key.

Catalog: https://panasms.github.io/module-registry/

## Available modules

| Module | Version | What it does |
| --- | --- | --- |
| Files | 0.3.13 | File manager with a folder tree, removable devices, uploads, background copy and move, trash and thumbnails. It can also browse connected Google Drive and Dropbox accounts. |
| Terminal | 0.2.11 | Browser terminal that runs as the signed-in Linux user. Available to administrators. |
| Cloud Sync | 0.1.24 | Folder synchronization with several Google Drive and Dropbox accounts. |
| Containers | 0.1.16 | Docker images, container lifecycle, shared networks, persistent folders and Docker storage settings. Available to administrators. |

Every current release requires core `>=0.2.15,<0.3.0` and module API 1, and has ARM64 and AMD64
packages. Each signed manifest lists the Debian packages the module needs, such as `rclone`
for Files and Cloud Sync.

Cloud Sync tasks run in upload, download or two-way mode. Upload and download modes do not
propagate deletions and keep replaced versions at the destination. The first two-way sync needs
one of the two folders to be empty, and rclone's deletion limit stays in force. While idle, the
module waits for inotify events and cloud change cursors instead of rescanning local folders.
Cloud access needs an authorized Google or Dropbox connection on the NAS. OneDrive and Synology
Drive are not supported yet.

Containers checks for Docker Engine and Compose and offers to install missing components. It
groups Compose-managed containers in the list but does not deploy arbitrary Compose projects yet.

## Install a module

Open **Modules** in the PaNasMs panel and click the install or update icon next to a module. You
can also download a release archive from the catalog page and upload it on the Modules page.
Before installing, the core checks the module signature, payload hashes, API and core
compatibility, architecture and dependencies. Do not install packages from a signer you do not
trust.

## Catalog format

- `catalog.json` is deterministic UTF-8 JSON with a trailing newline and `schemaVersion: 1`.
- `catalog.sig` is an Ed25519 envelope with `algorithm`, `signer` and a base64 `signature`. The
  signature covers the exact bytes of `catalog.json`.
- Each `modules[].releases[]` entry has the signed module manifest, a base64 manifest signature,
  an immutable archive URL, its SHA-256, its size and the channel. The catalog keeps one entry per
  module, version and architecture, sorted newest first. All published versions stay available.
- A manifest signature covers compact, sorted-key UTF-8 JSON without a trailing newline, which
  matches the core package format.
- Clients must use a public key they trust through a separate channel. Downloading a key from this
  site does not establish trust. The core package pins the `panasms-local` and `panasms-ci` key
  identifiers.
- Before installation a client must verify the catalog signature, compatibility, archive length
  and SHA-256, the manifest signature and every payload hash. Registry metadata alone must never
  authorize installation or execution. The PaNasMs client verifies signatures, offers only stable
  releases and selects the variant for the NAS architecture. It does not yet enforce signed catalog expiry
  or keep an anti-rollback watermark.

## Repository layout

| Path | Contents |
| --- | --- |
| `catalog.json`, `catalog.sig` | Signed catalog |
| `entries/` | One release entry per module, version and architecture |
| `provenance/` | Source repository, tag, release and asset SHA-256 for each imported package |
| `keys/` | Public keys for `panasms-local` and `panasms-ci` |
| `site/` | Static catalog page published to GitHub Pages |
| `scripts/registry.py` | Import, sign, build and verify the catalog |
| `scripts/publish.py` | Automated import of module releases |
| `tests/` | Unit tests for both scripts |

## Automated releases

Source repositories:

- [Files](https://github.com/PaNasMs/module-files)
- [Terminal](https://github.com/PaNasMs/module-terminal)
- [Cloud Sync](https://github.com/PaNasMs/module-cloud-sync)
- [Containers](https://github.com/PaNasMs/module-containers)
- [Module SDK](https://github.com/PaNasMs/module-sdk)

To release a module, update its manifest and package version and push a `vX.Y.Z` tag. The
module's CI tests and builds the source for ARM64 and AMD64 and publishes an immutable release with
unsigned payloads.

The **Import module releases** workflow (`import.yml`) checks the source repositories every 15
minutes, and you can also start it by hand. GitHub may delay scheduled runs. The workflow:

1. verifies the source release, checks each payload against its asset architecture and checks
   the payload hashes;
2. signs the installable archives with the `panasms-ci` key;
3. uploads all architecture variants before it finalizes the immutable registry release;
4. verifies every public catalog asset;
5. opens and merges a catalog pull request after the required `validate` check passes, then
   starts the Pages deployment.

The signing job never runs code from a module payload. The `CI_PUBLISHING_ENABLED` repository
variable turns publication on or off, and it is on for this registry. The **Validate and publish
registry** workflow (`pages.yml`) runs the tests and an online catalog build for every pull request
and push to `main`, and publishes the site from `main`.

Publication runs one job at a time, and a failed run can safely run again. The importer verifies and reuses archives
that are already published and never overwrites them. Existing entries stay in the catalog. If
someone changes the catalog by hand at the same time, the importer retries. CI pins action commits
and uses lockfiles.

## Signing keys

The CI signing key is a secret that only this repository can use. The `panasms-local` key stays
offline. A new installation trusts a key only after its core package delivers it, so rotating a
key means shipping the new public key in a core update before any catalog is signed with it.
Releases signed with the local key remain valid. Older ARM64-only releases also remain valid and
unchanged.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 scripts/registry.py verify --online
gh workflow run import.yml --repo PaNasMs/module-registry
```

## License

Original registry software, catalog metadata and modules are licensed under PolyForm
Noncommercial 1.0.0. It is a source-available noncommercial license, not an OSI-approved
open-source license. Third-party components keep their own licenses, and each module archive
includes LICENSE and NOTICE files. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Public
documentation is in English.
