# downloads/

The Android APK served at <https://sosforge.soclose.co/download/>. Nothing in
here but this README and its `.gitignore` is ever committed: APK binaries do
not enter git, and they do not enter the docker image either.

The web container mounts this directory read-only (`docker-compose.yml`:
`./downloads:/srv/downloads:ro`) and `deploy/nginx.conf` serves exactly four
kinds of file from it, nothing else:

| URL | File | Cache |
|---|---|---|
| `/download/latest.json` | `latest.json` | `no-cache` |
| `/download/sosforge-v<version>.apk` | the APK | one year, `immutable` |
| `/download/sosforge-v<version>.apk.sha256` | `sha256sum -c` format | one year, `immutable` |
| `/download/sosforge-latest.apk` | the newest APK (a hard link here, a copy on the server) | `no-cache` |

A missing file is a plain 404, never the single-page app, and carries no
cache header: a 404 is never held.

## Filling it

Never by hand. On a machine with the Android SDK (the Mac):

```bash
make publish-apk APK=/path/to/sosforge-v0.1.2.apk NOTES_EN="..." NOTES_FR="..."
```

`tools/publish_apk.py` reads every field of `latest.json` back from the APK
(aapt2, apksigner, the bytes themselves), refuses a signer other than the
release key, refuses to overwrite a published version with other bytes, and
writes `latest.json` last. `latest.json` is the contract the Android app reads
for its update check (`tasks/todo.md`, "Round 2").

Before writing anything, the tool also reads what the SERVER serves (its
`latest.json` and the version's `.sha256`): this directory is gitignored, so
another clone starts empty and would see nothing to refuse. It prints the
copy steps when it is done; they are these two, in this order:

```bash
rsync -av --ignore-existing --include 'sosforge-v*' --exclude '*' downloads/ helper-vps:/root/SAAS/SuiteForge/SOSForge/downloads/
rsync -av downloads/latest.json downloads/sosforge-latest.apk helper-vps:/root/SAAS/SuiteForge/SOSForge/downloads/
```

1. The versioned files only, and never over a file the server already has
   (`--ignore-existing`): a served version is immutable, browsers keep it a
   year.
2. Then the pointers, `latest.json` and the no-JavaScript alias, which do
   change at every release: the pointer never names a file that has not
   arrived.

This README and `.gitignore` are never copied: they are tracked in the
server's git tree, and an rsynced copy makes its next `git pull` refuse.

Old versions stay: their URLs are served as immutable, and a page or a search
result may still point at one.
