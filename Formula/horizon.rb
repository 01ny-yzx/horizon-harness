class Horizon < Formula
  desc "Terminal AI agent"
  homepage "https://github.com/01ny-yzx/horizon-harness"
  version "1.0.4"
  license "MIT"
  depends_on :macos
  on_arm do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.4/horizon-1.0.4-macos-arm64.tar.gz"
    sha256 "6533480e460c99f7e5fe96111b1edb5a09a1667afc2fbcdd4c7c3e7d0f535171"
  end
  on_intel do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.4/horizon-1.0.4-macos-x64.tar.gz"
    sha256 "2dcc3476bc98b5d2c373382eb7e1e020bf824d2fcb33a66fa2e7fbd834f85c71"
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
