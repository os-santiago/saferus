from argparse import Namespace
from pathlib import Path
import unittest
from unittest.mock import patch
from tools.environment_selection import configure, DEFAULT_CLUSTER


class EnvironmentSelectionTests(unittest.TestCase):
    def args(self, cluster=DEFAULT_CLUSTER):
        return Namespace(ai_sdlc_cluster=cluster, ai_sdlc_state=None, state=None)

    def test_legacy_and_konflux_defaults_select_matching_separate_paths(self):
        with patch.object(Path, 'home', return_value=Path('/operator')):
            legacy=configure(self.args(),external=True)
            self.assertEqual(legacy.ai_sdlc_state,Path('/operator/.local/state/ai-sdlc/ai-sdlc-saferus'))
            self.assertEqual(legacy.state,Path('/operator/.local/state/saferus/ai-sdlc-production'))
            new=configure(self.args('ai-sdlc-konflux-reviewed'),external=True)
            self.assertEqual(new.ai_sdlc_state,Path('/operator/.local/state/ai-sdlc/ai-sdlc-konflux-reviewed'))
            self.assertEqual(new.state,Path('/operator/.local/state/saferus/ai-sdlc-konflux-reviewed-production'))
            self.assertNotEqual(new.state,legacy.state)

    def test_explicit_paths_are_preserved(self):
        args=self.args('ai-sdlc-konflux-reviewed');args.state=Path('/application');args.ai_sdlc_state=Path('/runtime')
        configure(args,external=True)
        self.assertEqual(args.state,Path('/application'));self.assertEqual(args.ai_sdlc_state,Path('/runtime'))

    def test_invalid_cluster_fails_before_path_derivation(self):
        for value in ('../x','UPPER','a'*37,'/tmp/foreign',None):
            args=self.args(value)
            with patch.object(Path,'home',side_effect=AssertionError('path derived before validation')),self.assertRaises(ValueError):
                configure(args,external=True)
            self.assertIsNone(args.state);self.assertIsNone(args.ai_sdlc_state)

    def test_non_environment_command_cannot_select_another_cluster(self):
        args=self.args('ai-sdlc-konflux-reviewed')
        with self.assertRaises(ValueError):configure(args,external=False)
        self.assertIsNone(args.state);self.assertIsNone(args.ai_sdlc_state)


if __name__ == '__main__':
    unittest.main()
