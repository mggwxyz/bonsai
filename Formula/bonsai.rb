# frozen_string_literal: true

# Homebrew formula for Bonsai.
class Bonsai < Formula
  include Language::Python::Virtualenv

  desc "Manage per-branch git worktrees with ports and Caddy URLs"
  homepage "https://github.com/mggwxyz/bonsai"
  url "https://github.com/mggwxyz/bonsai.git", tag: "v0.1.2"
  license "MIT"

  depends_on "caddy"
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"bonsai", "--version"
  end
end
