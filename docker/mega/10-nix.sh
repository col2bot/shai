export NIX_REMOTE=local
export NIX_SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
case ":$PATH:" in
    *":$HOME/.local/state/nix/profile/bin:"*) ;;
    *) export PATH=$PATH:$HOME/.local/state/nix/profile/bin ;;
esac
