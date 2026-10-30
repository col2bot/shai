export RUSTUP_HOME=/usr/local/rustup
export CARGO_HOME=/usr/local/cargo
export PATH=/usr/local/go/bin:/usr/local/cargo/bin:$PATH
export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Also sourced by /etc/zsh/zshenv, which does not read /etc/profile.d itself.
. /etc/profile.d/10-nix.sh
