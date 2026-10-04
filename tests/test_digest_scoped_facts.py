from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

from ai_notes import digest, digest_pipeline as pipeline
from ai_notes.digest_claims import validate_claims


def scope(**values):
    result = dict(version=None, platform=None, host_architecture=None,
                  build_architecture=None, installation_path=None,
                  requirement='unknown', conditions=[], evidence_kind='documentation')
    result.update(values)
    return result


class ScopedClaimTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://github.com/obsproject/obs-studio/releases/tag/32.2.2'
        # Frozen release excerpt behind the v4 audit's host/build confusion.
        self.quote = (
            'This change means that OBS Studio versions built for Intel-based Macs but running on Apple Silicon Macs '
            'will automatically update to OBS Studio built for Apple Silicon Macs. '
            'If an installation was using third-party plugins, those plugins will no longer load until replaced with Apple Silicon versions.')
        self.claim = dict(field='usage_conditions', text='Apple Silicon 主机上的 Intel 构建迁移后，已有第三方插件也需匹配架构。',
                          evidence_url=self.url, quote=self.quote,
                          scope=scope(host_architecture='Apple Silicon Macs', build_architecture='Intel-based Macs',
                                      requirement='required', conditions=['If an installation was using third-party plugins']))
        self.contexts = [dict(url=self.url, text=self.quote)]

    def test_obs_host_build_and_plugin_condition_survive_independently(self):
        before = copy.deepcopy(self.claim)
        output = validate_claims([self.claim], self.contexts, require_scope=True)
        self.assertEqual([before], output)
        self.assertEqual('Apple Silicon Macs', output[0]['scope']['host_architecture'])
        self.assertEqual('Intel-based Macs', output[0]['scope']['build_architecture'])
        self.assertEqual(['If an installation was using third-party plugins'], output[0]['scope']['conditions'])
        output[0]['scope']['conditions'].clear()
        self.assertEqual(before, self.claim)

    def test_conditions_must_belong_to_this_claim_not_merely_same_source(self):
        self.contexts[0]['text'] += '\nWindows: install the desktop runtime.'
        self.claim['scope']['platform'] = 'Windows'
        with self.assertRaisesRegex(ValueError, 'platform.*literal span'):
            validate_claims([self.claim], self.contexts, require_scope=True)

    def test_other_document_cannot_supply_a_claims_missing_platform(self):
        self.contexts.append(dict(url='https://docs.example/linux', text='Linux native package needs ffmpeg.'))
        self.claim['scope']['installation_path'] = 'Linux native package'
        with self.assertRaisesRegex(ValueError, 'installation_path.*literal span'):
            validate_claims([self.claim], self.contexts)

    def test_unread_target_cannot_supply_viewed_media_claim(self):
        link = 'A demo is available at https://demo.example/.'
        claim = dict(field='detail', text='提供演示入口，未读取入口内容。', quote=link,
                     evidence_url=self.url, scope=scope(evidence_kind='media_link'))
        self.assertEqual('media_link', validate_claims([claim], [dict(url=self.url, text=link)])[0]['scope']['evidence_kind'])
        claim['evidence_url'] = 'https://demo.example/'
        with self.assertRaisesRegex(ValueError, 'supplied original'):
            validate_claims([claim], [dict(url=self.url, text=link)])

    def test_maintenance_and_usage_reports_remain_distinct_source_kinds(self):
        source = 'Version 3.2.1 fixes import errors. I used it for six months to scan invoices.'
        quotes = [('Version 3.2.1 fixes import errors.', 'maintenance_record'),
                  ('I used it for six months to scan invoices.', 'usage_report')]
        claims = [dict(field='retention_reason', text=text, evidence_url=self.url, quote=text,
                       scope=scope(evidence_kind=kind)) for text, kind in quotes]
        self.assertEqual(['maintenance_record', 'usage_report'],
                         [claim['scope']['evidence_kind'] for claim in validate_claims(claims, [dict(url=self.url, text=source)])])

    def test_literal_check_does_not_pretend_to_prove_host_semantics(self):
        # Both spans occur in the source: only an editorial/semantic review can
        # determine that swapping host and build is wrong. Keep that boundary.
        swapped = copy.deepcopy(self.claim)
        swapped['scope']['host_architecture'] = 'Intel-based Macs'
        swapped['scope']['build_architecture'] = 'Apple Silicon Macs'
        self.assertEqual([swapped], validate_claims([swapped], self.contexts))

    def test_optional_acceleration_does_not_merge_with_basic_install_path(self):
        first = 'macOS DMG bundles ffmpeg; optional cloud translation sends text to the provider.'
        second = 'Linux native package requires ffmpeg.'
        claims = [dict(field='usage_conditions', text='macOS 安装包内含 ffmpeg；可选云翻译会联网。', evidence_url=self.url, quote=first,
                       scope=scope(platform='macOS', installation_path='DMG', requirement='optional',
                                   conditions=['optional cloud translation sends text to the provider'])),
                  dict(field='usage_conditions', text='Linux 原生包另需 ffmpeg。', evidence_url=self.url, quote=second,
                       scope=scope(platform='Linux', installation_path='native package', requirement='required',
                                   conditions=['requires ffmpeg']))]
        validated = validate_claims(claims, [dict(url=self.url, text=first+'\n'+second)], require_scope=True)
        self.assertEqual(['optional', 'required'], [claim['scope']['requirement'] for claim in validated])
        self.assertEqual(['macOS', 'Linux'], [claim['scope']['platform'] for claim in validated])

    def test_unknown_is_retained_without_inventing_universal_scope(self):
        result = validate_claims([self.claim], self.contexts, require_scope=True)
        self.assertIsNone(result[0]['scope']['version'])
        self.assertIsNone(result[0]['scope']['installation_path'])

    def test_legacy_claim_is_unchanged_but_new_usage_review_requires_scope(self):
        legacy = {key:value for key,value in self.claim.items() if key != 'scope'}
        self.assertEqual([legacy], validate_claims([legacy], self.contexts))
        with self.assertRaisesRegex(ValueError, 'requires scope'):
            validate_claims([legacy], self.contexts, require_scope=True)

    def test_unknown_scope_keys_invalid_enums_and_unquoted_conditions_fail(self):
        variants = [dict(extra=True), dict(requirement='always'), dict(evidence_kind='locally_tested'),
                    dict(conditions=['all Intel Macs'])]
        for change in variants:
            with self.subTest(change=change):
                altered = copy.deepcopy(self.claim)
                altered['scope'].update(change)
                with self.assertRaises(ValueError):
                    validate_claims([altered], self.contexts)

    def test_existing_legacy_checkpoint_is_not_rewritten_or_requested_again(self):
        legacy = {key:value for key,value in self.claim.items() if key != 'scope'}
        saved = dict(verified_at=digest._now().isoformat(), facts={'claims':[legacy]}, request_id='old-receipt')
        job = dict(checkpoints={'original:old':copy.deepcopy(saved)})
        model = Mock()
        with patch.object(pipeline.sources, 'fetch') as fetch:
            result = pipeline._review_original('.', job, 'unused', model, {}, 'old', 12)
        self.assertEqual(saved, result)
        self.assertNotIn('scope', result['facts']['claims'][0])
        fetch.assert_not_called()
        model.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
