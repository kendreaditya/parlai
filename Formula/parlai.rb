# Homebrew formula for parlai.
#
# Install via tap (after this file is committed and tagged):
#   brew tap kendreaditya/parlai https://github.com/kendreaditya/parlai
#   brew install kendreaditya/parlai/parlai
#
# Or, if you set up a separate tap repo (kendreaditya/homebrew-tap):
#   brew install kendreaditya/tap/parlai
#
# After every release: bump `url` to the new tag tarball and update `sha256`
# (the latter is computed by `brew install --build-from-source` on first install).
class Parlai < Formula
  include Language::Python::Virtualenv

  desc "Unified CLI for personal AI chat history (ChatGPT/Claude/Gemini/AI Studio/Perplexity/Codex/Claude Code)"
  homepage "https://github.com/kendreaditya/parlai"
  url "https://github.com/kendreaditya/parlai/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256_REPLACE_AFTER_FIRST_RELEASE"
  license "MIT"
  head "https://github.com/kendreaditya/parlai.git", branch: "main"

  depends_on "python@3.12"

  # Heavy dependencies are vendored at install time by virtualenv_install_with_resources.
  # If brew audit asks for `resource` blocks, regenerate with:
  #   brew update-python-resources parlai

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "parlai", shell_output("#{bin}/parlai --help")
  end
end
