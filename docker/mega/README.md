# shai-mega

A comprehensive Debian-based development environment with modern programming languages, build tools, and AI/developer CLIs.

## What's Included

### Programming Languages & Runtimes
- **Go** - Latest stable version (overridable)
- **Rust** - Latest stable version (overridable)
- **Node.js** - Latest stable version (overridable)
- **Python 3** - From Debian repositories with pip, uv/uvx, venv, and dev tools
- **Java** - OpenJDK from Debian repositories
- **C/C++** - GCC, Clang, and build essentials

### Package Managers & Build Tools
- **npm, yarn, pnpm** - Latest versions from npm registry
- **cargo** - Rust package manager
- **pip** - Python package manager
- **uv / uvx** - Fast Python package manager and tool runner
- **Nix 2.34.3** - base2-compatible shared store, flakes, and signed binary caches
- **bazel** - Bazel build system
- **build-essential** - GCC, make, and core build tools

### AI & Development CLIs
- **@colony2/c2m** - C2M agent memory CLI
- **@colony2/c2j** - C2J job system CLI
- **@openai/codex** - OpenAI Codex CLI
- **@google/gemini-cli** - Google Gemini CLI
- **@anthropic-ai/claude-code** - Anthropic Claude Code CLI
- **@moonrepo/cli** - Moon build system
- **wrangler** - Cloudflare Workers CLI
- **playwright** - Browser automation (includes Chromium with deps)

### Cloud, IaC & Kubernetes CLIs
- **OpenTofu** - Infrastructure as code CLI
- **awscli** - AWS CLI
- **gcloud** - Google Cloud CLI
- **az** - Azure CLI
- **pulumi** - Pulumi CLI
- **kubectl** - Kubernetes CLI
- **helm** - Kubernetes package manager
- **kustomize** - Kubernetes manifest customization CLI

### System Tools & Utilities
- **Shells**: bash, zsh (available, not configured as default)
- **Version Control**: git
- **Utilities**: jq, curl, wget, rsync, tree, htop, ncdu, strace
- **Networking**: iproute2, net-tools, openssh-client, sshpass, tinyproxy, dnsmasq
- **Editors**: nano, vim-tiny
- **Process Management**: supervisor, inotify-tools

## Image Details

- **Base Image**: debian:bookworm-slim
- **Working Directory**: `/src`
- **Architectures**: amd64 (x86_64), arm64 (aarch64)
- **Configuration**: Uses Linux standard patterns (`/etc/profile.d`, `/etc/supervisor/conf.d`)

## Version Management

This image uses a **multi-stage build** to automatically fetch the latest versions of all dependencies at build time. When you build the image without specifying versions, it will use the latest stable releases.

### Automatic Latest Versions

By default, the following are fetched automatically:
- **Languages**: Go, Rust, Node.js
- **npm packages**: npm, yarn, pnpm, c2m, c2j, codex, gemini-cli, claude-code, moon, wrangler, playwright
- **other pinned installs**: uv, bazel, opentofu, awscli, gcloud, azure-cli, pulumi, helm, kubectl, kustomize

### Overriding Versions

You can pin specific versions using build arguments:

```bash
# Pin Go version
docker build --build-arg GO_VERSION=1.24.0 .

# Pin Rust version
docker build --build-arg RUST_VERSION=1.83.0 .

# Pin Node.js version
docker build --build-arg NODE_VERSION=22.11.0 .

# Pin multiple versions
docker build \
  --build-arg GO_VERSION=1.24.0 \
  --build-arg RUST_VERSION=1.83.0 \
  --build-arg NODE_VERSION=22.11.0 \
  .
```

### Available Build Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `GO_VERSION` | Go version (e.g., `1.24.0`) | Latest from go.dev |
| `RUST_VERSION` | Rust version (e.g., `1.83.0`) | Latest stable |
| `NODE_VERSION` | Node.js version (e.g., `22.11.0`) | Latest from nodejs.org |
| `NIX_BASE_IMAGE` | Certified base2 runtime source; changing requires compatibility tests | Pinned multi-architecture base2 digest in Dockerfile |

## Nix and c2j

Nix follows the [base2 shared-store contract](https://github.com/colony-2/base/blob/d6f7c04203443020f18448352eded6b473f250b1/docs/shared-nix-store.md).
The image copies base2's exact Nix 2.34.3 runtime, cache configuration, pinned
nixpkgs registry, build-denial hook, and `base-store-preflight` diagnostic. Its
ELF loader and libraries are isolated under `/opt/nix-runtime/lib` because base2
uses a newer Ubuntu than mega's Debian and Ubuntu DinD variants. Only Nix's ELF
interpreter and library search path are changed; system libraries and child
process environments are unaffected. Package versions and license notices are
retained under `/opt/nix-runtime`.

`/nix` ships empty. Nix runs directly as root with `NIX_REMOTE=local`, without a
daemon or startup wrapper. Signed substitutes from `cache.nixos.org` and
`colony2.cachix.org` are enabled; local/remote builds, fallback builds, automatic
GC, and automatic store optimization are disabled. Profiles live privately in
`/root/.local/state/nix/profiles`, outside the shared store. The profile's `bin`
directory follows the image's normal tools on PATH, including in login shells.

For one container, Nix initializes its ephemeral store on first use. For a shared
store, the provider must initialize a local Docker volume once before attaching
workers (root UID/GID 0), then validate each attachment:

```bash
docker volume create mega-nix
docker run --rm --mount type=volume,src=mega-nix,dst=/nix,volume-nocopy \
  --entrypoint /bin/sh shai-mega -ec \
  'base-store-preflight; nix-store --init; base-store-preflight --require-initialized'
docker run --rm --mount type=volume,src=mega-nix,dst=/nix,volume-nocopy \
  --entrypoint nix shai-mega run nixpkgs#hello
```

Share the whole `/nix`, including its database. Keep profiles private and never
run live GC, deletion, repair, or optimization while workers are attached. Retire
whole idle volumes instead. Use the same architecture and compatibility group
(`colony-nix-store-v1`) as base2. This does not enable Nix for Shai's non-root
users; the shared-store/c2j worker contract uses root.

### Runtime acceptance tests

```bash
python3 docker/mega/tests/test-nix.py --image shai-mega --platform linux/arm64
```

The tests use real signed packages and an actual c2j recipe: cold installation,
offline reuse in both directions with base2, concurrent installations, private
profiles, database integrity/NAR hashes, rejected unsigned packages and local
builds, direct entrypoints, and attachment checks. CI runs them for both mega
variants on amd64 and arm64 before publishing the combined `latest` manifests.

To test only the Nix addition against an existing mega image:

```bash
docker build --target nix-base \
  --build-arg BASE_IMAGE=ghcr.io/colony-2/shai-mega:latest \
  -t shai-mega:nix-test docker/mega
python3 docker/mega/tests/test-nix.py --image shai-mega:nix-test --platform linux/arm64
```

When updating `NIX_BASE_IMAGE`, run against the previous certified base2 digest
using `--reference` as well; matching schema numbers alone do not establish
shared-store compatibility.

## Usage Examples

### Basic Usage

```bash
# Build with latest versions
docker build -t shai-mega .

# Run interactive shell
docker run -it --rm shai-mega

# Run with volume mount
docker run -it --rm -v $(pwd):/src shai-mega
```

### Build with Specific Versions

```bash
# Build with Go 1.24.0 and latest Rust/Node
docker build --build-arg GO_VERSION=1.24.0 -t shai-mega:go1.24 .

# Build with specific versions for all languages
docker build \
  --build-arg GO_VERSION=1.24.0 \
  --build-arg RUST_VERSION=1.83.0 \
  --build-arg NODE_VERSION=20.11.0 \
  -t shai-mega:pinned .
```

### Development Workflow

```bash
# Start container with project mounted
docker run -it --rm \
  -v $(pwd):/src \
  -w /src \
  shai-mega

# Inside container
go build ./...
cargo build
npm install
python3 -m venv venv && source venv/bin/activate
```

## Layer Structure

The Dockerfile is organized into distinct layers for optimal caching:

The `nix-runtime` stage extracts the pinned base2 runtime. The `nix-base` stage
adds it and its shared-store configuration before the following tool layers.

1. **Base system packages** - apt packages and build tools
2. **Go installation** - Go toolchain
3. **Rust installation** - Rust compiler and cargo
4. **Node.js installation** - Node runtime and npm
5. **Python verification** - Verify Python installation
6. **uv** - Python package manager and `uvx` runner
7. **Java verification** - Verify JDK installation
8. **npm package managers** - npm, yarn, pnpm
9. **AI CLI tools** - AI assistant CLIs
10. **C2M/C2J CLIs** - `@colony2/c2m`, `@colony2/c2j`
11. **Wrangler** - Cloudflare Workers CLI
12. **Bazel** - Bazel release binary
13. **OpenTofu** - IaC CLI
14. **AWS CLI** - official tagged source build
15. **Google Cloud CLI** - official tarball
16. **Azure CLI** - Python venv install
17. **Pulumi** - official release tarball
18. **Helm** - official Go module build
19. **kubectl** - official Kubernetes apt package
20. **Kustomize** - official Go module build
21. **Playwright** - Browser automation with Chromium
22. **System configuration** - Locale generation
23. **Profile.d scripts** - Environment variable configuration

This structure ensures that:
- Changing npm package versions doesn't rebuild Go/Rust/Node layers
- Changing Go version doesn't rebuild Rust/Node layers
- Language installations are independent and cached separately
- Configuration is managed via standard Linux patterns

## Environment Variables

All environment variables are configured using Linux standard patterns via `/etc/profile.d` scripts and `/etc/zsh/zshenv` for zsh compatibility.

### Configuration Files

#### `/etc/profile.d/10-locale.sh`
- `LANG=en_US.UTF-8`
- `LC_ALL=en_US.UTF-8`
- `LANGUAGE=en_US:en`

#### `/etc/profile.d/10-proxy.sh`
**Note:** Proxy settings only apply to non-root users (uid != 0)
- `HTTP_PROXY=http://127.0.0.1:8888`
- `HTTPS_PROXY=http://127.0.0.1:8888`
- `NO_PROXY=localhost,127.0.0.1,::1`
- `http_proxy=http://127.0.0.1:8888`
- `https_proxy=http://127.0.0.1:8888`
- `no_proxy=localhost,127.0.0.1,::1`

#### `/etc/profile.d/10-lang-paths.sh`
- `RUSTUP_HOME=/usr/local/rustup`
- `CARGO_HOME=/usr/local/cargo`
- `PATH=/usr/local/go/bin:/usr/local/cargo/bin:$PATH`
- `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`

## Supervisor

The image includes supervisor for process management. The `/etc/supervisor/conf.d/` directory is available for adding service configurations.

Example usage:
```bash
# Create a supervisor config for your service
cat > /etc/supervisor/conf.d/myservice.conf <<EOF
[program:myservice]
command=/path/to/your/service
autostart=true
autorestart=true
stdout_logfile=/var/log/supervisor/myservice.log
stderr_logfile=/var/log/supervisor/myservice.err
EOF

# Start supervisor
supervisord -n
```

## Build Performance

### Multi-stage Build

The build uses a separate `version-fetcher` stage that:
1. Queries upstream sources for latest versions
2. Stores versions in files
3. Copies version files into main build

This approach ensures:
- **Efficient caching**: Version files only change when upstream versions change
- **Automatic updates**: Rebuilding fetches the latest versions
- **Reproducibility**: Build args allow pinning for CI/CD

### Cache Optimization Tips

```bash
# Use BuildKit for better caching
DOCKER_BUILDKIT=1 docker build .

# Use cache from previous builds
docker build --cache-from shai-mega:latest .

# Build with multiple cache sources
docker build \
  --cache-from shai-mega:latest \
  --cache-from shai-mega:dev \
  .
```

## License

This Dockerfile and configuration are provided as-is for development purposes.
