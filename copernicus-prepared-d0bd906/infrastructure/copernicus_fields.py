"""Offline CF field boundary for a small marine import audit, not a routing grid.

Native geometry, model-run identity and masks must be audited before building
GridProduct. These component conversions never invent spatial coverage.
"""
from math import cos, isfinite, radians, sin


WAVE_FIELDS = {
    'VHM0': ('sea_surface_wave_significant_height', ('m',)),
    'VTM02': ('sea_surface_wave_mean_period_from_variance_spectral_density_second_frequency_moment', ('s',)),
    'VMDR': ('sea_surface_wave_from_direction', ('degree', 'degrees', 'degree_true')),
}
CURRENT_FIELDS = {
    'uo': ('eastward_sea_water_velocity', ('m s-1', 'm/s')),
    'vo': ('northward_sea_water_velocity', ('m s-1', 'm/s')),
}
MASK_FIELDS = {'mask': ('sea_binary_mask', ('1',))}


def validate_fields(variables, expected):
    """Check names AND CF semantics; a peak period cannot substitute for Tm02."""
    for name, (standard, units) in expected.items():
        attrs = variables.get(name, {})
        if attrs.get('standard_name') != standard or attrs.get('units') not in units:
            raise ValueError(f'Unsupported or missing CF field: {name}')


def wave_node(height, period, direction, wet):
    if wet not in (None, 0, 1) or isinstance(wet, bool):
        raise ValueError('Mask must explicitly use sea=1, land=0')
    if wet != 1 or any(v is None for v in (height, period, direction)):
        return None
    _finite((height, period, direction))
    if height < 0 or period <= 0 or not 0 <= direction <= 360:
        raise ValueError('Invalid wave sample')
    bearing = radians(direction % 360)
    return height, period, sin(bearing), cos(bearing)


def current_node(east, north):
    # CF eastward/northward ground-relative velocities already are TO vectors.
    if east is None or north is None:
        return None
    _finite((east, north))
    return east, north


def _finite(values):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
           for v in values):
        raise ValueError('Nonfinite samples must be decoded to explicit gaps')
