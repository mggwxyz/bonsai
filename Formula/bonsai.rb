# frozen_string_literal: true

# Formula template for the unpublished Bonsai tap.
class Bonsai < Formula
  include Language::Python::Virtualenv

  desc "Manage per-branch git worktrees with ports and Caddy URLs"
  homepage "https://github.com/mggwxyz/bonsai"
  # Template for the future tap. Before publishing, create the v0.1.0 tag
  # and run brew update-python-resources to add Python resource blocks.
  url "https://github.com/mggwxyz/bonsai.git", tag: "v0.1.0"
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"bonsai", "--version"
  end
end
