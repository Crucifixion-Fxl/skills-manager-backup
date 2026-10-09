"""Current Tencent input routes correctly; retired AWS CN archives stay readable."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'fleet_report', Path(__file__).resolve().parents[1] / 'generate_report.py'
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class CNInventoryReportTest(unittest.TestCase):
    def render_input(self, records, unreachable=()):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'classified.ndjson'
            source.write_text('\n'.join(json.dumps(row) for row in records))
            return report.render_html(report.load_rows(source), unreachable)

    def record(self, cluster, name='example'):
        return dict(cluster=cluster, name=name, severity='P1', category='app',
                    sync='Synced', health='Degraded', tier='prod', anomaly=['Degraded'])

    def test_cn_prod_and_staging_keep_distinct_correct_links(self):
        expected = {
            'tencent-100014919455-cn-main': ('cn-main', 'argocd-cn-k8s.addx.live'),
            'tencent-100052802231-cn-staging': ('tencent-100052802231-cn-staging', 'argocd-cn-staging.addx.live'),
        }
        output = self.render_input([self.record(directory) for directory in expected])
        for directory, (name, host) in expected.items():
            self.assertIn(f'data-cluster="{name}"', output)
            self.assertIn(f'https://{host}/applications/argo-cd/example?view=tree', output)
            if directory != name:
                self.assertNotIn(f'data-cluster="{directory}"', output)
        self.assertNotIn('https://argocd-cn.addx.live', output)
        self.assertNotIn('https://argocd-cn-tech-service.addx.live', output)

    def test_pending_tech_service_stays_visible_without_current_link(self):
        cluster = 'tencent-100052802231-cn-tech-service'
        self.assertIsNone(report.app_url(cluster, 'example'))
        self.assertIsNone(report.cluster_url(cluster))
        output = self.render_input([self.record(cluster)])
        self.assertIn(f'data-cluster="{cluster}"', output)
        self.assertIn('待切换：argocd-cn-tech-service-tke.addx.live', output)
        self.assertNotIn('href="https://argocd-cn-tech-service-tke.addx.live', output)
        self.assertNotIn('argocd-cn-tech-service-2231-tke.addx.live', output)
        counts, clusters, stats = report.compute_summary([self.record(cluster)])
        self.assertEqual(clusters, [cluster])
        self.assertEqual(counts['P1'], 1)
        self.assertEqual(stats[cluster]['total'], 1)

    def test_current_aliases_share_one_summary_bucket(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.ndjson'
            source.write_text('\n'.join(json.dumps(self.record(cluster)) for cluster in
                                        ['cn-main', 'tencent-100014919455-cn-main']))
            _, clusters, stats = report.compute_summary(report.load_rows(source))
        self.assertEqual(clusters, ['cn-main'])
        self.assertEqual(stats['cn-main']['total'], 2)

    def test_archived_retired_cn_input_stays_visible_without_new_cluster_link(self):
        retired = ['cn-prod', 'cn-dev', 'aws-741924744516-cn-prod',
                   'aws-589899215075-cn-tech-service', 'tencent-100050722703-cn-staging']
        output = self.render_input([self.record(cluster, 'archived-app') for cluster in retired])
        for cluster in retired:
            self.assertIn(f'data-cluster="{cluster}"', output)
            self.assertIn(f'<td>{cluster}</td>', output)
            self.assertIsNone(report.app_url(cluster, 'archived-app'))
        self.assertNotIn('https://argocd-cn-dev.addx.live', output)
        self.assertNotIn('https://argocd-cn.addx.live', output)

    def test_ambiguous_historical_short_names_never_link_to_new_account(self):
        for cluster in ['cn-staging', 'cn-tech-service']:
            with self.subTest(cluster=cluster):
                output = self.render_input([self.record(cluster, 'archived-short-app')])
                self.assertIn(f'data-cluster="{cluster}"', output)
                self.assertIsNone(report.app_url(cluster, 'archived-short-app'))
                self.assertNotIn('/applications/argo-cd/archived-short-app?view=tree', output)

    def test_unreachable_new_account_alias_is_not_reported_healthy(self):
        output = self.render_input([], ['tencent-100052802231-cn-staging'])
        self.assertIn('tencent-100052802231-cn-staging <span class="unreach-pill">unreachable', output.replace('</a>', ''))
        self.assertNotIn('0 扫通', output)
        self.assertNotIn('cn-dev', output)


if __name__ == '__main__':
    unittest.main()
