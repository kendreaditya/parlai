# Homebrew tap

This directory holds a Homebrew formula for distributing `parlai`.

## First-time release flow

1. Tag a release: `git tag v0.1.0 && git push --tags`
2. Compute the tarball SHA: `curl -L https://github.com/kendreaditya/parlai/archive/refs/tags/v0.1.0.tar.gz | shasum -a 256`
3. Replace `PLACEHOLDER_SHA256_REPLACE_AFTER_FIRST_RELEASE` in `parlai.rb` with the value
4. Generate Python resource blocks (one per pip dep): `brew update-python-resources parlai` after the tap is set up
5. Push the formula change

## Install from this repo (after release)

```bash
brew tap kendreaditya/parlai https://github.com/kendreaditya/parlai
brew install kendreaditya/parlai/parlai
```

For wider discoverability, mirror this `Formula/` into a dedicated `kendreaditya/homebrew-tap` repo so users can `brew install kendreaditya/tap/parlai`.
