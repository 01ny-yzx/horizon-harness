class Horizon < Formula
  desc "Terminal AI agent"
  homepage "https://github.com/01ny-yzx/horizon-harness"
  version "1.0.3"
  license "MIT"
  depends_on :macos
  on_arm do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.3/horizon-1.0.3-macos-arm64.tar.gz"
    sha256 "42d028c03293ec2acb6754dac9018402db88df3bbbd0abf50be101935c77e081"
  end
  on_intel do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.3/horizon-1.0.3-macos-x64.tar.gz"
    sha256 "cc7a76ff3923081c373646dd8f6d55afec67127362112de4d7869b961b3f87ed"
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
