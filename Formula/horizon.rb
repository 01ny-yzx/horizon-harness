class Horizon < Formula
  desc "Terminal AI agent"
  homepage "https://github.com/01ny-yzx/horizon-harness"
  version "1.0.2"
  license "MIT"
  depends_on :macos
  on_arm do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.2/horizon-1.0.2-macos-arm64.tar.gz"
    sha256 "c2311771cc4755cf0870e81529adbee56a618d9d22c1a6fdfafdd0b9c9b7eca9"
  end
  on_intel do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.2/horizon-1.0.2-macos-x64.tar.gz"
    sha256 "762ccab9edc225bb4ce65e7dca15c1158a73e7620f1a7b689fdb27de68878264"
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
