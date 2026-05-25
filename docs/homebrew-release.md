# Homebrew Release

Homebrew maps `brew tap mggwxyz/bonsai` to the GitHub repository
`https://github.com/mggwxyz/homebrew-bonsai`. The tap repository is separate
from the Bonsai source repository.

## One-time tap setup

```bash
brew tap-new mggwxyz/bonsai
gh repo create mggwxyz/homebrew-bonsai \
  --public \
  --source="$(brew --repo mggwxyz/bonsai)" \
  --remote=origin \
  --push
```

## Release checklist

1. Merge the Bonsai source release to the branch that should be tagged.

2. Create and push a source tag from the Bonsai source repository.

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. Copy the formula into the installed tap repository.

   ```bash
   tap_dir="$(brew --repo mggwxyz/bonsai)"
   mkdir -p "$tap_dir/Formula"
   cp Formula/bonsai.rb "$tap_dir/Formula/bonsai.rb"
   ```

4. Generate Python dependency resources from the tap formula.

   ```bash
   brew update-python-resources --ignore-non-pypi-packages mggwxyz/bonsai/bonsai
   ```

5. Check the formula and publish the tap update.

   ```bash
   cd "$(brew --repo mggwxyz/bonsai)"
   brew style Formula/bonsai.rb
   ruby -c Formula/bonsai.rb
   git add Formula/bonsai.rb
   git commit -m "Add bonsai formula"
   git push origin main
   ```

6. Verify the install path from a clean shell.

   ```bash
   brew untap mggwxyz/bonsai || true
   brew tap mggwxyz/bonsai
   brew install bonsai
   bonsai --version
   ```
