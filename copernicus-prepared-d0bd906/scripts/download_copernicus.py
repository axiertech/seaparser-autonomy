"""Bounded Vinga fetch using existing Toolbox credentials; no routing or DB.

Run via download-copernicus.sh for a wall-clock bound and private exit log.
The only optional runtime dependency is the already installed Toolbox 2.5.0.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import socket
from math import isfinite
from pathlib import Path
import sys

from infrastructure.copernicus_fields import CURRENT_FIELDS, MASK_FIELDS, WAVE_FIELDS, validate_fields


REQUESTS = (
    ('waves', 'cmems_mod_bal_wav_anfc_PT1H-i', WAVE_FIELDS),
    ('current', 'cmems_mod_bal_phy_anfc_PT1H-i', CURRENT_FIELDS),
    ('wave-mask', 'cmems_mod_bal_wav_anfc_static', MASK_FIELDS),
)
START, END = '2026-10-13T12:00:00Z', '2026-10-13T16:00:00Z'
BBOX = dict(minimum_longitude=11.58, maximum_longitude=11.63,
            minimum_latitude=57.62, maximum_latitude=57.65)


def selection(catalogue, dataset_id, fields):
    """Pin one catalogue version/part/service; reject uncertain field schemas."""
    datasets = [d for p in catalogue.products for d in p.datasets if d.dataset_id == dataset_id]
    if len(datasets) != 1 or not datasets[0].versions:
        raise ValueError('Requested dataset/version not uniquely available')
    version = datasets[0].versions[0]
    matches = []
    for part in version.parts:
        if part.retired_date:
            continue
        for service in part.services:
            if service.service_name not in ('arco-geo-series', 'static-arco'):
                continue
            variables = {v.short_name: {'standard_name': v.standard_name, 'units': v.units}
                         for v in service.variables}
            try:
                validate_fields(variables, fields)
            except ValueError:
                continue
            matches.append((part, service))
    if len(matches) != 1:
        raise ValueError('Native field service/part ambiguous or unavailable')
    part, service = matches[0]
    kwargs = dict(dataset_id=dataset_id, dataset_version=version.label,
                  dataset_part=part.name, service=service.service_name, variables=list(fields))
    if fields == CURRENT_FIELDS:
        depths = [c for v in service.variables if v.short_name in fields
                  for c in v.coordinates if c.coordinate_id == 'depth']
        if len(depths) != 2 or any(c.coordinate_unit != 'm' for c in depths):
            raise ValueError('Surface depth coordinate unavailable')
        shallow = [float(c.minimum_value) for c in depths]
        if shallow[0] != shallow[1] or not isfinite(shallow[0]) or shallow[0] < 0:
            raise ValueError('Unverified surface depth')
        kwargs.update(minimum_depth=shallow[0], maximum_depth=shallow[0], vertical_axis='depth')
    return kwargs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Prevent client exception logs or signed URLs from entering the audit log.
    logging.disable(logging.CRITICAL)
    import copernicusmarine as marine
    if marine.__version__ != '2.5.0':
        raise ValueError('This request was reviewed against Toolbox 2.5.0')
    socket.getaddrinfo('stac.marine.copernicus.eu', 443)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {'schema': 1, 'toolbox_version': marine.__version__, 'bbox': BBOX,
                'start': START, 'end': END, 'files': [],
                'attribution': 'Generated using E.U. Copernicus Marine Service Information; DOI: 10.48670/moi-00010, 10.48670/moi-00011'}
    for label, dataset_id, fields in REQUESTS:
        catalogue = marine.describe(dataset_id=dataset_id, disable_progress_bar=True, raise_on_error=True)
        request = selection(catalogue, dataset_id, fields)
        request.update(BBOX, coordinates_selection_method='strict-inside',
                       output_directory=args.output, output_filename=f'{label}.nc',
                       file_format='netcdf', disable_progress_bar=True, raise_if_updating=True)
        if label != 'wave-mask':
            request.update(start_datetime=START, end_datetime=END)
        # No login, passwords, credential copying, or environment credentials.
        # Toolbox discovers the previously saved $HOME/.copernicusmarine credentials.
        estimate = marine.subset(**request, dry_run=True)
        sizes = (estimate.file_size, estimate.data_transfer_size)
        if any(v is None or not isfinite(v) or v < 0 for v in sizes) or sizes[0] > 10 or sizes[1] > 100:
            raise ValueError('Missing size estimate or bounded download budget exceeded')
        print(f'FETCH {label} dataset={dataset_id} version={request["dataset_version"]}', flush=True)
        marine.subset(**request)
        path = args.output / f'{label}.nc'
        if not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError('Missing or oversized NetCDF output')
        entry = {'product': label, 'dataset_id': dataset_id, 'version': request['dataset_version'],
                 'part': request['dataset_part'], 'service': request['service'],
                 'filename': path.name, 'sha256': sha256(path.read_bytes()).hexdigest(),
                 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                 'request': {k: v for k, v in request.items() if k != 'output_directory'}}
        manifest['files'].append(entry)
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('DOWNLOAD_COMPLETE; run scripts.audit_copernicus before any adapter integration')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Exception type is sufficient for diagnostics; never print auth-bearing messages.
        print(f'FETCH_FAILED {type(error).__name__}; no regional routing enabled', file=sys.stderr)
        sys.exit(1)
