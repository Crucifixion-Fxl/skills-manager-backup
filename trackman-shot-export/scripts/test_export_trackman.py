"""Offline behavioral checks; no third-party dependencies or network access."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from export_trackman import export, split_reports, RESULT_COLUMNS, POINT_COLUMNS


def fixture():
    return {'Id': 'report', 'StrokeGroups': [
        {'Id': 'shared-group', 'Club': '7Iron', 'Strokes': [
            {'Id': 's1', 'Measurement': {'Carry': 0, 'ReducedAccuracy': ['Carry'],
             'BallTrajectory': [{'X': 0.0, 'Y': 0.0, 'Z': -1.2, 'extra': 7}], 'ClubTrajectory': []}},
            {'Id': 's2', 'Measurement': {'BallTrajectory': None},
             'NormalizedMeasurement': {'Carry': 20, 'BallTrajectory': [{'X': 20, 'Y': 0, 'Z': 1}]}}
        ]},
        {'Id': 'shared-group', 'Club': 'Driver', 'Strokes': [{'Id': 's1'}]}
    ]}


class ExportTests(unittest.TestCase):
    def test_lossless_split_and_missing_data(self):
        original = fixture()
        snapshot = copy.deepcopy(original)
        results, trajectories, _, warnings = split_reports([(original, {'normalized_display_requested': True})])
        self.assertEqual(original, snapshot)
        self.assertEqual(len(results), 3)
        self.assertEqual(len({s['shot_key'] for s in results}), 3)
        self.assertTrue(warnings)
        source_strokes = [s for g in original['StrokeGroups'] for s in g['Strokes']]
        for src, res, traj in zip(source_strokes, results, trajectories):
            self.assertEqual(res['shot_key'], traj['shot_key'])
            reconstructed = dict(res['stroke_metadata'])
            for variant in ('Measurement', 'NormalizedMeasurement'):
                if variant in src:
                    reconstructed[variant] = dict(res['measurements'][variant], **traj['measurements'][variant])
            self.assertEqual(src, reconstructed)
        self.assertNotIn('Carry', results[1]['measurements']['Measurement'])
        self.assertIsNone(trajectories[2]['measurements']['Measurement'])

    def test_duplicate_within_group_rejected(self):
        src = fixture()
        src['StrokeGroups'][0]['Strokes'].append(src['StrokeGroups'][0]['Strokes'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            split_reports([(src, {})])

    def test_pair_written_and_overwrite_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export'
            export([(fixture(), {})], path, file_format='json')
            self.assertEqual({p.name for p in path.iterdir()}, {'shot_results.json', 'shot_trajectories.json'})
            a, b = [json.loads((path / name).read_text(encoding='utf-8')) for name in
                    ('shot_results.json', 'shot_trajectories.json')]
            self.assertEqual(a['export_id'], b['export_id'])
            self.assertEqual(a['ball_point_count'], 2)
            before = (path / 'shot_results.json').read_bytes()
            with self.assertRaises(FileExistsError):
                export([(fixture(), {})], path, file_format='json')
            self.assertEqual(before, (path / 'shot_results.json').read_bytes())

    def test_bad_second_report_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export'
            with self.assertRaises(ValueError):
                export([(fixture(), {}), ({'error': 'not a report'}, {})], path)
            self.assertFalse(path.exists())

    def test_invalid_points_rejected(self):
        for point in [{'X': 1, 'Y': 2}, {'X': 1, 'Y': 2, 'Z': float('nan')}]:
            src = fixture()
            src['StrokeGroups'][0]['Strokes'][0]['Measurement']['BallTrajectory'] = [point]
            with self.assertRaisesRegex(ValueError, 'invalid XYZ'):
                split_reports([(src, {})])

    def test_csv_reference_layout_points_and_placeholders(self):
        src = fixture()
        src['StrokeGroups'][0]['Player'] = {'Name': '测试, "A"\nB'}
        src['StrokeGroups'][0]['Strokes'][0]['Measurement']['BallSpeed'] = 0
        src['StrokeGroups'][1]['Strokes'][0]['Measurement'] = {'Id': 's1'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'csv'
            summary = export([(src, {})], path, prefix='TrackMan_test')
            self.assertEqual(len(list(path.iterdir())), 2)
            self.assertEqual(summary['format'], 'csv')
            with (path / summary['files'][0]).open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames[:len(RESULT_COLUMNS)], RESULT_COLUMNS)
                rows = list(reader)
            with (path / summary['files'][1]).open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames[:len(POINT_COLUMNS)], POINT_COLUMNS)
                points = list(reader)
            self.assertEqual(rows[0]['Player_Name'], '测试, "A"\nB')
            self.assertEqual(rows[0]['Raw_BallSpeed'], '0')
            self.assertEqual(rows[0]['HasBallSpeed'], 'True')
            self.assertEqual(rows[0]['Raw_Carry'], '0')
            self.assertEqual(rows[1]['Raw_Carry'], '')
            self.assertEqual(rows[2]['IsPlaceholderRecord'], 'True')
            self.assertEqual(rows[2]['HasMeasurementData'], 'False')
            self.assertEqual(rows[2]['Raw_Id'], 's1')
            self.assertEqual(rows[0]['Normalized_BallTrajectory'], '')
            self.assertEqual(rows[0]['HasNormalizedMeasurementData'], 'False')
            self.assertEqual([p['DataSpace'] for p in points], ['raw', 'normalized'])
            self.assertEqual([p['PointIndex'] for p in points], ['0', '0'])
            self.assertEqual(points[0]['Point_extra'], '7')
            self.assertEqual(points[0]['ShotKey'], rows[0]['ShotKey'])
            self.assertEqual(points[1]['ShotKey'], rows[1]['ShotKey'])
            self.assertTrue((path / summary['files'][0]).read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(json.loads(rows[0]['Raw_BallTrajectory'])[0]['Z'], float(points[0]['Z']))

    def test_csv_no_trajectory_is_header_only(self):
        src = {'StrokeGroups': [{'Id': 'group', 'Strokes': [{'Id': 'placeholder'}]}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'empty-points'
            summary = export([(src, {})], path)
            with (path / summary['files'][1]).open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, POINT_COLUMNS)
                self.assertEqual(list(reader), [])

    def test_csv_prefix_cannot_escape_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'output'
            with self.assertRaises(ValueError):
                export([(fixture(), {})], path, prefix='../escape')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
