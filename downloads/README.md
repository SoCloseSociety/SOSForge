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
| `/download/sosforge-latest.apk` | hard link to the newest APK | `no-cache` |

A missing file is a plain 404, never the single-page app.

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

Then copy it to the server, APKs first and `latest.json` last, so the pointer
never names a file that has not arrived:

```bash
rsync -avH --exclude latest.json downloads/ helper-vps:/root/SAAS/SuiteForge/SOSForge/downloads/
rsync -av downloads/latest.json helper-vps:/root/SAAS/SuiteForge/SOSForge/downloads/
```

Old versions stay: their URLs are served as immutable, and a page or a search
result may still point at one.
