#!/usr/bin/env python3
"""Exercise the base2 shared-store contract and a real c2j recipe in mega.

Adapted from colony-2/base tests/shared_nix_store at
d6f7c04203443020f18448352eded6b473f250b1. Requires Docker and network access
for the initial signed fixture only; consumers run without networking.
"""
import argparse
import json
import shlex
import subprocess
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor


def docker(*args, check=True, timeout=300):
    result = subprocess.run(['docker', *args], check=False, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError(f'docker {args[:3]} failed:\n{result.stdout}\n{result.stderr}')
    return result


def volume():
    name = 'mega-nix-test-' + uuid.uuid4().hex
    docker('volume', 'create', name)
    return name


class NixAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = volume()
        cls.addClassCleanup(lambda: docker('volume', 'rm', cls.fixture))
        # Preserve signatures from the public cache, then test offline consumers.
        result = cls.run_image(IMAGE, '''
import json, pathlib, subprocess
registry=json.loads(pathlib.Path('/etc/nix/registry.json').read_text())
rev=registry['flakes'][0]['to']['rev']
expr='let p = import (builtins.fetchTarball "https://github.com/NixOS/nixpkgs/archive/'+rev+'.tar.gz") {}; in { hello = p.hello.outPath; figlet = p.figlet.outPath; }'
paths=json.loads(subprocess.check_output(['nix','eval','--impure','--json','--expr',expr]))
subprocess.run(['nix','copy','--from','https://cache.nixos.org','--to','file:///fixture/binary',*paths.values()],check=True)
print(json.dumps(paths))
''', network='bridge', python=True)
        cls.paths = json.loads(result.stdout)

    @classmethod
    def run_image(cls, image, command, *, store=None, source=True, network='none',
                  python=False, check=True, readonly=False):
        args = ['run', '--rm', '--platform', PLATFORM, '--network', network,
                '--mount', f'type=volume,src={cls.fixture},dst=/fixture,volume-nocopy',
                '-e', 'NIX_CONFIG=extra-substituters =\nsubstituters = ' +
                ('file:///fixture/binary?trusted=false' if source is True else source or '')]
        if store:
            args += ['--mount', f'type=volume,src={store},dst=/nix,volume-nocopy' +
                     (',readonly' if readonly else '')]
        # Also exercise images that normally start DinD via their entrypoint.
        args += ['--entrypoint', 'python3' if python else '/bin/bash', image,
                 '-c' if python else '-euc', command]
        return docker(*args, check=check)

    def setUp(self):
        self.store = volume()
        self.addCleanup(lambda: docker('volume', 'rm', self.store))

    def run_in(self, command, **kwargs):
        return self.run_image(IMAGE, command, store=self.store, **kwargs)

    def validate(self):
        self.run_in('''
import pathlib, sqlite3, subprocess, sys
db=sqlite3.connect('file:/nix/var/nix/db/db.sqlite?mode=ro',uri=True)
assert db.execute('pragma integrity_check').fetchall()==[('ok',)]
assert not db.execute('pragma foreign_key_check').fetchall()
paths=json.loads(PATHS)
closure=subprocess.check_output(['nix-store','-qR',*paths.values()],text=True).split()
subprocess.run(['nix-store','--check-validity',*closure],check=True)
for path in closure:
    recorded=subprocess.check_output(['nix-store','-q','--hash',path],text=True).strip()
    actual=subprocess.check_output(['nix-hash','--type','sha256','--base32',path],text=True).strip()
    assert recorded=='sha256:'+actual, path
'''.replace('paths=json.loads(PATHS)', 'import json\npaths=json.loads(' + repr(json.dumps(self.paths)) + ')'),
                    python=True, source=False)

    def test_configuration_and_empty_store(self):
        command = "nix --version; cat /etc/nix/nix.conf /etc/nix/registry.json; readlink /root/.nix-profile"
        self.assertEqual(self.run_in(command).stdout, self.run_image(REFERENCE, command).stdout)
        self.run_in('test -z "$(ls -A /nix)"; nix --version; c2j version; '
                    'bash -lc \'test "$NIX_REMOTE" = local; '
                    'case ":$PATH:" in *":$HOME/.local/state/nix/profile/bin:"*) ;; *) exit 1;; esac; '
                    'nix --version\'; '
                    'test -z "$(ls -A /nix)"; base-store-preflight')
        for executable, arg in [('/usr/bin/nix', '--version'), ('c2j', 'version')]:
            docker('run', '--rm', '--platform', PLATFORM, '--network', 'none',
                   '--mount', f'type=volume,src={self.store},dst=/nix,volume-nocopy',
                   '--entrypoint', executable, IMAGE, arg)
        self.assertNotEqual(self.run_in('base-store-preflight --require-initialized', check=False).returncode, 0)
        self.run_in('nix-store --init; base-store-preflight --require-initialized')
        self.run_image(IMAGE, 'test -z "$(ls -A /nix)"; nix-store --init; base-store-preflight --require-initialized')

    def test_cold_substitution_and_offline_cross_image_reuse(self):
        for producer, consumer in [(IMAGE, REFERENCE), (REFERENCE, IMAGE)]:
            with self.subTest(producer=producer):
                store = volume()
                self.addCleanup(lambda s=store: docker('volume', 'rm', s))
                result = self.run_image(producer, 'nix-store -r ' + self.paths['hello'], store=store)
                self.assertIn('copying path', result.stderr)
                result = self.run_image(consumer, 'nix build --out-link /tmp/result ' +
                                        self.paths['hello'] + '; /tmp/result/bin/hello', store=store, source=False)
                self.assertIn('Hello, world!', result.stdout)
                self.assertNotIn('copying path', result.stderr)

    def test_concurrent_installs_and_private_profiles(self):
        self.run_in('nix-store --init')
        # Separate PID namespaces and images, one initialized live store.
        jobs = [(IMAGE, self.paths['hello']), (REFERENCE, self.paths['figlet']),
                (IMAGE, self.paths['figlet']), (REFERENCE, self.paths['hello'])]
        def install(job):
            image, path = job
            # Modern Nix disables substitution without a network interface;
            # explicitly enable our signed, local file-cache fixture.
            result = self.run_image(image, 'nix --option substitute true profile install ' + path +
                                    '; readlink -f /root/.local/state/nix/profile', store=self.store)
            return result.stdout.strip()
        with ThreadPoolExecutor(4) as pool:
            profiles = list(pool.map(install, jobs))
        self.assertEqual(profiles[0], profiles[3])
        self.assertEqual(profiles[1], profiles[2])
        self.assertNotEqual(profiles[0], profiles[1])
        empty = self.run_in('nix profile list --json', source=False)
        self.assertEqual(json.loads(empty.stdout)['elements'], {})
        self.validate()

    def test_c2j_recipe(self):
        recipe = {'id': 'mega_shared_nix', 'version': '1.0', 'input_schema': {},
                  'sequence': [{'id': 'install', 'op': 'command_execution', 'inputs': {
                      'run': 'nix --option substitute true build --out-link /tmp/result ' + self.paths['hello'] +
                             ' && /tmp/result/bin/hello'}}],
                  'outputs': {'message': '${{ sequence.install.outputs.stdout }}'}}
        suite = {'cases': [{'id': 'shared_nix', 'type': 'recipe_case',
                           'mocks': {'ops': [{'match': {'op': 'command_execution'},
                                             'behavior': {'mode': 'passthrough'}}]},
                           'assertions': [{'type': 'output_equals', 'path': 'message',
                                           'value': 'Hello, world!'}]}]}
        self.run_in('nix-store --init')
        script = ('from pathlib import Path; import subprocess\n'
                  'Path("/tmp/recipe.yaml").write_text(' + repr(json.dumps(recipe)) + ')\n'
                  'Path("/tmp/recipe.test.yaml").write_text(' + repr(json.dumps(suite)) + ')\n'
                  'subprocess.run(["c2j","test","run","--recipe-file","/tmp/recipe.yaml",'
                  '"--file","/tmp/recipe.test.yaml","--artifact-mode","inline"],check=True)')
        for source in (True, False):
            result = self.run_in(script, python=True, source=source)
            self.assertIn('shared_nix passed', result.stdout)
        result = self.run_in('nix path-info --sigs ' + self.paths['hello'], source=False)
        self.assertIn('cache.nixos.org-1:', result.stdout)

    def test_local_builds_are_rejected(self):
        for prefer in ('false', 'true'):
            expression = ('derivation { name="mega-forbidden"; system=builtins.currentSystem; '
                          'builder="/bin/sh"; args=["-c" "touch /tmp/forbidden; echo bad > $out"]; '
                          f'preferLocalBuild={prefer}; }}')
            result = self.run_in('set +e; nix-build --no-out-link --expr ' + shlex.quote(expression) +
                                 '; status=$?; test ! -e /tmp/forbidden || exit 99; exit "$status"',
                                 source=False, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotEqual(result.returncode, 99)
            self.assertIn('local builds are disabled' if prefer == 'true' else 'max-jobs', result.stderr)

    def test_unsigned_substitutes_are_rejected(self):
        self.run_in(r'''
from pathlib import Path
import shutil, subprocess
shutil.copytree('/fixture/binary','/tmp/unsigned')
for info in Path('/tmp/unsigned').glob('*.narinfo'):
    info.write_text('\n'.join(line for line in info.read_text().splitlines() if not line.startswith('Sig:'))+'\n')
result=subprocess.run(['nix-store','--option','substituters','file:///tmp/unsigned?trusted=false',
                      '-r',TARGET],capture_output=True,text=True)
assert result.returncode != 0, result.stdout
assert 'not signed' in result.stderr.lower(), result.stderr
'''.replace('TARGET', repr(self.paths['hello'])), python=True)

    def test_preflight_rejects_unsafe_mounts(self):
        for mutation in ('chown 123:123 /nix', 'chmod 777 /nix', 'chmod 555 /nix',
                         'ln -s /tmp /nix/store'):
            with self.subTest(mutation=mutation):
                store = volume()
                self.addCleanup(lambda s=store: docker('volume', 'rm', s))
                result = self.run_image(IMAGE, mutation + '; base-store-preflight', store=store, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('rejected attachment', result.stderr)
        self.run_in('nix-store --init')
        result = self.run_in('base-store-preflight', readonly=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Read-only file system', result.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--reference', default='ghcr.io/colony-2/base2@sha256:9f9720da133a266d552239e7d4470834fea5500c5930e2a59a7e471c6b2aa22e')
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    IMAGE, REFERENCE, PLATFORM = args.image, args.reference, args.platform
    unittest.main(argv=[__file__], verbosity=2)
