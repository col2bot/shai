"""Export base2's Nix and ELF dependencies without depending on its host distro.

Only the ELF interpreter and RPATH of the two executables change. The private
loader/libraries never enter the system linker path or child-process environment.
Nix's compiled-in /usr paths, multicall symlinks, and shared-store policy remain
unchanged. /nix is deliberately never opened or copied during this export.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path('/nix-export')
LIB = Path('/opt/nix-runtime/lib')


def copy(source, destination=None):
    source = Path(source)
    target = ROOT / str(destination or source).lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)
    return target


version = subprocess.check_output(['nix', '--version'], text=True).strip()
assert version == 'nix (Nix) 2.34.3', version
executables = [Path('/usr/bin/nix'), Path('/usr/libexec/nix-nswrapper')]
dependencies = set()
for executable in executables:
    linked = subprocess.check_output(['ldd', str(executable)], text=True)
    assert 'not found' not in linked, linked
    dependencies.update(re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(', linked, re.MULTILINE))

for dependency in sorted(dependencies):
    # Dereference distro symlinks; retain the SONAME filename in the private dir.
    target = ROOT / str(LIB / Path(dependency).name).lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dependency, target)

for executable in executables:
    target = copy(executable)
    loader = subprocess.check_output(['patchelf', '--print-interpreter', str(executable)], text=True).strip()
    assert loader in dependencies, loader
    subprocess.run(['patchelf', '--set-interpreter', str(LIB / Path(loader).name),
                    '--force-rpath', '--set-rpath', str(LIB), str(target)], check=True)

for link in Path('/usr/bin').glob('nix-*'):
    assert link.is_symlink() and link.readlink() == Path('nix'), link
    copy(link)
for source in ('/usr/libexec/nix/build-remote', '/etc/nix/nix.conf',
               '/etc/nix/registry.json', '/usr/local/libexec/nix-deny-build',
               '/usr/local/bin/base-store-preflight'):
    copy(source)

# Retain distribution copyright/license notices for the copied runtime closure.
packages = {'nix-bin'}
for dependency in dependencies:
    real = str(Path(dependency).resolve())
    owners = subprocess.check_output(['dpkg-query', '-S', real], text=True)
    packages.update(line.split(': /', 1)[0] for line in owners.splitlines())
manifest = {}
for package in sorted(packages):
    manifest[package] = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}', package], text=True)
    notice = Path('/usr/share/doc') / package.split(':')[0] / 'copyright'
    if notice.exists():
        copy(notice.resolve(), Path('/opt/nix-runtime/licenses') / package / 'copyright')
common = Path('/usr/share/common-licenses')
shutil.copytree(common, ROOT / 'opt/nix-runtime/licenses/common-licenses')
(ROOT / 'opt/nix-runtime/packages.json').write_text(json.dumps(manifest, indent=2) + '\n')
