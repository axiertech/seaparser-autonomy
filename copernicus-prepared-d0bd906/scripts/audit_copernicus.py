"""Read downloaded NetCDFs offline; audit CF fields, native grids and sample gaps.

Uses xarray/numpy from the Toolbox environment only, not the routing runtime.
No file data is extrapolated or turned into a ForecastDataset here.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from infrastructure.copernicus_fields import CURRENT_FIELDS, MASK_FIELDS, WAVE_FIELDS, current_node, validate_fields, wave_node


def coordinate(dataset, standard):
    names = [n for n, a in dataset.coords.items() if a.attrs.get('standard_name') == standard]
    if len(names) != 1:
        raise ValueError(f'Unambiguous CF coordinate required: {standard}')
    result = dataset[names[0]]
    if result.ndim != 1 or result.dims != (names[0],):
        raise ValueError('Only native rectilinear coordinates supported for this audit')
    return result


def inspect_file(path, fields, np, xr):
    if path.stat().st_size > 10_000_000:
        raise ValueError('NetCDF size budget exceeded')
    with xr.open_dataset(path, decode_cf=True, mask_and_scale=True) as ds:
        if any(v.size > 100_000 for v in ds.variables.values()):
            raise ValueError('Decoded array budget exceeded')
        validate_fields({n: a.attrs for n, a in ds.data_vars.items()}, fields)
        lon, lat = coordinate(ds, 'longitude'), coordinate(ds, 'latitude')
        for axis, units, bounds in ((lon, 'degrees_east', (11.58, 11.63)),
                                    (lat, 'degrees_north', (57.62, 57.65))):
            if (axis.attrs.get('units') != units or len(axis) < 2
                    or not np.isfinite(axis.values).all() or not (np.diff(axis.values) > 0).all()
                    or float(axis.min()) < bounds[0] or float(axis.max()) > bounds[1]):
                raise ValueError('Insufficient/unsupported native geographic axis')
        dimensions = [lat.name, lon.name]
        result = {'longitude': lon.values.tolist(), 'latitude': lat.values.tolist(),
                  'variables': {}, 'issued_at': None}
        if fields != MASK_FIELDS:
            time = coordinate(ds, 'time')
            times = time.values.astype('datetime64[s]')
            if (time.encoding.get('calendar', 'standard') not in ('standard', 'gregorian', 'proleptic_gregorian')
                    or np.isnat(times).any() or len(times) != 5
                    or not (np.diff(times) == np.timedelta64(1, 'h')).all()
                    or times[0] != np.datetime64('2026-10-13T12:00:00')
                    or times[-1] != np.datetime64('2026-10-13T16:00:00')):
                raise ValueError('Requested exact five hourly validity steps not present')
            result['times'] = [str(t) + 'Z' for t in times]
            dimensions.insert(0, time.name)
            run = [a for a in ds.variables.values() if a.attrs.get('standard_name') == 'forecast_reference_time']
            if len(run) == 1 and run[0].size == 1:
                value = np.asarray(run[0].values).reshape(-1)[0]
                if np.issubdtype(type(value), np.datetime64) and not np.isnat(value):
                    result['issued_at'] = str(value.astype('datetime64[s]')) + 'Z'
        depth_name = None
        if fields == CURRENT_FIELDS:
            depth = coordinate(ds, 'depth')
            if (len(depth) != 1 or depth.attrs.get('units') != 'm'
                    or depth.attrs.get('positive') != 'down'
                    or not np.isfinite(depth.values).all() or float(depth.values[0]) < 0):
                raise ValueError('Explicit positive-down single surface depth required')
            depth_name = depth.name
            result['surface_depth_m'] = float(depth.values[0])
        arrays = {}
        for name in fields:
            variable = ds[name]
            if depth_name:
                variable = variable.isel({depth_name: 0})
            if set(variable.dims) != set(dimensions):
                raise ValueError('Unexpected variable geometry')
            array = variable.transpose(*dimensions).values
            arrays[name] = array
            valid = np.isfinite(array)
            result['variables'][name] = {
                'standard_name': variable.attrs['standard_name'], 'units': variable.attrs['units'],
                'shape': list(array.shape), 'missing_samples': int((~valid).sum()),
                'min': float(array[valid].min()) if valid.any() else None,
                'max': float(array[valid].max()) if valid.any() else None,
                'fill_value_declared': '_FillValue' in ds[name].encoding or 'missing_value' in ds[name].encoding}
        return result, arrays


def audit(directory):
    import numpy as np
    import xarray as xr
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('schema') != 1 or len(manifest.get('files', [])) != 3
            or {f['filename'] for f in manifest['files']} != {'waves.nc', 'current.nc', 'wave-mask.nc'}):
        raise ValueError('Complete three-file download manifest required')
    entries = {f['filename']: f for f in manifest['files']}
    for filename, entry in entries.items():
        if sha256((directory / filename).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Downloaded file hash mismatch')
    waves, wa = inspect_file(directory / 'waves.nc', WAVE_FIELDS, np, xr)
    current, ca = inspect_file(directory / 'current.nc', CURRENT_FIELDS, np, xr)
    mask, ma = inspect_file(directory / 'wave-mask.nc', MASK_FIELDS, np, xr)
    if waves['longitude'] != mask['longitude'] or waves['latitude'] != mask['latitude']:
        raise ValueError('Static wet mask is not on the exact native wave grid')
    if waves['times'] != current['times']:
        raise ValueError('Wave/current validity differs')

    def value(v):
        return float(v) if np.isfinite(v) else None

    wave_gaps, current_gaps = 0, 0
    for index in np.ndindex(wa['VHM0'].shape):
        node = wave_node(*(value(wa[n][index]) for n in WAVE_FIELDS), value(ma['mask'][index[-2:]]))
        wave_gaps += node is None
    for index in np.ndindex(ca['uo'].shape):
        current_gaps += current_node(value(ca['uo'][index]), value(ca['vo'][index])) is None
    blockers = ['regional_wind_forecast_missing', 'native_grid_to_local_routing_geometry_not_verified',
                'current_native_wet_mask_not_verified', 'cross_product_model_run_compatibility_not_verified']
    for name, data in (('waves', waves), ('current', current)):
        if data['issued_at'] is None:
            blockers.append(f'{name}_model_issue_time_missing')
    if wave_gaps or current_gaps:
        blockers.append('masked_or_missing_samples_require_routing_support_checks')
    return {'schema': 1, 'audited_at': datetime.now(timezone.utc).isoformat(),
            'status': 'regional_routing_unavailable', 'adapter_sample_checks': 'passed',
            'waves': waves, 'current': current, 'wave_mask': mask,
            'normalized_wave_gaps': wave_gaps, 'normalized_current_gaps': current_gaps,
            'manifest_file_hashes': {n: f['sha256'] for n, f in entries.items()}, 'blockers': blockers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    report = audit(args.directory)
    (args.directory / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print('ADAPTER_SAMPLE_CHECKS=passed REGIONAL_ROUTING=unavailable; see audit.json')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'AUDIT_FAILED {type(error).__name__}; no regional routing enabled', file=sys.stderr)
        sys.exit(1)
