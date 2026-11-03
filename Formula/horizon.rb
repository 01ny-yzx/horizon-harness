class Horizon < Formula
  desc "Terminal AI agent"
  homepage "https://github.com/01ny-yzx/horizon-harness"
  version "1.0.1"
  license "MIT"
  depends_on :macos
  on_arm do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.1/horizon-1.0.1-macos-arm64.tar.gz"
    sha256 "724037339dc1f075a36b00505ad712d6546b0515df9355806c5e0510f6a88db8"
  end
  on_intel do
    url "https://github.com/01ny-yzx/horizon-harness/releases/download/v1.0.1/horizon-1.0.1-macos-x64.tar.gz"
    sha256 "22419f255040d4054ed3e47b8751b95fe3be1bef1609ac04830117adb42428eb"
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
