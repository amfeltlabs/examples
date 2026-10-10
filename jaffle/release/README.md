# Releasing the Jaffle corpus

A tag `jaffle/vX.Y.Z` publishes a GitHub release of this repository with two assets:

- `jaffle-vX.Y.Z.tar.gz`, whose root is `jaffle/` holding `data/`, `db/`, `expected/`, `SHA256SUMS`,
  `NOTICE` and `LICENSE.jaffle-shop-generator`. The derivation scripts, `PROVENANCE` and
  `VERSION` stay in the repository; the asset is what consumers load.
- `jaffle-vX.Y.Z.tar.gz.sha256`, one line in the standard `<hex>  <name>` format.

A tag ending in `-rc.N` makes a prerelease. No release is marked "latest": the repository will hold
other corpora, each tagged on its own, so consumers pin an explicit tag and its SHA-256.

## Versioning

Each corpus is versioned on its own, Go-style (spec D7). A data or model change is a **major**
version. A comment or documentation change is a **minor** version. `jaffle/VERSION` holds the
version without `v` or `-rc.N`.

## Procedure

1. Bump `jaffle/VERSION` in a pull request and merge it. The release candidates of a version share
   its `VERSION`, so a new `-rc.N` of the same version needs no bump.
2. Tag the merged commit on `main` and push the tag:

   ```bash
   git tag jaffle/v1.0.0-rc.1 <sha on main>
   git push origin jaffle/v1.0.0-rc.1
   ```

3. The `release-jaffle` workflow refuses the tag unless its commit is on `main` and its version
   matches `VERSION`. It then runs the CI jobs on the tag (lint, commitlint, reproduce, addresses,
   load and asset), builds the asset and creates the release. A failing check publishes nothing.
4. Verify the release:

   ```bash
   gh release download jaffle/v1.0.0-rc.1 --repo infusal/examples --pattern 'jaffle-v*'
   sha256sum -c jaffle-v1.0.0-rc.1.tar.gz.sha256
   tar -xzf jaffle-v1.0.0-rc.1.tar.gz
   (cd jaffle/data && sha256sum -c ../SHA256SUMS)
   ```

   The SHA-256 in the release notes must equal the one in the `.sha256` file. Then pin the tag and
   the SHA-256 in the consumers.

To re-run a tag whose release was not created (for example after a runner failure), start the
workflow by hand with the existing tag as input. It fails if the release already exists.

## Building the asset yourself

```bash
sh jaffle/release/build-asset.sh jaffle/v1.0.0-rc.1 out/
sh jaffle/release/test-reproducible.sh
```

The build needs GNU tar. On macOS run `brew install gnu-tar`; the scripts prefer `gtar`. The
tarball's mtime is the commit date of `HEAD`, or `SOURCE_DATE_EPOCH` when set, so the same tag
commit rebuilds to the same bytes under any timezone, umask or directory.
