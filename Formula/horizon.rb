class Horizon < Formula
  desc "Terminal AI agent"
  homepage "https://github.com/01ny-yzx/horizon-harness"
  version "1.0.0"
  license "MIT"
  depends_on :macos
  on_arm do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.0/horizon-1.0.0-macos-arm64.tar.gz"
    sha256 "41b73ba01ed35e77d72ec0ac2df255a7205552258a348692cdcbbaf3503f5dd8"
  end
  on_intel do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.0/horizon-1.0.0-macos-x64.tar.gz"
    sha256 "749db207f02bc7445c8cd3bdd40602d22d144b766f678f162e57365a648068d0"
  end
  def install
    odie "This build requires macOS 11.0+" if Hardware::CPU.arm? && MacOS.version < MacOSVersion.new("11.0")
    odie "This build requires macOS 10.13+" if Hardware::CPU.intel? && MacOS.version < MacOSVersion.new("10.13")
    libexec.install Dir["*"]
    bin.install_symlink libexec/"horizon"
  end
  test do
    system bin/"horizon", "--version"
  end
end
