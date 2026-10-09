#!/usr/bin/env python3
"""Export TrackMan report results and supplied trajectory points. Python 3.10+, stdlib only."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

API = 'https://golf-player-activities.trackmangolf.com/api/reports/'
HOST = 'web-dynamic-reports.trackmangolf.com'
VARIANTS = ('Measurement', 'NormalizedMeasurement')

# Leading columns match the user-provided July 28 CSVs; additions are appended.
RESULT_COLUMNS = [
    'SourceReportId',
    'ReportId',
    'ManifestId',
    'ReportKind',
    'ReportTime',
    'ReportUpdated',
    'GroupId',
    'GroupDate',
    'GroupClub',
    'GroupBall',
    'Player_Id',
    'Player_Name',
    'Player_Email',
    'Player_Gender',
    'StrokeIndex',
    'StrokeId',
    'StrokeTime',
    'StrokeClub',
    'StrokeBall',
    'HasMeasurementData',
    'HasNormalizedMeasurementData',
    'IsPlaceholderRecord',
    'HasBallSpeed',
    'HasClubSpeed',
    'RawBallTrajectoryPointCount',
    'RawClubTrajectoryPointCount',
    'NormalizedBallTrajectoryPointCount',
    'NormalizedClubTrajectoryPointCount',
    'Client_Name',
    'Client_Version',
    'Environment_Altitude',
    'Environment_Temperature',
    'Groups_JSON',
    'MeasurementDetails',
    'MeasurementDetails_ClubClassification_ClassifiedClubType',
    'MeasurementDetails_ClubClassification_ClubTypes',
    'MeasurementDetails_ClubClassification_Confidences',
    'MeasurementDetails_ImpactLocation_CameraConfiguration_TiltPanRoll',
    'MeasurementDetails_ImpactLocation_CameraConfiguration_Translation',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_BallCompression',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_Bulge',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_HoselOffset',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_Roll',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_StaticLie',
    'MeasurementDetails_ImpactLocation_ClubConfiguration_StaticLoft',
    'MeasurementDetails_ImpactLocation_Measurements_Dexterity',
    'MeasurementDetails_ImpactLocation_Measurements_DynamicLie',
    'MeasurementDetails_ImpactLocation_Measurements_DynamicLoft',
    'MeasurementDetails_ImpactLocation_Measurements_FaceAngle',
    'MeasurementDetails_ImpactLocation_Measurements_HoselAngle',
    'MeasurementDetails_ImpactLocation_Measurements_HoselPoint',
    'MeasurementDetails_ImpactLocation_Measurements_ImpactHeight',
    'MeasurementDetails_ImpactLocation_Measurements_ImpactOffset',
    'MeasurementDetails_ImpactLocation_Measurements_TeePosition',
    'Normalized_AttackAngle',
    'Normalized_BallSpeed',
    'Normalized_BallSpeedDifference',
    'Normalized_BallTrajectory',
    'Normalized_Carry',
    'Normalized_CarrySide',
    'Normalized_ClubPath',
    'Normalized_ClubSpeed',
    'Normalized_ClubTrajectory',
    'Normalized_Curve',
    'Normalized_DPlaneTilt',
    'Normalized_DistanceFromPin',
    'Normalized_DynamicLie',
    'Normalized_DynamicLoft',
    'Normalized_FaceAngle',
    'Normalized_FaceToPath',
    'Normalized_HangTime',
    'Normalized_Id',
    'Normalized_ImpactHeight',
    'Normalized_ImpactOffset',
    'Normalized_InvalidNoTarget',
    'Normalized_Kind',
    'Normalized_LandingAngle',
    'Normalized_LastData',
    'Normalized_LaunchAngle',
    'Normalized_LaunchDirection',
    'Normalized_LengthToTarget',
    'Normalized_LowPointDistance',
    'Normalized_LowPointHeight',
    'Normalized_LowPointSide',
    'Normalized_MaxHeight',
    'Normalized_PlayerDexterity',
    'Normalized_Score',
    'Normalized_SmashFactor',
    'Normalized_SmashIndex',
    'Normalized_SpinAxis',
    'Normalized_SpinIndex',
    'Normalized_SpinLoft',
    'Normalized_SpinRate',
    'Normalized_SpinRateDifference',
    'Normalized_SwingDirection',
    'Normalized_SwingPlane',
    'Normalized_SwingRadius',
    'Normalized_TeePosition',
    'Normalized_Time',
    'Normalized_Total',
    'Normalized_TotalSide',
    'Raw_AttackAngle',
    'Raw_BallSpeed',
    'Raw_BallSpeedDifference',
    'Raw_BallTrajectory',
    'Raw_Carry',
    'Raw_CarrySide',
    'Raw_ClubPath',
    'Raw_ClubSpeed',
    'Raw_ClubTrajectory',
    'Raw_Curve',
    'Raw_DPlaneTilt',
    'Raw_DynamicLie',
    'Raw_DynamicLoft',
    'Raw_FaceAngle',
    'Raw_FaceToPath',
    'Raw_HangTime',
    'Raw_Id',
    'Raw_ImpactHeight',
    'Raw_ImpactOffset',
    'Raw_InvalidNoTarget',
    'Raw_Kind',
    'Raw_LandingAngle',
    'Raw_LastData',
    'Raw_LaunchAngle',
    'Raw_LaunchDirection',
    'Raw_LowPointDistance',
    'Raw_LowPointHeight',
    'Raw_LowPointSide',
    'Raw_MaxHeight',
    'Raw_PlayerDexterity',
    'Raw_ReducedAccuracy',
    'Raw_SmashFactor',
    'Raw_SmashIndex',
    'Raw_SpinAxis',
    'Raw_SpinIndex',
    'Raw_SpinLoft',
    'Raw_SpinRate',
    'Raw_SpinRateDifference',
    'Raw_SwingDirection',
    'Raw_SwingPlane',
    'Raw_SwingRadius',
    'Raw_TeePosition',
    'Raw_Time',
    'Raw_Total',
    'Raw_TotalSide',
    'ReportUser_Email',
    'ReportUser_Id',
    'ReportUser_Name',
    'Schema_JSON',
    'Settings_JSON',
    'Sponsors_JSON',
    'Videos_JSON',
]

POINT_COLUMNS = [
    'GroupId',
    'GroupDate',
    'GroupClub',
    'StrokeIndex',
    'StrokeId',
    'StrokeTime',
    'DataSpace',
    'PointIndex',
    'X',
    'Y',
    'Z',
]



def read_url(url, timeout, payload=None):
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
    req = Request(url, data=data, headers={'User-Agent': 'TrackManShotExport/1.0',
                                         'Content-Type': 'application/json'})
    with urlopen(req, timeout=timeout) as response:
        return response.geturl(), response.read()


def load_source(source, timeout):
    if not source.startswith(('https://', 'http://')):
        raw = Path(source).read_bytes()
        return json.loads(raw), {'source': str(Path(source).resolve()),
                                 'response_sha256': hashlib.sha256(raw).hexdigest()}
    parsed = urlparse(source)
    if parsed.scheme != 'https' or parsed.hostname not in ('tm-short.me', HOST):
        raise ValueError('Expected HTTPS tm-short.me or TrackMan dynamic report URL')
    resolved, _ = read_url(source, timeout)
    parsed = urlparse(resolved)
    if parsed.scheme != 'https' or parsed.hostname != HOST:
        raise ValueError('Share link did not resolve to the supported TrackMan report site')
    query = parse_qs(parsed.query)
    # These URL keys and request fields are used by the official report frontend.
    if len(query.get('a', [])) == 1:
        identifier, field, route = query['a'][0], 'ActivityId', 'getactivityreport'
    elif len(query.get('r', [])) == 1:
        identifier, field, route = query['r'][0], 'ReportId', 'getreport'
    else:
        raise ValueError('Expected one activity a= or report r= identifier')
    UUID(identifier)
    payload = {field: identifier}
    for key, outkey in [('nd_altitude', 'Altitude'), ('nd_temperature', 'Temperature')]:
        if key in query:
            value = float(query[key][0])
            if not math.isfinite(value):
                raise ValueError(f'Invalid {key}')
            unit = query.get(key + 'Unit', ['Meters' if key == 'nd_altitude' else 'Celsius'])[0]
            if key == 'nd_altitude':
                if unit not in ('Meters', 'Feet'):
                    raise ValueError(f'Unknown altitude unit: {unit}')
                value = value * 0.3048 if unit == 'Feet' else value
            else:
                if unit not in ('Celsius', 'Fahrenheit'):
                    raise ValueError(f'Unknown temperature unit: {unit}')
                value = (value - 32) * 5 / 9 if unit == 'Fahrenheit' else value
            payload[outkey] = value
    if 'nd_ballType' in query:
        payload['BallType'] = query['nd_ballType'][0]
    _, raw = read_url(API + route, timeout, payload)
    return json.loads(raw), {'source': source, 'resolved_url': resolved,
                             'request': payload, 'api_url': API + route,
                             'normalized_display_requested': query.get('nd', ['false'])[0] == 'true',
                             'response_sha256': hashlib.sha256(raw).hexdigest()}


def split_reports(sources):
    results, trajectories, provenance, warnings = [], [], [], []
    seen = set()
    for report_index, (report, source_meta) in enumerate(sources, 1):
        if not isinstance(report, dict) or not isinstance(report.get('StrokeGroups'), list):
            raise ValueError('Unsupported response: expected object with StrokeGroups array')
        meta = dict(source_meta, report_index=report_index,
                    report_metadata={k: report[k] for k in ('Id', 'Kind', 'Time', 'Settings', 'Environment', 'Client') if k in report},
                    groups=[])
        report_count = normalized_count = 0
        for group_index, group in enumerate(report['StrokeGroups'], 1):
            if not isinstance(group, dict) or not isinstance(group.get('Strokes'), list):
                raise ValueError('Unsupported group: expected Strokes array')
            meta['groups'].append({'group_index': group_index, 'shot_count': len(group['Strokes']),
                                   **{k: group[k] for k in ('Id', 'Club', 'Ball', 'Date', 'Title') if k in group}})
            for shot_index, stroke in enumerate(group['Strokes'], 1):
                if not isinstance(stroke, dict) or not stroke.get('Id'):
                    raise ValueError('Missing stroke Id; refusing to invent identity')
                # Group Id repeats across clubs in real reports. Ordinals disambiguate occurrences.
                shot_key = f'{report_index}:{group_index}:{stroke["Id"]}'
                if shot_key in seen:
                    raise ValueError(f'Duplicate stroke within group: {shot_key}')
                seen.add(shot_key)
                identity = dict(shot_key=shot_key, report_index=report_index,
                                report_id=report.get('Id'), group_index=group_index,
                                group_id=group.get('Id'), shot_index=shot_index,
                                shot_id=stroke['Id'], time=stroke.get('Time'),
                                club=stroke.get('Club', group.get('Club')))
                unexpected = [k for k, v in stroke.items() if k not in VARIANTS and
                              isinstance(v, dict) and any('Trajectory' in x for x in v)]
                if unexpected:
                    raise ValueError(f'Unsupported measurement variant: {unexpected}')
                result = dict(identity, stroke_metadata={k: v for k, v in stroke.items() if k not in VARIANTS},
                              measurements={}, trajectory_counts={})
                trajectory = dict(identity, measurements={})
                for variant in VARIANTS:
                    measurement = stroke.get(variant)
                    if measurement is None:
                        result['measurements'][variant] = None
                        trajectory['measurements'][variant] = None
                        continue
                    if not isinstance(measurement, dict):
                        raise ValueError(f'{shot_key}: {variant} is not an object')
                    if variant == 'NormalizedMeasurement':
                        normalized_count += 1
                    fields, series, counts = {}, {}, {}
                    for name, value in measurement.items():
                        if 'Trajectory' not in name:
                            fields[name] = value
                            continue
                        if value is not None and not isinstance(value, list):
                            raise ValueError(f'{shot_key}: unsupported trajectory structure {name}')
                        if value:
                            for point in value:
                                if not isinstance(point, dict) or not all(
                                    isinstance(point.get(axis), (float, int)) and
                                    not isinstance(point[axis], bool) and math.isfinite(point[axis])
                                    for axis in ('X', 'Y', 'Z')
                                ):
                                    raise ValueError(f'{shot_key}: invalid XYZ point in {name}')
                        # Copy exact point dictionaries, including any future time/quality fields.
                        series[name] = value
                        counts[name] = None if value is None else len(value)
                    result['measurements'][variant] = fields
                    result['trajectory_counts'][variant] = counts
                    trajectory['measurements'][variant] = series
                results.append(result)
                trajectories.append(trajectory)
                report_count += 1
        if report_count == 0:
            raise ValueError(f'Report {report_index} has no strokes')
        meta.update(shot_count=report_count, normalized_shot_count=normalized_count)
        if source_meta.get('normalized_display_requested') and normalized_count < report_count:
            warnings.append(f'Report {report_index}: URL requests normalized display, but only '
                            f'{normalized_count}/{report_count} strokes contain NormalizedMeasurement. '
                            'No normalized values were synthesized or substituted.')
        provenance.append(meta)
    if not results:
        raise ValueError('No strokes to export')
    return results, trajectories, provenance, warnings


def csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite CSV value')
    return str(value)


def flatten(value, prefix, target):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            flatten(child, f'{prefix}_{key}' if prefix else key, target)
    else:
        if prefix in target:
            raise ValueError(f'Flattened column collision: {prefix}')
        target[prefix] = value


def has_measurement_data(measurement):
    # The reference contains placeholder measurements with only an Id.
    return bool(measurement) and any(
        key not in ('Id', 'Time', 'Kind') and value is not None and value != [] and value != {}
        for key, value in measurement.items()
    )


def build_csv_tables(sources, export_id):
    result_rows, point_rows = [], []
    for report_index, (report, source) in enumerate(sources, 1):
        request = source.get('request', {})
        source_id = request.get('ActivityId', request.get('ReportId', report.get('Id')))
        for group_index, group in enumerate(report['StrokeGroups'], 1):
            for stroke_index, stroke in enumerate(group['Strokes'], 1):
                row = dict(SourceReportId=source_id, ReportId=report.get('Id'),
                           ManifestId=report.get('ManifestId'), ReportKind=report.get('Kind'),
                           ReportTime=report.get('Time'), ReportUpdated=report.get('Updated'),
                           GroupId=group.get('Id'), GroupDate=group.get('Date'),
                           GroupClub=group.get('Club'), GroupBall=group.get('Ball'),
                           StrokeIndex=stroke_index, StrokeId=stroke['Id'],
                           StrokeTime=stroke.get('Time'), StrokeClub=stroke.get('Club'),
                           StrokeBall=stroke.get('Ball'))
                raw, normalized = stroke.get('Measurement') or {}, stroke.get('NormalizedMeasurement') or {}
                has_raw, has_normalized = has_measurement_data(raw), has_measurement_data(normalized)
                row.update(HasMeasurementData=has_raw, HasNormalizedMeasurementData=has_normalized,
                           IsPlaceholderRecord=not (has_raw or has_normalized),
                           HasBallSpeed=any(m.get('BallSpeed') is not None for m in (raw, normalized)),
                           HasClubSpeed=any(m.get('ClubSpeed') is not None for m in (raw, normalized)))
                for prefix, measurement in [('Raw', raw), ('Normalized', normalized)]:
                    for kind in ('Ball', 'Club'):
                        row[f'{prefix}{kind}TrajectoryPointCount'] = len(measurement.get(f'{kind}Trajectory') or [])
                    for name, value in measurement.items():
                        flatten(value, f'{prefix}_{name}', row)
                for obj, prefix in [(group.get('Player'), 'Player'), (report.get('User'), 'ReportUser'),
                                    (report.get('Client'), 'Client'), (report.get('Environment'), 'Environment')]:
                    if obj is not None:
                        flatten(obj, prefix, row)
                for name, value in stroke.items():
                    if name in ('Id', 'Time', 'Club', 'Ball', *VARIANTS):
                        continue
                    if name == 'Videos':
                        row['Videos_JSON'] = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                    else:
                        flatten(value, name, row)
                for name in ('Groups', 'Settings', 'Sponsors'):
                    if name in report:
                        row[name + '_JSON'] = json.dumps(report[name], ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                for name in ('$schema', 'Schema'):
                    if name in report:
                        row['Schema_JSON'] = json.dumps(report[name], ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                        break
                # Preserve fields beyond the reference, without changing its leading columns.
                for name, value in group.items():
                    if name not in ('Id', 'Date', 'Club', 'Ball', 'Player', 'Strokes'):
                        flatten(value, 'Group_' + name, row)
                for name, value in report.items():
                    if name not in ('Id', 'ManifestId', 'Kind', 'Time', 'Updated', 'StrokeGroups',
                                    'User', 'Client', 'Environment', 'Groups', 'Settings', 'Sponsors', '$schema', 'Schema'):
                        flatten(value, 'Report_' + name, row)
                linkage = dict(ExportId=export_id, ReportIndex=report_index, GroupIndex=group_index,
                               ShotKey=f'{report_index}:{group_index}:{stroke["Id"]}')
                row.update(linkage, SourceURL=source.get('source'), ResponseSHA256=source.get('response_sha256'),
                           NormalizedDisplayRequested=source.get('normalized_display_requested'),
                           Request_JSON=json.dumps(request, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
                result_rows.append(row)
                for space, measurement in [('raw', raw), ('normalized', normalized)]:
                    for point_index, point in enumerate(measurement.get('BallTrajectory') or []):
                        point_row = {k: row[k] for k in ('GroupId', 'GroupDate', 'GroupClub', 'StrokeIndex', 'StrokeId', 'StrokeTime')}
                        point_row.update(DataSpace=space, PointIndex=point_index,
                                         X=point['X'], Y=point['Y'], Z=point['Z'])
                        point_row.update(SourceReportId=source_id, ReportId=report.get('Id'), **linkage)
                        for name, value in point.items():
                            if name not in ('X', 'Y', 'Z'):
                                flatten(value, 'Point_' + name, point_row)
                        point_rows.append(point_row)
    return result_rows, point_rows


def write_csv(path, base_columns, rows):
    columns = base_columns + sorted(set().union(*(set(row) for row in rows)) - set(base_columns))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\r\n')
        writer.writeheader()
        writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in rows)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError('CSV header verification failed')
        saved = list(reader)
        expected = [{key: csv_cell(row.get(key)) for key in columns} for row in rows]
        if saved != expected:
            raise ValueError('CSV round-trip verification failed')


def export(sources, output, file_format='csv', prefix=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Output directory already exists; choose a new directory: {output}')
    results, trajectories, provenance, warnings = split_reports(sources)
    if file_format not in ('csv', 'json'):
        raise ValueError('Unsupported output format')
    run_id = str(uuid4())
    ball_points = sum(len(m.get('BallTrajectory') or []) for s in trajectories
                      for m in s['measurements'].values() if m is not None)
    common = dict(schema_version='trackman-shot-export.v1', export_id=run_id,
                  exported_at_utc=datetime.now(timezone.utc).isoformat(), sources=provenance,
                  shot_count=len(results), ball_point_count=ball_points, warnings=warnings,
                  value_convention='Unconverted API values; no display-unit conversion. See skill schema reference.',
                  trajectory_convention='API XYZ and array order preserved; no resampling, time inference or coordinate transform.')
    if prefix is None:
        dates = {str(g.get('Date') or 'undated') for report, _ in sources for g in report['StrokeGroups']}
        prefix = 'TrackMan_' + (next(iter(dates)) if len(dates) == 1 else 'multiple_dates')
    if not prefix or any(c in prefix for c in '<>:"/\\|?*') or prefix in ('.', '..') or prefix[-1] in '. ':
        raise ValueError('Prefix must be a filename component, not a path')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.trackman-export-', dir=output.parent))
    try:
        if file_format == 'csv':
            rows, points = build_csv_tables(sources, run_id)
            if len(rows) != len(results) or len(points) != ball_points:
                raise ValueError('CSV shot/point count does not match validated input')
            filenames = [prefix + '_all_swings.csv', prefix + '_ball_trajectory.csv']
            write_csv(staging / filenames[0], RESULT_COLUMNS, rows)
            write_csv(staging / filenames[1], POINT_COLUMNS, points)
        else:
            filenames = ['shot_results.json', 'shot_trajectories.json']
            for filename, kind, rows in [('shot_results.json', 'shot_results', results),
                                         ('shot_trajectories.json', 'shot_trajectories', trajectories)]:
                doc = dict(common, file_kind=kind, shots=rows)
                path = staging / filename
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
                if json.loads(path.read_text(encoding='utf-8')) != doc:
                    raise ValueError('Written JSON failed round-trip validation')
        # Publish the pair only after all downloads, schema checks and writes succeed.
        if output.exists():
            raise FileExistsError(f'Output appeared during export: {output}')
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return dict(output=str(output.resolve()), export_id=run_id, shot_count=len(results),
                ball_point_count=ball_points, format=file_format, files=filenames,
                reports=[{k: p[k] for k in ('report_index', 'shot_count', 'normalized_shot_count')} for p in provenance],
                warnings=warnings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='+', help='Share URLs, resolved report URLs, or raw report JSON paths')
    parser.add_argument('--out-dir', required=True, help='New directory; never overwrite an existing export')
    parser.add_argument('--timeout', type=float, default=60, help='Per-request network timeout in seconds')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv', help='Default: two reference-compatible CSVs')
    parser.add_argument('--prefix', help='CSV filename prefix; default TrackMan_<group-date> or TrackMan_multiple_dates')
    args = parser.parse_args()
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('--timeout must be finite and positive')
        if Path(args.out_dir).exists():
            raise FileExistsError('Output directory already exists; choose a new directory')
        sources = [load_source(source, args.timeout) for source in args.sources]
        print(json.dumps(export(sources, args.out_dir, args.format, args.prefix), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
