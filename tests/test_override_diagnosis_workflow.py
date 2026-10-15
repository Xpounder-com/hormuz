"""Project and supplied launch overrides through the public diagnosis CLI."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests import test_connected_client_lifetime_workflow as lifetime_workflow


class OverrideDiagnosisWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifetime_workflow.ConnectedClientLifetimeWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.root / 'project'
        self.project.mkdir(mode=0o700)
        (self.project / '.git').mkdir()
        (self.project / '.codex').mkdir(mode=0o700)

    def trust(self, level='trusted', project=None):
        path = self.fixture.home / 'config.toml'
        with path.open('a') as output:
            output.write('\n[projects.' + json.dumps(str((project or self.project).resolve())) + ']\ntrust_level = ' + json.dumps(level) + '\n')

    def diagnose(self, *options):
        result = self.fixture.command('diagnose', '--project', str(self.project), *options)
        self.assertNotIn('PRIVATE_', result.stdout)
        receipt = json.loads(result.stdout)
        overrides = receipt['override_diagnosis']
        self.assertEqual(overrides['schema_id'], 'hormuz.client-overrides')
        self.assertEqual(overrides['schema_version'], 1)
        self.assertEqual(overrides['supported_client_version'], '0.147.0')
        self.assertEqual(overrides['running_session'], 'unobserved')
        if 'error' in overrides:
            self.assertEqual(set(overrides['effective_routing'].values()), {'unobserved'})
        return receipt

    def test_trusted_project_override_is_diagnosed_without_changing_configuration(self):
        with self.fixture.configured():
            self.trust()
            project_config = self.project / '.codex/config.toml'
            project_config.write_text('model = "PRIVATE_PROJECT_MODEL"\n')
            self.fixture.command('connect')
            before = (self.fixture.home / 'config.toml').read_bytes()
            result = self.diagnose()
            self.assertEqual(result['project_overrides'], 'detected')
            self.assertEqual(result['action'], 'inspect_project_overrides')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model'], 'overridden')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model_provider'], 'matches')
            self.assertEqual((self.fixture.home / 'config.toml').read_bytes(), before)
            self.assertIn('PRIVATE_PROJECT_MODEL', project_config.read_text())

    def test_supplied_launch_override_reports_bypass_without_echoing_values(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            result = self.diagnose('--launch-arg=--config', '--launch-arg=model_provider="PRIVATE_CLOUD_PROVIDER"')
            self.assertEqual(result['action'], 'inspect_launch_overrides')
            self.assertEqual(result['override_diagnosis']['launch_state'], 'detected')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model_provider'], 'overridden')

    def test_untrusted_project_is_inactive_and_provider_redirection_is_ignored(self):
        with self.fixture.configured():
            config = self.project / '.codex/config.toml'
            config.write_text('model = "PRIVATE_MODEL"\nmodel_provider = "PRIVATE_PROVIDER"\n')
            self.fixture.command('connect')
            inactive = self.diagnose()
            self.assertEqual(inactive['project_overrides'], 'inactive')
            self.assertEqual(set(inactive['override_diagnosis']['effective_routing'].values()), {'matches'})
            self.trust()
            config.write_text('model_provider = "PRIVATE_PROVIDER"\n[model_providers.any]\nbase_url = "https://PRIVATE_HOST"\n')
            ignored = self.diagnose()
            self.assertEqual(ignored['project_overrides'], 'ignored')
            self.assertEqual(ignored['override_diagnosis']['effective_routing']['model_provider'], 'matches')
            self.assertEqual(ignored['override_diagnosis']['layers'][0]['ignored_keys'], ['model_provider', 'model_providers'])

    def test_closest_layer_wins_and_explicit_nested_distrust_is_honored(self):
        import tomllib
        with self.fixture.configured():
            self.trust()
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_ROOT_MODEL"\n')
            nested = self.project / 'sub'
            (nested / '.codex').mkdir(parents=True)
            self.fixture.command('connect')
            owned = tomllib.loads((self.fixture.home / 'config.toml').read_text())['model']
            (nested / '.codex/config.toml').write_text('model = ' + json.dumps(owned) + '\n')
            matching = self.diagnose('--project', str(nested))
            self.assertEqual(matching['override_diagnosis']['effective_routing']['model'], 'matches')
            self.assertEqual(len(matching['override_diagnosis']['layers']), 2)
            self.trust('untrusted', nested)
            overridden = self.diagnose('--project', str(nested))
            self.assertEqual(overridden['override_diagnosis']['effective_routing']['model'], 'overridden')
            self.assertEqual(overridden['override_diagnosis']['layers'][-1]['state'], 'inactive')

    def test_profile_file_and_explicit_model_flag_precedence(self):
        import tomllib
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            owned = tomllib.loads((self.fixture.home / 'config.toml').read_text())['model']
            (self.fixture.home / 'review.config.toml').write_text('model = "PRIVATE_PROFILE_MODEL"\n')
            profile = self.diagnose('--launch-arg=--profile', '--launch-arg=review')
            self.assertEqual(profile['action'], 'inspect_profile_overrides')
            self.assertEqual(profile['override_diagnosis']['effective_routing']['model'], 'overridden')
            (self.project / '.codex/config.toml').write_text('model = ' + json.dumps(owned) + '\n')
            project = self.diagnose('--launch-arg=-p', '--launch-arg=review')
            self.assertEqual(project['override_diagnosis']['effective_routing']['model'], 'matches')
            explicit = self.diagnose('--launch-arg=-m', '--launch-arg=' + owned,
                                     '--launch-arg=-c', '--launch-arg=model="PRIVATE_LATE_CONFIG"')
            self.assertEqual(explicit['override_diagnosis']['effective_routing']['model'], 'matches')

    def test_profile_provider_table_and_launch_provider_changes_are_visible(self):
        import tomllib
        with self.fixture.configured():
            self.fixture.command('connect')
            provider = tomllib.loads((self.fixture.home / 'config.toml').read_text())['model_provider']
            (self.fixture.home / 'review.config.toml').write_text('[model_providers.' + provider + ']\nbase_url = "http://PRIVATE_ENDPOINT/v1"\n')
            changed = self.diagnose('--launch-arg=-p', '--launch-arg=review')
            self.assertEqual(changed['override_diagnosis']['effective_routing']['model_providers'], 'overridden')
            self.assertEqual(changed['action'], 'inspect_profile_overrides')
            changed = self.diagnose('--launch-arg=-c', '--launch-arg=model_providers.' + provider + '.env_key="PRIVATE_ENV"')
            self.assertEqual(changed['override_diagnosis']['effective_routing']['model_providers'], 'overridden')
            self.assertEqual(changed['action'], 'inspect_launch_overrides')

    def test_remote_and_unknown_launch_context_cannot_claim_matching_local_routing(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            remote = self.diagnose('--launch-arg=--remote', '--launch-arg=ws://PRIVATE_HOST')
            self.assertEqual(remote['action'], 'inspect_remote_connection')
            self.assertEqual(set(remote['override_diagnosis']['effective_routing'].values()), {'unobserved'})
            invalid = self.diagnose('--launch-arg=--unknown-PRIVATE_FLAG')
            self.assertEqual(invalid['project_overrides'], 'invalid')
            self.assertEqual(invalid['action'], 'inspect_launch_configuration')
            prompt = self.diagnose('--launch-arg=--', '--launch-arg=PRIVATE_PROMPT --model anything')
            self.assertEqual(prompt['override_diagnosis']['launch_state'], 'none')

    def test_malformed_trusted_project_and_missing_profile_are_safe(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            config = self.project / '.codex/config.toml'
            config.write_text('model = "PRIVATE_INVALID\n')
            invalid = self.diagnose()
            self.assertEqual(invalid['project_overrides'], 'invalid')
            self.assertEqual(invalid['action'], 'inspect_project_configuration')
            config.write_text('')
            missing = self.diagnose('--launch-arg=-p', '--launch-arg=PRIVATE_MISSING_PROFILE')
            self.assertEqual(missing['project_overrides'], 'invalid')

    def test_launch_cd_and_nested_repository_stop_project_search(self):
        with self.fixture.configured():
            self.trust()
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_OUTER_MODEL"\n')
            nested = self.project / 'nested-repo'
            (nested / '.git').mkdir(parents=True)
            self.fixture.command('connect')
            result = self.diagnose('--launch-arg=-C', '--launch-arg=nested-repo')
            self.assertEqual(result['project_overrides'], 'none')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model'], 'matches')

    def test_search_and_oss_flags_do_not_report_original_defaults_as_effective(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            search = self.diagnose('--launch-arg=--search', '--launch-arg=-c', '--launch-arg=web_search="disabled"')
            self.assertEqual(search['override_diagnosis']['effective_routing']['web_search'], 'overridden')
            oss = self.diagnose('--launch-arg=--oss', '--launch-arg=--local-provider=ollama')
            self.assertEqual(oss['override_diagnosis']['effective_routing']['model_provider'], 'overridden')
            self.assertEqual(oss['override_diagnosis']['effective_routing']['model'], 'unobserved')
            local_flag = self.diagnose('--launch-arg=--local-provider=ollama')
            self.assertEqual(local_flag['override_diagnosis']['effective_routing']['model_provider'], 'matches')

    def test_repeated_config_table_replacement_and_opaque_arguments_are_conservative(self):
        import tomllib
        with self.fixture.configured():
            self.fixture.command('connect')
            provider = tomllib.loads((self.fixture.home / 'config.toml').read_text())['model_provider']
            result = self.diagnose('--launch-arg=-c', '--launch-arg=model_providers.' + provider + '.base_url="PRIVATE_OTHER"',
                                   '--launch-arg=-c', '--launch-arg=model_providers={}')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model_providers'], 'matches')
            invalid = self.diagnose('--launch-arg=PRIVATE_PROMPT', '--launch-arg=-c', '--launch-arg=model="PRIVATE_MODEL"')
            self.assertEqual(invalid['project_overrides'], 'invalid')

    def test_unsafe_project_file_retains_connection_diagnosis(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            private = self.project / 'private.toml'
            private.write_text('model = "PRIVATE_NEVER_READ"\n')
            (self.project / '.codex/config.toml').symlink_to(private)
            result = self.diagnose()
            self.assertEqual(result['connection']['phase'], 'connected')
            self.assertEqual(result['project_overrides'], 'invalid')
            self.assertEqual(result['action'], 'inspect_project_configuration')

    def test_profile_provider_selection_is_attributed_to_profile(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            (self.fixture.home / 'review.config.toml').write_text('model_provider = "original"\n')
            result = self.diagnose('--launch-arg=-p', '--launch-arg=review')
            self.assertEqual(result['action'], 'inspect_profile_overrides')
            self.assertEqual({item['source'] for item in result['override_diagnosis']['conflicts']}, {'profile'})

    def test_linked_worktree_inherits_main_repository_trust(self):
        with self.fixture.configured():
            main = self.fixture.root / 'main'
            gitdir = main / '.git/worktrees/selected'
            gitdir.mkdir(parents=True)
            (gitdir / 'commondir').write_text('../..\n')
            (self.project / '.git').rmdir()
            (self.project / '.git').write_text('gitdir: ' + str(gitdir.resolve()) + '\n')
            self.trust(project=main)
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_WORKTREE_MODEL"\n')
            self.fixture.command('connect')
            self.assertEqual(self.diagnose()['project_overrides'], 'detected')

    def test_lexical_project_alias_preserves_explicit_trust_lookup(self):
        with self.fixture.configured():
            alias = self.fixture.root / 'alias'
            alias.symlink_to(self.project, target_is_directory=True)
            path = self.fixture.home / 'config.toml'
            with path.open('a') as output:
                output.write('\n[projects.' + json.dumps(str(alias.absolute())) + ']\ntrust_level = "trusted"\n')
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_ALIAS_MODEL"\n')
            self.fixture.command('connect')
            self.assertEqual(self.diagnose('--project', str(alias))['project_overrides'], 'detected')

    def test_custom_marker_root_uses_independent_repository_trust(self):
        with self.fixture.configured():
            path = self.fixture.home / 'config.toml'
            path.write_text('project_root_markers = ["WORKSPACE"]\n' + path.read_text())
            (self.project / 'WORKSPACE').touch()
            repository = self.project / 'repo'
            (repository / '.git').mkdir(parents=True)
            cwd = repository / 'sub'
            cwd.mkdir()
            self.trust(project=repository)
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_ROOT_MODEL"\n')
            self.fixture.command('connect')
            self.assertEqual(self.diagnose('--project', str(cwd))['project_overrides'], 'detected')

    def test_legacy_profile_and_inspection_limits_do_not_claim_clean_routing(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            path = self.fixture.home / 'config.toml'
            with path.open('a') as output:
                output.write('\n[profiles.review]\nmodel = "PRIVATE_LEGACY"\n')
            (self.fixture.home / 'review.config.toml').write_text('model = "PRIVATE_NEW_PROFILE"\n')
            legacy = self.diagnose('--launch-arg=-p', '--launch-arg=review')
            self.assertEqual(legacy['project_overrides'], 'invalid')
            limited = self.diagnose(*['--launch-arg=--search' for _ in range(129)])
            self.assertEqual(limited['project_overrides'], 'invalid')

    def test_effective_legacy_profile_selectors_are_reported_as_invalid(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            path = self.fixture.home / 'config.toml'
            original = path.read_text()
            profile = self.fixture.home / 'review.config.toml'
            profile.write_text('')
            for flags in ((), ('--launch-arg=-p', '--launch-arg=review')):
                with self.subTest(source='user', flags=flags):
                    path.write_text('profile = "PRIVATE_LEGACY"\n' + original)
                    self.assertEqual(self.diagnose(*flags)['project_overrides'], 'invalid')
            path.write_text(original)
            profile.write_text('profile = "PRIVATE_PROFILE_LEGACY"\n')
            selected = self.diagnose('--launch-arg=-p', '--launch-arg=review')
            self.assertEqual(selected['project_overrides'], 'invalid')
            self.assertEqual(selected['action'], 'inspect_profile_configuration')
            profile.write_text('')
            generic = self.diagnose('--launch-arg=-c', '--launch-arg=profile="PRIVATE_LAUNCH_LEGACY"')
            self.assertEqual(generic['project_overrides'], 'invalid')
            self.assertEqual(generic['action'], 'inspect_launch_configuration')
            (self.project / '.codex/config.toml').write_text('profile = "PRIVATE_IGNORED"\n')
            ignored = self.diagnose()
            self.assertEqual(ignored['project_overrides'], 'ignored')
            self.assertEqual(ignored['override_diagnosis']['layers'][0]['ignored_keys'], ['profile'])

    def test_symlink_loop_is_a_content_free_inspection_failure(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            private = self.project / 'PRIVATE_LOOP'
            private.symlink_to(private.name, target_is_directory=True)
            (self.project / '.codex').rmdir()
            (self.project / '.codex').symlink_to(private, target_is_directory=True)
            result = self.diagnose()
            self.assertEqual(result['connection']['phase'], 'connected')
            self.assertEqual(result['project_overrides'], 'invalid')
            self.assertEqual(result['action'], 'inspect_project_configuration')

    def test_dangling_configuration_directory_alias_is_not_clean_routing(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            (self.project / '.codex').rmdir()
            (self.project / '.codex').symlink_to('PRIVATE_MISSING_DIRECTORY', target_is_directory=True)
            result = self.diagnose()
            self.assertEqual(result['connection']['phase'], 'connected')
            self.assertEqual(result['project_overrides'], 'invalid')
            self.assertEqual(result['action'], 'inspect_project_configuration')

    def test_safe_configuration_directory_alias_is_inspected(self):
        with self.fixture.configured():
            self.trust()
            self.fixture.command('connect')
            directory = self.project / 'PRIVATE_CONFIGURATION_DIRECTORY'
            directory.mkdir(mode=0o700)
            (directory / 'config.toml').write_text('model = "PRIVATE_ALIASED_MODEL"\n')
            (self.project / '.codex').rmdir()
            (self.project / '.codex').symlink_to(directory, target_is_directory=True)
            result = self.diagnose()
            self.assertEqual(result['connection']['phase'], 'connected')
            self.assertEqual(result['project_overrides'], 'detected')
            self.assertEqual(result['override_diagnosis']['effective_routing']['model'], 'overridden')
            self.assertEqual(result['action'], 'inspect_project_overrides')

    def test_invalid_typed_project_trust_preserves_connection_diagnosis(self):
        with self.fixture.configured():
            self.fixture.command('connect')
            path = self.fixture.home / 'config.toml'
            original = path.read_text()
            (self.project / '.codex/config.toml').write_text('model = "PRIVATE_MODEL"\n')
            for value in ('[]', '{}', 'false', '1'):
                with self.subTest(value=value):
                    path.write_text(original + '\n[projects.' + json.dumps(str(self.project.resolve())) +
                                    ']\ntrust_level = ' + value + '\n')
                    result = self.diagnose()
                    self.assertEqual(result['connection']['phase'], 'connected')
                    self.assertEqual(result['project_overrides'], 'invalid')
